#!/bin/bash
set -euo pipefail

# This script continuously monitors and resolves split-brain conditions in Sentinel
# It runs in a loop and is managed by supervisord

# Monitoring interval (seconds) - check every second
readonly MONITORING_INTERVAL=${SPLIT_BRAIN_MONITORING_INTERVAL:-1}

# Constants
readonly LINK="Please refer to the documentation for instructions on how to resolve this issue: https://github.com/FalkorDB/runbooks/blob/main/alerts/SentinelSplitBrainAlertsRunbook.md"
readonly MAX_STARTUP_RETRIES=300  # Wait up to 5 minutes (300 seconds) for services to be ready
readonly STARTUP_CHECK_INTERVAL=1

echo "Starting split-brain monitor with ${MONITORING_INTERVAL}s interval"

# Function to check if a Redis instance is responding
check_redis_connectivity() {
    local host=$1
    local port=$2
    local password=$3
    local ssl_flag=$4
    
    redis-cli --no-auth-warning -a "$password" ${ssl_flag} -h "$host" -p "$port" PING &>/dev/null
    return $?
}

# Function to wait for minimum required services to be ready
wait_for_services_ready() {
    local adminpass=$1
    local ssl_flag=$2
    local resource_key=$3
    
    echo "Waiting for minimum required services to be ready before starting monitoring..."
    echo "Required: sentinel-${resource_key}-0 and at least one node"
    
    local attempt=1
    local sentinel_ready=false
    local at_least_one_node_ready=false
    
    while [[ $attempt -le $MAX_STARTUP_RETRIES ]]; do
        # Check sentinel-sz/mz-0 (mandatory)
        if check_redis_connectivity "sentinel-${resource_key}-0" "$SENTINEL_PORT" "$adminpass" "$ssl_flag"; then
            sentinel_ready=true
        else
            echo "Waiting for sentinel-${resource_key}-0 to be ready (attempt $attempt/$MAX_STARTUP_RETRIES)..."
            sentinel_ready=false
        fi
        
        # Check if at least one node is ready (check all NUM_REPLICAS nodes)
        at_least_one_node_ready=false
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            if check_redis_connectivity "node-${resource_key}-$i" "$NODE_PORT" "$adminpass" "$ssl_flag"; then
                at_least_one_node_ready=true
                break
            fi
        done
        
        if [[ "$at_least_one_node_ready" == "false" ]]; then
            echo "Waiting for at least one node to be ready (attempt $attempt/$MAX_STARTUP_RETRIES)..."
        fi
        
        # If minimum required services are ready, break out of the loop
        if [[ "$sentinel_ready" == "true" && "$at_least_one_node_ready" == "true" ]]; then
            echo "Minimum required services are ready! Starting monitoring..."
            echo "Note: The monitor will handle temporarily unavailable nodes during operation."
            return 0
        fi
        
        sleep $STARTUP_CHECK_INTERVAL
        ((attempt++))
    done
    
    echo "ERROR: Timeout waiting for minimum required services after $MAX_STARTUP_RETRIES seconds" >&2
    echo "Required: sentinel-${resource_key}-0 (ready: $sentinel_ready) and at least one node (ready: $at_least_one_node_ready)" >&2
    return 1
}

# Function to get admin password
get_admin_password() {
    local adminpass=""
    
    if [[ -f /run/secrets/adminpassword ]]; then
        adminpass=$(cat /run/secrets/adminpassword)
    else
        # Try to get from Kubernetes API
        adminpass=$(curl -s \
            --cacert /var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
            -H "Authorization: Bearer $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" \
            https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT/api/v1/namespaces/$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace)/secrets \
            | grep '.*adminpassword.*"$' | awk 'NR==1{print $2}' | tr -d '"' | base64 -d)
    fi
    
    if [[ -z "$adminpass" ]]; then
        echo "ERROR: Failed to retrieve admin password from secret. Cannot proceed with Redis operations." >&2
        return 1
    fi
    
    echo "$adminpass"
}

# Main monitoring loop
main() {
    echo "Split-brain monitor starting on ${HOSTNAME}"
    
    local adminpass
    
    # Get admin password once at startup
    if ! adminpass=$(get_admin_password); then
        echo "ERROR: Cannot start monitoring without admin password" >&2
        exit 1
    fi
    
    # Derived configuration
    readonly SSL_FLAG=$([[ "${TLS:-false}" == "true" ]] && echo "--tls" || echo "")
    readonly RESOURCE_KEY=$([[ "$RESOURCE_ALIAS" =~ .*mz.* ]] && echo "mz" || echo "sz")
    readonly INTERNAL_SUFFIX=$([[ "${NETWORKING_TYPE:-}" == "INTERNAL" ]] && echo "-internal" || echo "")
    
    # Wait for all required services before starting monitoring
    if ! wait_for_services_ready "$adminpass" "$SSL_FLAG" "$RESOURCE_KEY"; then
        echo "ERROR: Cannot start monitoring - required services are not ready" >&2
        exit 1
    fi
    
    # Common Redis CLI options (as array to preserve argument integrity)
    REDIS_OPTS=(--no-auth-warning -a "$adminpass")
    [[ -n "$SSL_FLAG" ]] && REDIS_OPTS+=("$SSL_FLAG")
    readonly REDIS_OPTS
    readonly MAX_RETRIES=3
    readonly RETRY_DELAY=2
    
    # Function to execute Redis CLI commands with error handling and retry logic
    redis_exec() {
        local host=$1 port=$2
        shift 2
        local attempt=1
        local output
        local exit_code
        
        while [[ $attempt -le $MAX_RETRIES ]]; do
            # Temporarily disable errexit to prevent premature exit during retries
            set +e
            output=$(redis-cli "${REDIS_OPTS[@]}" -h "$host" -p "$port" "$@" 2>&1)
            exit_code=$?
            set -e
            
            if [[ $exit_code -eq 0 ]]; then
                echo "$output"
                return 0
            fi
            
            if [[ $attempt -lt $MAX_RETRIES ]]; then
                echo "Could not connect to Redis at ${host}:${port} (attempt $attempt/$MAX_RETRIES): $output" >&2
                echo "Retrying in ${RETRY_DELAY} seconds..." >&2
                sleep $RETRY_DELAY
            fi
            
            ((attempt++))
        done
        
        # All retries failed - log but don't exit (we're in monitoring loop)
        echo "Could not connect to Redis at ${host}:${port} after $MAX_RETRIES attempts" >&2
        return 1
    }
    
    # Function to get master address from sentinel
    get_master_addr() {
        local host=$1
        local result
        # Redis SENTINEL returns hostname and port on separate lines
        result=$(redis_exec "$host" "$SENTINEL_PORT" SENTINEL get-master-addr-by-name "$MASTER_NAME" 2>/dev/null | {
            read -r hostname
            read -r port
            echo "$hostname"
        }) || return 1
        [[ -n "$result" && "$result" != "null" ]] && echo "$result" || return 1
    }
    
    # Function to check if node is master
    is_master() {
        local host=$1
        redis_exec "$host" "$NODE_PORT" info | grep -q "role:master"
    }
    
    # Function to resolve split brain for a specific sentinel
    resolve_split_brain() {
        local host=$1
        echo "Resolving split brain for host: $host"
        
        redis_exec "$host" "$SENTINEL_PORT" SENTINEL remove "$MASTER_NAME" || true
        redis_exec "$host" "$SENTINEL_PORT" SENTINEL MONITOR "$MASTER_NAME" "$true_master" "$NODE_PORT" "$SENTINEL_QUORUM" || true
        redis_exec "$host" "$SENTINEL_PORT" SENTINEL SET "$MASTER_NAME" auth-pass "$adminpass" || true
        redis_exec "$host" "$SENTINEL_PORT" SENTINEL FLUSHCONFIG || true
    }
    
    # Monitoring loop
    check_split_brain() {
        local -a hosts=()
        local true_master=""
        
        # Get sentinel-0 master address
        if ! hosts[0]=$(get_master_addr "sentinel-${RESOURCE_KEY}-0"); then
            echo "Warning: Could not get master address from sentinel-${RESOURCE_KEY}-0" >&2
            return
        fi
        
        # Process replicas
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            
            # Get master address from this sentinel
            if hosts[$((i + 1))]=$(get_master_addr "$node_host"); then
                : # Success, continue
            else
                echo "Warning: Could not get master address from $node_host" >&2
            fi
            
            # Check if this node is the actual master
            if is_master "$node_host"; then
                true_master="${node_host}${INTERNAL_SUFFIX}.${EXTERNAL_DNS_SUFFIX}"
            fi
        done
        
        # Filter out empty/invalid responses
        local -a valid_hosts=()
        for host in "${hosts[@]}"; do
            if [[ -n "$host" && "$host" != "null" ]]; then
                valid_hosts+=("$host")
            fi
        done
        
        # Need at least one valid host to proceed
        if [[ ${#valid_hosts[@]} -eq 0 ]]; then
            echo "Warning: No valid sentinel responses" >&2
            return
        fi
        
        # Get unique hosts (split brain detection)
        local -a unique_hosts
        readarray -t unique_hosts < <(printf "%s\n" "${valid_hosts[@]}" | sort -u)
        
        if [[ -z "$true_master" ]]; then
            echo "Warning: Unable to determine the real master, skipping split-brain check" >&2
            return
        fi
        
        # Check for split brain condition
        if [[ ${#unique_hosts[@]} -gt 1 ]]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Split brain detected - ${#unique_hosts[@]} different masters reported"
            echo "True master: $true_master"
            
            # Fix sentinel-0 if needed
            if [[ "${hosts[0]}" != "$true_master" ]]; then
                echo "Fixing sentinel-${RESOURCE_KEY}-0"
                resolve_split_brain "sentinel-${RESOURCE_KEY}-0"
            fi
            
            # Fix replica sentinels if needed
            for ((i = 0; i < NUM_REPLICAS; i++)); do
                if [[ "${hosts[$((i + 1))]}" != "$true_master" ]]; then
                    echo "Fixing node-${RESOURCE_KEY}-$i sentinel"
                    resolve_split_brain "node-${RESOURCE_KEY}-$i"
                fi
            done
            
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Split brain resolution completed"
        else
            echo "$(date '+%Y-%m-%d %H:%M:%S') - No split brain detected - all sentinels report same master: ${unique_hosts[0]}"
        fi
    }
    
    # Infinite monitoring loop
    while true; do
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Running split-brain check"
        
        # Run check in a subshell to prevent any errors from terminating the loop
        (check_split_brain) || {
            echo "Warning: Split-brain check encountered an error" >&2
        }
        
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Sleeping for ${MONITORING_INTERVAL} seconds"
        sleep "$MONITORING_INTERVAL"
    done
}

# Run main function
main
