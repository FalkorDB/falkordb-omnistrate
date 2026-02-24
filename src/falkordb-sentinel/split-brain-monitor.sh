#!/bin/bash
set -euo pipefail

# This script continuously monitors and resolves split-brain conditions in Sentinel
# It runs in a loop and is managed by supervisord

# Monitoring interval (seconds) - check every second
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
        
        [[ -n "$result" && "$result" != "null" ]] && echo "$result" || return 1
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
    
    # Monitoring loop
    check_split_brain() {
        # First, handle any self-replication bugs
        handle_self_replication
        # Step 1: Verify ALL sentinels and nodes are reachable
        if ! check_redis_connectivity "sentinel-${RESOURCE_KEY}-0" "$SENTINEL_PORT" "$adminpass" "$SSL_FLAG"; then
            return
        fi
        
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            # Check sentinel connectivity
            if ! check_redis_connectivity "$node_host" "$SENTINEL_PORT" "$adminpass" "$SSL_FLAG"; then
                return
            fi
            # Check node connectivity
            if ! check_redis_connectivity "$node_host" "$NODE_PORT" "$adminpass" "$SSL_FLAG"; then
                return
            fi
        done
        
        # Step 2: Find all actual masters (nodes with role:master)
        local -a actual_masters=()
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            if is_master "$node_host"; then
                actual_masters+=("${node_host}${INTERNAL_SUFFIX}.${EXTERNAL_DNS_SUFFIX}")
            fi
        done
        
        # Step 3: If there are multiple actual masters, do nothing (alert will fire)
        if [[ ${#actual_masters[@]} -gt 1 ]]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Multiple actual masters detected (${#actual_masters[@]}). Skipping fix - alert should fire."
            return
        fi
        
        # Step 4: If no master found, skip
        if [[ ${#actual_masters[@]} -eq 0 ]]; then
            return
        fi
        
        # We have exactly one master
        local true_master="${actual_masters[0]}"
        
        # Step 5: Check what each sentinel reports
        local -a sentinel_views=()
        
        # Get sentinel-0 view
        if ! sentinel_views[0]=$(get_master_addr "sentinel-${RESOURCE_KEY}-0"); then
            return
        fi
        
        # Get replica sentinel views
        for ((i = 0; i < NUM_REPLICAS; i++)); do
            local node_host="node-${RESOURCE_KEY}-$i"
            if ! sentinel_views[$((i + 1))]=$(get_master_addr "$node_host"); then
                sentinel_views[$((i + 1))]=""  # Leave empty if get_master_addr fails
            fi
        done
        
        # Step 6: Check if all sentinels agree
        local all_agree=true
        for view in "${sentinel_views[@]}"; do
            if [[ "$view" != "$true_master" ]]; then
                all_agree=false
                break
            fi
        done
        
        # Step 7: If not all agree, fix the disagreeing sentinels
        if [[ "$all_agree" == "false" ]]; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Split brain detected. True master: $true_master"
            
            # Fix sentinel-0 if needed
            if [[ "${sentinel_views[0]}" != "$true_master" ]]; then
                resolve_split_brain "sentinel-${RESOURCE_KEY}-0"
            fi
            
            # Fix replica sentinels if needed
            for ((i = 0; i < NUM_REPLICAS; i++)); do
                # Skip entries where get_master_addr failed (empty or unset)
                if [[ -z "${sentinel_views[$((i + 1))]}" ]]; then
                    continue
                fi
                if [[ "${sentinel_views[$((i + 1))]}" != "$true_master" ]]; then
                    resolve_split_brain "node-${RESOURCE_KEY}-$i"
                fi
            done
            
            echo "$(date '+%Y-%m-%d %H:%M:%S') - Split brain fixed"
        fi
    }
    
    # Infinite monitoring loop
    while true; do
        # Run check in a subshell to prevent any errors from terminating the loop
        (check_split_brain) || true
        
        sleep "$MONITORING_INTERVAL"
    done
}

# Run main function
main
