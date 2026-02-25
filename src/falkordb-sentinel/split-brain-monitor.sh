#!/bin/bash
set -euo pipefail

# This script continuously monitors and resolves split-brain conditions in Sentinel
# It runs in a loop and is managed by supervisord

# Monitoring interval (seconds) - should be well below SENTINEL_DOWN_AFTER (default 30s)
# so the monitor can detect and fix split-brain conditions within a single failover window.
readonly MONITORING_INTERVAL=${SPLIT_BRAIN_MONITORING_INTERVAL:-5}

# Function to check if a Redis instance is responding
check_redis_connectivity() {
    local host=$1
    local port=$2
    local password=$3
    local ssl_flag=$4
    
    redis-cli --no-auth-warning -a "$password" ${ssl_flag} -h "$host" -p "$port" PING &>/dev/null
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
    
    # Common Redis CLI options (as array to preserve argument integrity)
    REDIS_OPTS=(--no-auth-warning -a "$adminpass")
    [[ -n "$SSL_FLAG" ]] && REDIS_OPTS+=("$SSL_FLAG")
    readonly REDIS_OPTS
    
    # Function to execute Redis CLI commands with error handling (no retries for monitoring)
    redis_exec() {
        local host=$1 port=$2
        shift 2
        local output
        
        set +e
        output=$(redis-cli "${REDIS_OPTS[@]}" -h "$host" -p "$port" "$@" 2>&1)
        local exit_code=$?
        set -e
        
        if [[ $exit_code -eq 0 ]]; then
            echo "$output"
            return 0
        fi
        
        return 1
    }
    
    # Function to get master address from sentinel
    get_master_addr() {
        local host=$1
        local output
        local result
        
        # Get the output from redis_exec
        output=$(redis_exec "$host" "$SENTINEL_PORT" SENTINEL get-master-addr-by-name "$MASTER_NAME" 2>/dev/null) || return 1
        
        # Extract just the hostname (first line) using bash string manipulation to avoid broken pipe
        result="${output%%$'\n'*}"
        
        [[ -n "$result" && "$result" != "null" && "$result" != "(nil)" ]] && echo "$result" || return 1
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
    
    # Function to detect and fix self-replication loops
    handle_self_replication() {
        # Check each node to see if it's slave-of-self
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            local info
            
            # Get replication info from the node
            info=$(redis_exec "$node_host" "$NODE_PORT" info replication 2>/dev/null) || continue
            
            # Only check slaves
            if [[ "$info" == *"role:slave"* ]]; then
                local master_host
                master_host=$(echo "$info" | grep "^master_host:" | cut -d: -f2 | tr -d '\r' | xargs)
                
                # Check if replicating from itself (compare hostname part)
                if [[ "$master_host" == *"node-${RESOURCE_KEY}-$i"* ]]; then
                    echo "$(date '+%Y-%m-%d %H:%M:%S') - SELF-REPLICATION detected on node-${RESOURCE_KEY}-$i (master_host: $master_host)"
                    
                    # Ask sentinel who the master should be
                    local sentinel_master
                    sentinel_master=$(get_master_addr "sentinel-${RESOURCE_KEY}-0" 2>/dev/null)
                    
                    if [[ -z "$sentinel_master" ]]; then
                        echo "$(date '+%Y-%m-%d %H:%M:%S') - WARNING: Cannot get master from sentinel, skipping fix for node-${RESOURCE_KEY}-$i"
                        continue
                    fi
                    
                    # Compare sentinel's choice with this node
                    local node_fqdn="${node_host}${INTERNAL_SUFFIX}.${EXTERNAL_DNS_SUFFIX}"
                    
                    if [[ "$sentinel_master" == "$node_fqdn" ]] || [[ "$sentinel_master" == "$node_host"* ]]; then
                        # Sentinel says THIS node should be master
                        echo "$(date '+%Y-%m-%d %H:%M:%S') - Sentinel says ${node_host} should be master, promoting with REPLICAOF NO ONE"
                        redis_exec "$node_host" "$NODE_PORT" REPLICAOF NO ONE || true
                    else
                        # Sentinel says a different node should be master
                        echo "$(date '+%Y-%m-%d %H:%M:%S') - Sentinel says master is ${sentinel_master}, reconfiguring ${node_host} to replicate from it"
                        redis_exec "$node_host" "$NODE_PORT" REPLICAOF "$sentinel_master" "$NODE_PORT" || true
                    fi
                    
                    echo "$(date '+%Y-%m-%d %H:%M:%S') - Self-replication fixed for node-${RESOURCE_KEY}-$i"
                fi
            fi
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
                return 1
            fi
        done
        return 0
    }

    # Monitoring loop
    check_split_brain() {
        # Step 1: Verify all sentinels and nodes are reachable via ping
        if ! check_redis_connectivity "sentinel-${RESOURCE_KEY}-0" "$SENTINEL_PORT" "$adminpass" "$SSL_FLAG"; then
            return
        fi
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            if ! check_redis_connectivity "$node_host" "$SENTINEL_PORT" "$adminpass" "$SSL_FLAG"; then
                return
            fi
            if ! check_redis_connectivity "$node_host" "$NODE_PORT" "$adminpass" "$SSL_FLAG"; then
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
            return
        fi

        if [[ ${#actual_masters[@]} -gt 1 ]]; then
            # Multiple nodes reporting role:master is expected during any failover transition
            # (the old master has not yet received REPLICAOF from sentinel).  Intervening here
            # would race with sentinel's own recovery, so we simply skip this cycle silently.
            return
        fi

        # Exactly one actual master
        local actual_master="${actual_masters[0]}"

        # Step 3: Check for self-replication and fix it
        handle_self_replication

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
                echo "$(date '+%Y-%m-%d %H:%M:%S') - ${sentinel_hosts[$idx]} returned nil for master address, cluster not stable yet - skipping"
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
            return
        fi

        # Step 6: Determine true master
        # a) actual_master = the node reporting role:master (already have it)
        # b) check if at least two sentinels agree on a master (quorum)
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

        local true_master=""
        if [[ -n "$sentinel_quorum_master" && "$sentinel_quorum_master" == "$actual_master" ]]; then
            # c) Both INFO and sentinel quorum agree — definite master
            true_master="$actual_master"
        elif [[ -z "$sentinel_quorum_master" ]]; then
            # d) Sentinels do not agree — trust INFO role:master
            true_master="$actual_master"
        else
            # Sentinel quorum picks a node that is actually a replica — sentinel is stale
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Sentinel quorum prefers $sentinel_quorum_master but it reports as replica. Trusting actual master $actual_master"
            true_master="$actual_master"
        fi

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
