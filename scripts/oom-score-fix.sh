#!/bin/bash

REDIS_PROCESS_NAME="redis-server"
TARGET_OOM_ADJ="-1000"
MONITOR_INTERVAL_SECONDS=5 # How often to check the score

echo "Monitoring for ${REDIS_PROCESS_NAME} process..."
echo "Will attempt to set its oom_score_adj to ${TARGET_OOM_ADJ} and monitor it."
echo "----------------------------------------------------------------------"
echo "NOTE: This script requires CAP_SYS_RESOURCE capability and to be run as root (UID 0) in the container's securityContext."
echo "Example securityContext for Kubernetes:"
echo "  securityContext:"
echo "    capabilities:"
echo "      add:"
echo "      - SYS_RESOURCE"
echo "    runAsUser: 0"
echo "    runAsGroup: 0"
echo "----------------------------------------------------------------------"

redis_pid=""

# Loop to find the redis-server process
while true; do
    # Find PID of redis-server. Use 'pgrep -o' for the oldest matching process,
    # or 'pgrep -n' for the newest. 'pgrep' is generally more reliable than 'ps aux | grep'.
    redis_pid=$(pgrep -o "${REDIS_PROCESS_NAME}" 2>/dev/null)

    if [[ -n "$redis_pid" ]]; then
        echo "Found ${REDIS_PROCESS_NAME} with PID: ${redis_pid}"

        # Attempt to set oom_score_adj
        current_oom_adj=$(cat "/proc/${redis_pid}/oom_score_adj" 2>/dev/null)
        if [[ "$current_oom_adj" != "$TARGET_OOM_ADJ" ]]; then
            echo "${TARGET_OOM_ADJ}" > "/proc/${redis_pid}/oom_score_adj" 2>/dev/null
            if [[ $? -eq 0 ]]; then
                echo "Successfully set oom_score_adj for PID ${redis_pid} to ${TARGET_OOM_ADJ}"
            else
                echo "Failed to set oom_score_adj for PID ${redis_pid}. Check permissions/capabilities."
            fi
        else
            echo "oom_score_adj for PID ${redis_pid} is already ${TARGET_OOM_ADJ}."
        fi

        # Start monitoring loop for the found PID
        while true; do
            # Check if the process is still running
            if ! kill -0 "$redis_pid" 2>/dev/null; then
                echo "${REDIS_PROCESS_NAME} (PID ${redis_pid}) is no longer running. Re-searching..."
                redis_pid="" # Reset PID to trigger outer loop to find it again
                break # Exit inner monitoring loop
            fi

            current_oom_adj=$(cat "/proc/${redis_pid}/oom_score_adj" 2>/dev/null)
            if [[ "$current_oom_adj" == "$TARGET_OOM_ADJ" ]]; then
                echo "$(date '+%Y-%m-%d %H:%M:%S') - PID ${redis_pid} (${REDIS_PROCESS_NAME}) oom_score_adj is ${TARGET_OOM_ADJ}. (OK)"
            else
                echo "$(date '+%Y-%m-%d %H:%M:%S') - WARNING: PID ${redis_pid} (${REDIS_PROCESS_NAME}) oom_score_adj changed to ${current_oom_adj}. Attempting to reset to ${TARGET_OOM_ADJ}..."
                echo "${TARGET_OOM_ADJ}" > "/proc/${redis_pid}/oom_score_adj" 2>/dev/null
                if [[ $? -eq 0 ]]; then
                    echo "Successfully reset oom_score_adj for PID ${redis_pid} to ${TARGET_OOM_ADJ}."
                else
                    echo "Failed to reset oom_score_adj for PID ${redis_pid}. Permissions issue or process terminated."
                fi
            fi
            sleep "$MONITOR_INTERVAL_SECONDS"
        done
    else
        echo "Waiting for ${REDIS_PROCESS_NAME} to start..."
        sleep "$MONITOR_INTERVAL_SECONDS"
    fi
done