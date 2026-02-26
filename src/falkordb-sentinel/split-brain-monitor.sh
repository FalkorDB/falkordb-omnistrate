#!/bin/bash
set -euo pipefail

# This script continuously monitors and resolves split-brain conditions in a Redis Sentinel
# cluster. It runs in an infinite loop and is managed by supervisord.
#
# Cases handled:
#
#   Case 1 — Minority of sentinels points to a different node as master
#             Detected in Steps 4-7: all sentinel views are collected; any that disagree
#             with the true master (the node actually reporting role:master) are reconfigured
#             via SENTINEL remove + SENTINEL MONITOR.
#
#   Case 2 — A replica, agreed on by the majority of sentinels, is not replicating from
#             the correct master
#             Detected in fix_replica_replication(): called at Step 5 (when sentinels agree)
#             and Step 6.5 (after true master is determined). Issues REPLICAOF to any
#             slave whose master_host does not match the quorum master.
#
#   Case 3a — The sentinel-agreed master is a slave of itself (self-replication loop)
#             Detected in handle_self_replication(): checks master_host against the node's
#             own name using exact matching. Issues REPLICAOF <actual_master> to fix it.
#
#   Case 3b — The sentinel-agreed master is a slave of another node
#             Covered by fix_replica_replication() as a subset of Case 2: any node that
#             sentinel considers master but reports role:slave will be redirected.
#
# Safety guards:
#   - All nodes must respond to PING before any action (Step 1)
#   - Multiple role:master nodes → skip cycle (sentinel failover in progress, Step 2)
#   - Sentinel flags != "master" → skip sentinel/replica intervention (is_failover_in_progress)
#   - Self-replication fix (Step 3) always runs regardless of sentinel state

# Monitoring interval (seconds) - should be well below SENTINEL_DOWN_AFTER (default 30s)
# so the monitor can detect and fix split-brain conditions within a single failover window.
readonly MONITORING_INTERVAL=${SPLIT_BRAIN_MONITORING_INTERVAL:-5}

# Function to check if a Redis instance is responding
# Uses a 5s timeout so a hung connection never blocks the monitoring loop.
check_redis_connectivity() {
    local host=$1
    local port=$2
    local password=$3
    local ssl_flag=$4

    timeout 5 redis-cli --no-auth-warning -a "$password" ${ssl_flag} -h "$host" -p "$port" PING &>/dev/null
    return $?
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
    
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Split-brain monitor started"

    # Debug logger — set DEBUG=true to enable verbose output
    log_debug() {
        [[ "${DEBUG:-false}" == "true" ]] && echo "$(date '+%Y-%m-%d %H:%M:%S') [DEBUG] $*" >&2 || true
    }
    
    # Common Redis CLI options (as array to preserve argument integrity)
    REDIS_OPTS=(--no-auth-warning -a "$adminpass")
    [[ -n "$SSL_FLAG" ]] && REDIS_OPTS+=("$SSL_FLAG")
    readonly REDIS_OPTS
    
    # Function to execute Redis CLI commands with error handling (no retries for monitoring)
    # Uses a 5s timeout to prevent a hung node from blocking the monitoring cycle.
    redis_exec() {
        local host=$1 port=$2
        shift 2
        local output
        
        set +e
        output=$(timeout 5 redis-cli "${REDIS_OPTS[@]}" -h "$host" -p "$port" "$@" 2>&1)
        local exit_code=$?
        set -e
        
        if [[ $exit_code -eq 0 ]]; then
            log_debug "redis_exec $host:$port $* -> ok"
            echo "$output"
            return 0
        fi

        log_debug "redis_exec $host:$port $* -> failed (exit $exit_code)"
        return 1
    }
    
    # Function to get master address from sentinel
    get_master_addr() {
        local host=$1
        local output
        local result
        
        # Get the output from redis_exec
        output=$(redis_exec "$host" "$SENTINEL_PORT" SENTINEL get-master-addr-by-name "$MASTER_NAME" 2>/dev/null) || {
            log_debug "get_master_addr: $host returned no response"
            return 1
        }

        # Extract just the hostname (first line) using bash string manipulation to avoid broken pipe
        result="${output%%$'\n'*}"

        if [[ -z "$result" || "$result" == "null" || "$result" == "(nil)" ]]; then
            log_debug "get_master_addr: $host returned nil/empty for $MASTER_NAME"
            return 1
        fi

        log_debug "get_master_addr: $host reports master=$result"
        echo "$result"
    }
    
    # Function to check if node is master
    is_master() {
        local host=$1
        local info
        info=$(redis_exec "$host" "$NODE_PORT" info 2>/dev/null) || return 1
        [[ "$info" == *"role:master"* ]]
    }
    
    # Function to resolve split brain for a specific sentinel
    resolve_split_brain() {
        local host=$1
        
        redis_exec "$host" "$SENTINEL_PORT" SENTINEL remove "$MASTER_NAME" || true
        redis_exec "$host" "$SENTINEL_PORT" SENTINEL MONITOR "$MASTER_NAME" "$true_master" "$NODE_PORT" "$SENTINEL_QUORUM" || true
        redis_exec "$host" "$SENTINEL_PORT" SENTINEL SET "$MASTER_NAME" auth-pass "$adminpass" || true
        redis_exec "$host" "$SENTINEL_PORT" SENTINEL FLUSHCONFIG || true
    }
    
    # Function to detect and fix self-replication loops (Case 3a)
    # Takes actual_master (FQDN) as determined by INFO role:master scan — avoids
    # relying on a single sentinel's view which may itself be stale.
    # Note: we do NOT skip actual_master here — there is a timing window between
    # Step 2 (which identified it as role:master) and now where an external REPLICAOF
    # could have demoted it. The role:slave check below is the authoritative gate.
    handle_self_replication() {
        local actual_master=$1
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"

            local info
            info=$(redis_exec "$node_host" "$NODE_PORT" info replication 2>/dev/null) || continue
            [[ "$info" != *"role:slave"* ]] && continue

            local master_host
            master_host=$(echo "$info" | grep "^master_host:" | cut -d: -f2 | tr -d '\r' | xargs)

            # Detect self-replication: master_host is exactly this node's short name,
            # or is an FQDN starting with "<short-name>." (e.g. node-sz-1.instance-xxx.cloud).
            # Exact matching avoids false positives like node-sz-1 matching node-sz-10.
            if [[ "$master_host" == "$node_host" ]] || [[ "$master_host" == "$node_host."* ]]; then
                echo "$(date '+%Y-%m-%d %H:%M:%S') - SELF-REPLICATION detected on $node_host (master_host: $master_host), redirecting to actual master $actual_master"
                redis_exec "$node_host" "$NODE_PORT" REPLICAOF "$actual_master" "$NODE_PORT" || true
            fi
        done
    }
    
    # Returns 0 (true) if any sentinel reports master flags other than "master",
    # meaning a failover or sdown/odown is actively in progress — skip all intervention.
    is_failover_in_progress() {
        local -a all_sentinels=("sentinel-${RESOURCE_KEY}-0")
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            all_sentinels+=("node-${RESOURCE_KEY}-$i")
        done

        for host in "${all_sentinels[@]}"; do
            local flags
            flags=$(redis_exec "$host" "$SENTINEL_PORT" SENTINEL MASTER "$MASTER_NAME" 2>/dev/null \
                | awk '/^flags$/{getline; print; exit}') || continue
            if [[ -n "$flags" && "$flags" != "master" ]]; then
                echo "$(date '+%Y-%m-%d %H:%M:%S') - Sentinel $host reports master flags='$flags', sentinel is handling this — skipping intervention"
                return 0
            fi
            log_debug "is_failover_in_progress: $host flags='$flags' (idle)"
        done
        return 1
    }

    # Function to ensure all replica nodes are replicating from the correct master (Case 2)
    fix_replica_replication() {
        local quorum_master=$1
        local quorum_short="${quorum_master%%.*}"  # short hostname extracted from FQDN
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            local node_fqdn="${node_host}${INTERNAL_SUFFIX}.${EXTERNAL_DNS_SUFFIX}"
            # Skip the master itself — exact FQDN match, or quorum_master is this node's
            # short name or FQDN (dot-delimited to avoid node-sz-1 matching node-sz-10)
            if [[ "$node_fqdn" == "$quorum_master" ]] \
                || [[ "$quorum_master" == "$node_host" ]] \
                || [[ "$quorum_master" == "$node_host."* ]]; then
                continue
            fi
            local info
            info=$(redis_exec "$node_host" "$NODE_PORT" info replication 2>/dev/null) || continue
            local role
            role=$(echo "$info" | grep "^role:" | tr -d '\r' | cut -d: -f2 | xargs)
            [[ "$role" != "slave" ]] && continue
            local current_master
            current_master=$(echo "$info" | grep "^master_host:" | cut -d: -f2 | tr -d '\r' | xargs)
            # If master_host is unreadable, skip rather than risk a spurious REPLICAOF
            [[ -z "$current_master" ]] && { log_debug "fix_replica_replication: $node_host master_host empty, skipping"; continue; }
            # Exact matching: current_master must equal the full FQDN, the short name,
            # or an FQDN with the same short-name prefix (dot-delimited).
            if [[ "$current_master" == "$quorum_master" ]] \
                || [[ "$current_master" == "$quorum_short" ]] \
                || [[ "$current_master" == "$quorum_short."* ]]; then
                log_debug "fix_replica_replication: $node_host correctly replicating from $current_master"
                continue
            fi
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Replica $node_host is replicating from $current_master instead of $quorum_master, reconfiguring"
            redis_exec "$node_host" "$NODE_PORT" REPLICAOF "$quorum_master" "$NODE_PORT" || true
        done
    }

    # Check that all sentinel and node hostnames resolve before taking any action.
    # Returns 1 (and logs a warning) if any hostname is unresolvable.
    all_dns_resolved() {
        local hosts_to_check=("sentinel-${RESOURCE_KEY}-0${INTERNAL_SUFFIX}.${EXTERNAL_DNS_SUFFIX}")
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            hosts_to_check+=("node-${RESOURCE_KEY}-$i${INTERNAL_SUFFIX}.${EXTERNAL_DNS_SUFFIX}")
        done

        for host in "${hosts_to_check[@]}"; do
            if ! getent hosts "$host" &>/dev/null; then
                log_debug "all_dns_resolved: $host not yet resolvable, skipping cycle"
                return 1
            fi
        done
        return 0
    }

    # Monitoring loop
    check_split_brain() {
        # Step 1: Verify all sentinels and nodes are reachable via ping
        if ! check_redis_connectivity "sentinel-${RESOURCE_KEY}-0" "$SENTINEL_PORT" "$adminpass" "$SSL_FLAG"; then
            log_debug "check_split_brain: sentinel-${RESOURCE_KEY}-0:${SENTINEL_PORT} unreachable, skipping cycle"
            return
        fi
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            if ! check_redis_connectivity "$node_host" "$SENTINEL_PORT" "$adminpass" "$SSL_FLAG"; then
                log_debug "check_split_brain: ${node_host}:${SENTINEL_PORT} (sentinel port) unreachable, skipping cycle"
                return
            fi
            if ! check_redis_connectivity "$node_host" "$NODE_PORT" "$adminpass" "$SSL_FLAG"; then
                log_debug "check_split_brain: ${node_host}:${NODE_PORT} (redis port) unreachable, skipping cycle"
                return
            fi
        done

        # Step 2: Check if multiple nodes report role:master — log and bail, do not intervene
        local -a actual_masters=()
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            if is_master "$node_host"; then
                actual_masters+=("${node_host}${INTERNAL_SUFFIX}.${EXTERNAL_DNS_SUFFIX}")
            fi
        done

        if [[ ${#actual_masters[@]} -eq 0 ]]; then
            log_debug "check_split_brain: no node reports role:master, skipping cycle"
            return
        fi

        if [[ ${#actual_masters[@]} -gt 1 ]]; then
            log_debug "check_split_brain: ${#actual_masters[@]} nodes report role:master (${actual_masters[*]}), failover in progress, skipping cycle"
            # (the old master has not yet received REPLICAOF from sentinel).  Intervening here
            # would race with sentinel's own recovery, so we simply skip this cycle silently.
            return
        fi

        # Exactly one actual master
        local actual_master="${actual_masters[0]}"
        log_debug "check_split_brain: actual master is $actual_master"

        # Step 3: Check for self-replication and fix it
        # (runs regardless of failover state — self-replication is always wrong)
        handle_self_replication "$actual_master"

        # Gate: if sentinel is actively handling a failover (flags != "master"),
        # do not intervene with sentinel reconfiguration or replica redirection.
        if is_failover_in_progress; then
            return
        fi

        # Step 4: Collect sentinel views
        local -a sentinel_hosts=("sentinel-${RESOURCE_KEY}-0")
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            sentinel_hosts+=("node-${RESOURCE_KEY}-$i")
        done

        local -a sentinel_views=()
        for idx in "${!sentinel_hosts[@]}"; do
            local view
            if view=$(get_master_addr "${sentinel_hosts[$idx]}") && [[ -n "$view" ]]; then
                sentinel_views[$idx]="$view"
            else
                return
            fi
        done

        # Step 5: If all sentinels already agree on the actual master, nothing to do
        local all_agree=true
        for view in "${sentinel_views[@]}"; do
            if [[ "$view" != "$actual_master" ]]; then
                all_agree=false
                break
            fi
        done
        if [[ "$all_agree" == "true" ]]; then
            log_debug "check_split_brain: all sentinels agree on $actual_master, checking replica wiring"
            # Sentinels agree on the master — still verify replica replication targets (Case 2)
            fix_replica_replication "$actual_master"
            return
        fi

        # Step 6: Determine true master.
        # We always trust the node that actually reports role:master via INFO (actual_master).
        # We additionally compute the sentinel quorum master only to log a warning when
        # sentinel's majority disagrees with the live state — useful for diagnostics.
        local total_sentinels=${#sentinel_hosts[@]}
        local quorum=$(( (total_sentinels / 2) + 1 ))

        declare -A _vote_counts
        for view in "${sentinel_views[@]}"; do
            [[ -n "$view" ]] && _vote_counts["$view"]=$(( ${_vote_counts["$view"]:-0} + 1 ))
        done

        local sentinel_quorum_master=""
        for candidate in "${!_vote_counts[@]}"; do
            if [[ ${_vote_counts[$candidate]} -ge $quorum ]]; then
                sentinel_quorum_master="$candidate"
                break
            fi
        done
        unset _vote_counts

        if [[ -n "$sentinel_quorum_master" && "$sentinel_quorum_master" != "$actual_master" ]]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Sentinel quorum prefers $sentinel_quorum_master but it reports as replica. Trusting actual master $actual_master"
        fi

        local true_master="$actual_master"

        # Step 6.5: Ensure all replicas are replicating from true_master (Case 2)
        fix_replica_replication "$true_master"

        # Step 7: Fix any sentinel that disagrees with true_master
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Split brain detected. True master: $true_master"
        for idx in "${!sentinel_hosts[@]}"; do
            local host="${sentinel_hosts[$idx]}"
            local view="${sentinel_views[$idx]}"
            if [[ "$view" != "$true_master" ]]; then
                echo "$(date '+%Y-%m-%d %H:%M:%S') - ${host} has wrong master ($view), reconfiguring"
                resolve_split_brain "$host"
            fi
        done
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Split brain fixed"
    }
    
    # Infinite monitoring loop
    while true; do
        # Guard: skip everything if any hostname does not resolve yet
        if all_dns_resolved; then
            # Run check in a subshell to prevent any errors from terminating the loop
            (check_split_brain) || true
        fi
        
        sleep "$MONITORING_INTERVAL"
    done
}

# Run main function
main
