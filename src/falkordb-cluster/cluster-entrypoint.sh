#!/bin/bash

read_secret_or_env() {
  local secret_path=$1
  local env_name=$2

  if [[ -f "$secret_path" ]] && [[ -s "$secret_path" ]]; then
    cat "$secret_path"
  else
    printf '%s' "${!env_name:-}"
  fi
}

load_credentials() {
  FALKORDB_USER=${FALKORDB_USER:-falkordb}
  FALKORDB_PASSWORD=$(read_secret_or_env "/run/secrets/falkordbpassword" "FALKORDB_PASSWORD")
  ADMIN_PASSWORD=$(read_secret_or_env "/run/secrets/adminpassword" "ADMIN_PASSWORD")
  export ADMIN_PASSWORD
}

initialize_defaults() {
  RUN_METRICS=${RUN_METRICS:-1}
  RUN_HEALTH_CHECK=${RUN_HEALTH_CHECK:-1}
  RUN_NODE=${RUN_NODE:-1}
  TLS=${TLS:-false}
  NODE_INDEX=${NODE_INDEX:-0}
  NETWORKING_TYPE=${NETWORKING_TYPE:-"PUBLIC"}
  INSTANCE_TYPE=${INSTANCE_TYPE:-''}
  PERSISTENCE_RDB_CONFIG_INPUT=${PERSISTENCE_RDB_CONFIG_INPUT:-'low'}
  PERSISTENCE_RDB_CONFIG=${PERSISTENCE_RDB_CONFIG:-'86400 1 21600 100 3600 10000'}
  PERSISTENCE_AOF_CONFIG=${PERSISTENCE_AOF_CONFIG:-'everysec'}
  FALKORDB_CACHE_SIZE=${FALKORDB_CACHE_SIZE:-25}
  FALKORDB_NODE_CREATION_BUFFER=${FALKORDB_NODE_CREATION_BUFFER:-16384}
  FALKORDB_MAX_QUEUED_QUERIES=${FALKORDB_MAX_QUEUED_QUERIES:-50}
  FALKORDB_RESULT_SET_SIZE=${FALKORDB_RESULT_SET_SIZE:-10000}
  FALKORDB_QUERY_MEM_CAPACITY=${FALKORDB_QUERY_MEM_CAPACITY:-0}
  FALKORDB_TIMEOUT_MAX=${FALKORDB_TIMEOUT_MAX:-0}
  FALKORDB_TIMEOUT_DEFAULT=${FALKORDB_TIMEOUT_DEFAULT:-0}
  FALKORDB_VKEY_MAX_ENTITY_COUNT=${FALKORDB_VKEY_MAX_ENTITY_COUNT:-4611686000000000000}
  FALKORDB_EFFECTS_THRESHOLD=${FALKORDB_EFFECTS_THRESHOLD:-0}
  MEMORY_LIMIT=${MEMORY_LIMIT:-''}
  AOF_FILE_SIZE_TO_MONITOR=${AOF_FILE_SIZE_TO_MONITOR:-5}
  CLUSTER_REPLICAS=${CLUSTER_REPLICAS:-1}
  IS_MULTI_ZONE=${IS_MULTI_ZONE:-0}
  NODE_HOST=${NODE_HOST:-localhost}
  NODE_PORT=${NODE_PORT:-6379}
  BUS_PORT=${BUS_PORT:-16379}
  ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/ca-certificates.crt}
  TLS_MOUNT_PATH=${TLS_MOUNT_PATH:-/etc/tls}
  SELFSIGNED_CA_PATH="$TLS_MOUNT_PATH/selfsigned-ca.crt"
  DATA_DIR=${DATA_DIR:-"${FALKORDB_HOME}/data"}
  LDAP_ENABLED=${LDAP_ENABLED:-false}
}

normalize_optional_config_values() {
  if [[ "$FALKORDB_QUERY_MEM_CAPACITY" == "<nil>" ]]; then
    FALKORDB_QUERY_MEM_CAPACITY=0
  fi
  if [[ "$FALKORDB_TIMEOUT_MAX" == "<nil>" ]]; then
    FALKORDB_TIMEOUT_MAX=0
  fi
  if [[ "$FALKORDB_TIMEOUT_DEFAULT" == "<nil>" ]]; then
    FALKORDB_TIMEOUT_DEFAULT=0
  fi
}

prepare_data_dir() {
  # Ensure DATA_DIR ends with /data
  if [[ $(basename "$DATA_DIR") != 'data' ]]; then
    DATA_DIR="$DATA_DIR/data"
  fi

  # If DATA_DIR is /data (the volume mount itself), nothing to do
  if [[ "$DATA_DIR" == '/data' ]]; then
    return
  fi

  # Create parent directory for DATA_DIR
  mkdir -p "$(dirname "$DATA_DIR")"

  # If the /data volume mount exists and DATA_DIR is not yet created,
  # symlink DATA_DIR -> /data so all files land on the persistent volume.
  # Otherwise just create the directory.
  if [[ -d '/data' ]] && [[ ! -e "$DATA_DIR" ]]; then
    ln -s /data "$DATA_DIR"
  elif [[ ! -e "$DATA_DIR" ]]; then
    mkdir -p "$DATA_DIR"
  fi
}

prepare_tls_ca_bundle() {
  if [[ "$TLS" == "true" ]] && [[ -f "$SELFSIGNED_CA_PATH" ]]; then
    if ! cat "$ROOT_CA_PATH" "$SELFSIGNED_CA_PATH" > "$DATA_DIR/selfsigned-tls-combined.pem"; then
      echo "Failed to create combined CA cert file"
      exit 1
    fi
    ROOT_CA_PATH="$DATA_DIR/selfsigned-tls-combined.pem"
  fi
}

initialize_runtime_paths() {
  DEBUG=${DEBUG:-0}
  REPLACE_NODE_CONF=${REPLACE_NODE_CONF:-0}
  TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
  AUTH_CONNECTION_STRING="-a $ADMIN_PASSWORD --no-auth-warning"
  SAVE_LOGS_TO_FILE=${SAVE_LOGS_TO_FILE:-1}
  LOG_LEVEL=${LOG_LEVEL:-notice}
  RESOURCE_ALIAS=${RESOURCE_ALIAS:-""}
  DATE_NOW=$(date +"%Y%m%d%H%M%S")
  FALKORDB_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo "$DATA_DIR/falkordb_$DATE_NOW.log"; else echo "/dev/null"; fi)
  NODE_CONF_FILE="$DATA_DIR/node.conf"

  if [[ $OMNISTRATE_ENVIRONMENT_TYPE != "PROD" ]]; then
    DEBUG=1
  fi
}

initialize_ldap() {
  LDAP_AUTH_SERVER_HTTP_URL=${LDAP_AUTH_SERVER_HTTP_URL:-'https://ldap-auth-service.ldap-auth.svc.cluster.local:8080'}
  LDAP_AUTH_SERVER_URL=${LDAP_AUTH_SERVER_URL:-'ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3390'}
  LDAP_AUTH_PASSWORD=${LDAP_AUTH_PASSWORD:-''}
  LDAP_AUTH_NAMESPACE=${LDAP_AUTH_NAMESPACE:-'ldap-auth'}
  LDAP_AUTH_PASSWORD_SECRET_NAME=${LDAP_AUTH_PASSWORD_SECRET_NAME:-'ldap-auth-admin-secret'}
  LDAP_AUTH_PASSWORD_SECRET_KEY=${LDAP_AUTH_PASSWORD_SECRET_KEY:-'LDAP_ADMIN_PASSWORD'}
  LDAP_AUTH_CA_CERT_PATH=${LDAP_AUTH_CA_CERT_PATH:-"$DATA_DIR/ldap-ca-cert.crt"}
  # if LDAP_AUTH_PASSWORD is empty, retrieve with with curl from namespace secret
  if [[ -z "$LDAP_AUTH_PASSWORD" ]]; then
    echo "Retrieving LDAP auth password from Kubernetes secret"
    LDAP_AUTH_PASSWORD=$(curl -s --cacert /var/run/secrets/kubernetes.io/serviceaccount/ca.crt --header "Authorization: Bearer $(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" "https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT/api/v1/namespaces/$LDAP_AUTH_NAMESPACE/secrets/$LDAP_AUTH_PASSWORD_SECRET_NAME" | jq -r ".data.\"$LDAP_AUTH_PASSWORD_SECRET_KEY\"" | base64 -d)
    echo "LDAP auth password retrieved"
  fi

  # Retrieve ldap server CA certificate
  echo "Retrieving LDAP server CA certificate"
  curl -s --insecure $LDAP_AUTH_SERVER_HTTP_URL/api/v1/ca-certificate > $LDAP_AUTH_CA_CERT_PATH
  echo "LDAP CA certificate saved to $LDAP_AUTH_CA_CERT_PATH"
}

ensure_run_bgrewriteaof_script() {
  if [[ ! -s "$FALKORDB_HOME/run_bgrewriteaof" && ! -f "$FALKORDB_HOME/run_bgrewriteaof" ]]; then
    echo "Creating run_bgrewriteaof script"
    cat > "$DATA_DIR/run_bgrewriteaof" <<'BGREWRITE_EOF'
#!/bin/bash
set -e
AOF_FILE_SIZE_TO_MONITOR=${AOF_FILE_SIZE_TO_MONITOR:-5}
ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/ca-certificates.crt}
DATA_DIR=${DATA_DIR:-/var/lib/falkordb/data}
TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
size=$(stat -c%s $DATA_DIR/appendonlydir/appendonly.aof.*.incr.aof)
if [ $size -gt $((AOF_FILE_SIZE_TO_MONITOR * 1024 * 1024)) ]; then
  echo "File larger than $AOF_FILE_SIZE_TO_MONITOR MB, running BGREWRITEAOF"
  $(which redis-cli) -a $(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING BGREWRITEAOF
else
  echo "File smaller than $AOF_FILE_SIZE_TO_MONITOR MB, not running BGREWRITEAOF"
fi
BGREWRITE_EOF
    chmod +x "$DATA_DIR/run_bgrewriteaof"
    ln -s "$DATA_DIR/run_bgrewriteaof" "$FALKORDB_HOME/run_bgrewriteaof"
    echo "run_bgrewriteaof script created"
  else
    echo "run_bgrewriteaof script already exists"
  fi
}

init_environment() {
  load_credentials
  initialize_defaults
  normalize_optional_config_values
  prepare_data_dir
  prepare_tls_ca_bundle
  initialize_runtime_paths
  if [[ "$LDAP_ENABLED" == "true" ]]; then
    initialize_ldap
  fi
  ensure_run_bgrewriteaof_script
}

add_ldap_config_to_conf() {
  if ! grep -q "^loadmodule /var/lib/falkordb/bin/valkey_ldap.so" "$NODE_CONF_FILE"; then
    echo "Adding LDAP module to node.conf"
    {
      echo "loadmodule /var/lib/falkordb/bin/valkey_ldap.so"
      echo "ldap.servers \"$LDAP_AUTH_SERVER_URL\""
      echo "ldap.auth_mode bind"
      echo "ldap.tls_ca_cert_path \"$LDAP_AUTH_CA_CERT_PATH\""
      echo "ldap.bind_dn_suffix \",ou=$INSTANCE_ID,dc=falkordb,dc=cloud\""
      echo "ldap.search_base \"ou=$INSTANCE_ID,dc=falkordb,dc=cloud\""
      echo "ldap.search_bind_dn \"cn=admin,ou=admin,dc=falkordb,dc=cloud\""
      echo "ldap.search_bind_passwd \"$LDAP_AUTH_PASSWORD\""
      echo "ldap.groups_rules_attribute \"description\""
      echo "ldap.exempted_users_regex \"^(default|falkordbUpgradeUser)$\""
      echo "ldap.acl_fallback_enabled yes"
      echo "ldap.tls_skip_verify yes"
    } >> "$NODE_CONF_FILE"
  else
    echo "LDAP module already present in node.conf"
  fi
}

meet_unknown_nodes() {
  # Had to add sleep until things are stable (nodes that can communicate should be given time to do so)
  # This fixes an issue where two nodes restart (ex: cluster-sz-1 (x.x.x.1) and cluster-sz-2 (x.x.x.2)) and their ips are switched
  # cluster-sz-1 gets (x.x.x.2) and cluster-sz-2 gets (x.x.x.1).
  # This can be caught by looking for the lines in the $DATA_DIR/nodes.conf file which have either the "fail" state or the "0:@0".
  # To fix the issue we use the CLUSTER MEET command to update the ips of each node that is unknown (0:@0 or fail).
  # Now the nodes should communitcate as expected.

  if [[ -f "$DATA_DIR/nodes.conf" && -s "$DATA_DIR/nodes.conf" ]]; then
    discrepancy=0
    while IFS= read -r line; do
      if [[ $line =~ .*@0.* || $line =~ .*fail.* ]]; then
        discrepancy=$((discrepancy + 1))
        hostname=$(echo "$line" | awk '{print $2}' | cut -d',' -f2 | cut -d':' -f1)

        tout=$(($(date +%s) + 300))
        while true; do
          if [[ $(date +%s) -gt $tout ]]; then
            echo "Timedout after 5 minutes while trying to ping $ip"
            exit 1
          fi

          sleep 3

          ip=$(getent hosts "$hostname" | awk '{print $1}')

          if [[ "$NETWORKING_TYPE" == "INTERNAL" ]]; then
            echo "The hostname is: $hostname"
            echo "The network type is: $NETWORKING_TYPE"
            hostname=$NODE_HOST
          else
            hostname=$(echo $hostname | cut -d'.' -f1)
          fi

          PONG=$(redis-cli -h $hostname $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING PING)

          echo "The answer to PING is: $PONG"
          echo "The ip is: $ip"

          if [[ -n $ip && $PONG == "PONG" ]]; then
            break
          fi

        done

        redis-cli $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CLUSTER MEET $ip $NODE_PORT
        echo "Found $discrepancy IP discrepancy in line: $line"

      fi

    done <<<"$(redis-cli $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CLUSTER NODES)"

    if [[ $discrepancy -eq 0 ]]; then
      echo "Did not find IP discrepancies between nodes."
    fi

  fi
  return 0
}

ensure_replica_connects_to_the_right_master_ip() {
  # This fixes an issue where a replica connects to the wrong ip of its master
  # the node does not update the ip of its master and gets stuck trying to connect to an incorrect ip.
  # To fix this we check for each slave if the master ip present (shown) using the "INFO REPLICATION"
  # is also found in the $DATA_DIR/nodes.conf or in the "CLUSTER NODES" output and if it is not
  # we update the new master using the CLUSTER REPLICATE command.
  info=$(redis-cli $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING info replication)
  if [[ "$info" =~ role:slave ]]; then
    echo "Making sure slave is connected to master using right ip."
    master_ip=$(echo "$info" | grep master_host | cut -d':' -f2 | tr -d '\r')
    echo "the master ip is: $master_ip"
    ans=$(grep "$master_ip" "$DATA_DIR/nodes.conf")
    echo "The answer is: $ans"
    if [[ -z $ans ]]; then
      echo "This instance is connected to its master using the wrong ip."
      myself=$(grep 'myself' "$DATA_DIR/nodes.conf")
      echo "The myself line is: $myself"
      master_id=$(echo "$myself" | awk '{print $4}')
      echo "The master id is: $master_id"
      redis-cli $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CLUSTER REPLICATE $master_id
    fi

  fi

}

resolve_host_ip() {
  local host=$1
  local description=${2:-$1}
  local timeout_seconds=${3:-300}
  local deadline=$((SECONDS + timeout_seconds))
  local resolved_ip

  if [[ $host =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$host"
    return 0
  fi

  while true; do
    resolved_ip=$(getent hosts "$host" 2>/dev/null | awk '{print $1; exit}')
    if [[ -n "$resolved_ip" ]]; then
      echo "$resolved_ip"
      return 0
    fi

    if (( SECONDS >= deadline )); then
      echo "Timed out trying to resolve ip for $description: $host" >&2
      return 1
    fi

    echo "Waiting for $description to resolve: $host" >&2
    sleep 3
  done
}

update_ips_in_nodes_conf() {
  # Replace old ip with new one (external ip)
  # This fixes the issue where when a node restarts it does not update its own ip
  # this is fixed by getting the new public ip using the command "getent hosts $NODE_HOST" (NODE_HOST
  # contains the domain name of the current node) and updating the nodes.conf file with the new ip before starting the redis server.
  # All cluster hostnames must resolve before Redis starts so restored nodes.conf entries do not
  # reconnect to stale backup IPs during initialization.

  local node_port_for_nodes_conf=$NODE_PORT
  local tmp_nodes_conf
  local nodes_content

  if [[ "$TLS" == "true" ]]; then
    node_port_for_nodes_conf=0
  fi

  if [[ ! -f "$DATA_DIR/nodes.conf" || ! -s "$DATA_DIR/nodes.conf" ]]; then
    echo "First time running the node.."
    return 0
  fi

  nodes_content=$(cat "$DATA_DIR/nodes.conf")
  tmp_nodes_conf="$DATA_DIR/nodes.conf.tmp"
  : > "$tmp_nodes_conf"

  while IFS= read -r line; do
    local current_line="$line"

    if [[ -z "$line" || "$line" =~ ^# || "$line" != *"@"* ]]; then
      printf '%s\n' "$current_line" >> "$tmp_nodes_conf"
      continue
    fi

    # Second field format: <ip>:<port>@<bus_port>[,<hostname>[:<tls_port>]]
    local old_addr
    old_addr=$(echo "$line" | awk '{print $2}' | cut -d',' -f1)

    if [[ -z "$old_addr" ]]; then
      printf '%s\n' "$current_line" >> "$tmp_nodes_conf"
      continue
    fi

    local old_ip
    old_ip=$(echo "$old_addr" | cut -d':' -f1)

    if [[ "$line" =~ myself ]]; then
      local self_ip=${POD_IP:-}

      if [[ -z "$self_ip" ]]; then
        self_ip=$(resolve_host_ip "$NODE_HOST" "current node host") || {
          rm -f "$tmp_nodes_conf"
          exit 1
        }
      fi

      local self_addr="$self_ip:$node_port_for_nodes_conf@$BUS_PORT"
      echo "Updating local node address: $old_addr -> $self_addr"
      current_line="${line/$old_addr/$self_addr}"
    else
      local hostname
      hostname=$(echo "$line" | awk '{print $2}' | cut -d',' -f2 | cut -d':' -f1)

      if [[ -z "$hostname" || "$hostname" == "$old_ip" ]]; then
        echo "No resolvable hostname found for node with addr: $old_addr, skipping"
      else
        local new_ip
        new_ip=$(resolve_host_ip "$hostname" "cluster node hostname") || {
          rm -f "$tmp_nodes_conf"
          exit 1
        }

        if [[ "$old_ip" != "$new_ip" ]]; then
          local new_addr="$new_ip:${old_addr#*:}"
          echo "Updating IP for node $hostname: $old_addr -> $new_addr"
          current_line="${line/$old_addr/$new_addr}"
        fi
      fi
    fi

    printf '%s\n' "$current_line" >> "$tmp_nodes_conf"
  done <<< "$nodes_content"

  mv "$tmp_nodes_conf" "$DATA_DIR/nodes.conf"
  cat "$DATA_DIR/nodes.conf"
  return 0
}

fix_namespace_in_config_files() {
  # Use INSTANCE_ID environment variable to get the current namespace
  if [[ -n "$INSTANCE_ID" ]]; then
    echo "Current namespace: $INSTANCE_ID"
    
    # Check and fix node.conf
    if [[ -f "$NODE_CONF_FILE" ]]; then
      echo "Checking node.conf for namespace mismatches"
      # Replace instance-X pattern with current namespace, where X can contain hyphens, underscores, and alphanumeric characters
      sed -i -E "s/instance-[a-zA-Z0-9_\-]+/${INSTANCE_ID}/g" "$NODE_CONF_FILE"
    fi
    
    # Check and fix nodes.conf (cluster mode)
    if [[ -f "$DATA_DIR/nodes.conf" ]]; then
      echo "Checking nodes.conf for namespace mismatches"
      sed -i -E "s/instance-[a-zA-Z0-9_\-]+/${INSTANCE_ID}/g" "$DATA_DIR/nodes.conf"
    fi
  else
    echo "INSTANCE_ID not set, skipping namespace fix"
  fi
  
  # Fix DNS suffix mismatches when snapshot is restored in different cluster
  if [[ -n "$DNS_SUFFIX" ]]; then
    echo "Current DNS suffix: $DNS_SUFFIX"
    
    # Escape special sed characters (&, \, /) in DNS_SUFFIX for safe use in replacement string
    local escaped_dns_suffix=$(echo "$DNS_SUFFIX" | sed 's/[&\\/]/\\&/g')
    
    # Check and fix node.conf
    if [[ -f "$NODE_CONF_FILE" ]]; then
      # First check if the file contains the current DNS suffix - if so, likely no replacement needed
      # But we still run the replacement to handle mixed cases where some entries might be outdated
      echo "Checking node.conf for DNS suffix mismatches"
      # Replace old DNS suffixes with current one for specific configuration parameters
      # This regex matches the Omnistrate DNS suffix structure: hc-<ID>.<REGION>.<CLOUD>.<HASH>.<TLD>
      # Example: hc-abc123.us-central1.gcp.f2e0a955bb84.cloud
      # Pattern: captures hostname (may contain underscores, must have at least one letter to avoid matching IPs), 
      # then matches the DNS suffix structure and replaces it with the current DNS suffix
      # The replacement is idempotent - if DNS suffix is already correct, it stays the same
      sed -i -E "s/([a-zA-Z0-9_-]*[a-zA-Z][a-zA-Z0-9_-]*)\.hc-[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.[a-f0-9]+\.[a-zA-Z]+/\1.${escaped_dns_suffix}/g" "$NODE_CONF_FILE"
    fi
    
    # Check and fix nodes.conf (cluster mode)
    if [[ -f "$DATA_DIR/nodes.conf" ]]; then
      echo "Checking nodes.conf for DNS suffix mismatches"
      # The replacement is idempotent - if DNS suffix is already correct, it stays the same
      sed -i -E "s/([a-zA-Z0-9_-]*[a-zA-Z][a-zA-Z0-9_-]*)\.hc-[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.[a-f0-9]+\.[a-zA-Z]+/\1.${escaped_dns_suffix}/g" "$DATA_DIR/nodes.conf"
    fi
  else
    echo "DNS_SUFFIX not set, skipping DNS suffix fix"
  fi
}

wait_for_node_ready() {
  local port=${1:-$NODE_PORT}
  echo "Waiting for FalkorDB to be ready on port $port"
  while true; do
    if [[ $(redis-cli -p $port $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING PING 2>/dev/null) == "PONG" ]]; then
      echo "FalkorDB is ready"
      break
    fi
    sleep 1
  done
}

wait_for_bgrewrite_to_finish() {
  tout=${tout:-30}
  # Give BGREWRITEAOF time to start
  sleep 3
  end=$((SECONDS + tout))
  while true; do
    if (( SECONDS >= end )); then
      echo "Timed out waiting for BGREWRITEAOF to complete"
      exit 1
    fi
    if [[ $(redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING INFO persistence | grep aof_rewrite_in_progress:0) ]]; then
      echo "BGREWRITEAOF completed"
      break
    fi
    sleep 1
  done
}

handle_sigterm() {
  echo "Caught SIGTERM"
  echo "Stopping FalkorDB"

  if [[ ! -z $falkordb_pid ]]; then
    # perform bgrewriteaof before shutting down
    echo "Running BGREWRITEAOF before shutdown"
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING BGREWRITEAOF
    wait_for_bgrewrite_to_finish
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SHUTDOWN
    kill -TERM $falkordb_pid
  fi

  exit 0
}

log() {
  if [[ $DEBUG -eq 1 ]]; then
    echo $1
  fi
}

get_host() {
  local host_idx=$1
  local deployment_mode=$(if [[ "$IS_MULTI_ZONE" == "1" ]]; then echo "mz"; else echo "sz"; fi)
  echo $(echo $NODE_HOST | sed "s/cluster-$deployment_mode-$NODE_INDEX/cluster-$deployment_mode-$host_idx/g")
}

wait_until_node_host_resolves() {
  local host=$1
  local port=$2

  # If $1 is an IP address, return
  if [[ $host =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    return
  fi

  while true; do
    log "Checking if node host resolves $host $port"
    if [[ $(getent hosts $host) ]]; then
      host_response=$(redis-cli -h $host -p $port $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING PING)
      host_response_code=$?
      log "Host Response: $host_response_code - $host_response"
      if [[ $host_response_code -eq 0 ]] && [[ $host_response == "PONG" ]]; then
        echo "Node host resolved"
        break
      fi
    fi
    echo "Waiting for node host to resolve"
    sleep 5
  done
}

wait_for_hosts() {
  local urls=$1
  echo "Waiting for hosts to resolve $urls"

  for url in $urls; do
    local host=$(echo $url | cut -d':' -f1)
    local port=$(echo $url | cut -d':' -f2)
    echo "Waiting for host $host:$port"
    wait_until_node_host_resolves $host $port
  done
}

create_user() {
  echo "Creating falkordb user"
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER reset
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER on ">$FALKORDB_PASSWORD" ~* +INFO +CLIENT +DBSIZE +PING +HELLO +AUTH +DUMP +DEL +EXISTS +UNLINK +TYPE +FLUSHALL +TOUCH +EXPIRE +PEXPIREAT +TTL +PTTL +EXPIRETIME +RENAME +RENAMENX +SCAN +DISCARD +EXEC +MULTI +UNWATCH +WATCH +ECHO +SLOWLOG +WAIT +WAITAOF +READONLY +MONITOR +GRAPH.INFO +GRAPH.LIST +GRAPH.QUERY +GRAPH.RO_QUERY +GRAPH.EXPLAIN +GRAPH.PROFILE +GRAPH.DELETE +GRAPH.CONSTRAINT +GRAPH.SLOWLOG +GRAPH.BULK +GRAPH.CONFIG +GRAPH.COPY +CLUSTER +COMMAND +GRAPH.MEMORY +GRAPH.UDF +MEMORY +BGREWRITEAOF '+MODULE|LIST'
}

get_default_memory_limit() {
  # Try to get container memory limit from cgroup (v2 then v1)
  local container_memory_bytes=0

  if [ -f /sys/fs/cgroup/memory.max ]; then
    # cgroup v2
    local mem_max=$(cat /sys/fs/cgroup/memory.max)
    if [ "$mem_max" != "max" ]; then
      container_memory_bytes=$mem_max
    fi
  elif [ -f /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then
    # cgroup v1
    container_memory_bytes=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)
  fi

  # If container memory limit is set and reasonable, calculate based on total memory
  if [ "$container_memory_bytes" -gt 0 ] && [ "$container_memory_bytes" -lt 9223372036854771712 ]; then
    # Calculate memory limit based on container size:
    # - <4GB: 100MB
    # - 4GB: minimum 2GB (50%)
    # - >4GB: 75% of total
    echo "$(awk -v mem_bytes="$container_memory_bytes" 'BEGIN {
      total_mb = mem_bytes / 1024 / 1024
      if (total_mb < 4096) {
        printf "%d\n", 100
      } else if (total_mb == 4096) {
        printf "%d\n", 2048
      } else {
        limit_75 = total_mb * 0.75
        printf "%d\n", limit_75
      }
    }')MB"
  else
    # Fall back to system memory from /proc/meminfo
    echo "$(awk '/MemTotal/ {
      total_mb = $2 / 1024
      if (total_mb < 4096) {
        printf "%d\n", 100
      } else if (total_mb == 4096) {
        printf "%d\n", 2048
      } else {
        limit_75 = total_mb * 0.75
        printf "%d\n", limit_75
      }
    }' /proc/meminfo)MB"
  fi
}

set_memory_limit() {
  if [[ -z $MEMORY_LIMIT ]]; then
    MEMORY_LIMIT=$(get_default_memory_limit)
  fi

  echo "Setting maxmemory to $MEMORY_LIMIT"
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET maxmemory $MEMORY_LIMIT
}

set_rdb_persistence_config() {
  if [[ $PERSISTENCE_RDB_CONFIG_INPUT == "low" ]]; then
    PERSISTENCE_RDB_CONFIG='86400 1 21600 100 3600 10000'
  elif [[ $PERSISTENCE_RDB_CONFIG_INPUT == "medium" ]]; then
    PERSISTENCE_RDB_CONFIG='21600 1 3600 100 300 10000'
  elif [[ $PERSISTENCE_RDB_CONFIG_INPUT == "high" ]]; then
    PERSISTENCE_RDB_CONFIG='3600 1 300 100 60 10000'
  else
    PERSISTENCE_RDB_CONFIG='86400 1 21600 100 3600 10000'
  fi
  echo "Setting persistence config: CONFIG SET save '$PERSISTENCE_RDB_CONFIG'"
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET save "$PERSISTENCE_RDB_CONFIG"
}

set_aof_persistence_config() {
  if [[ $PERSISTENCE_AOF_CONFIG != "no" ]]; then
    echo "Setting AOF persistence: $PERSISTENCE_AOF_CONFIG"
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET appendonly yes
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET appendfsync $PERSISTENCE_AOF_CONFIG
  fi
}

set_max_info_queries() {
  # if MAX_INFO_QUERIES does not exist in node.conf, set it to 1
  if ! grep -q "MAX_INFO_QUERIES 1" $NODE_CONF_FILE; then
    local max_info_queries=${FALKORDB_MAX_INFO_QUERIES:-1}
    echo "Setting max info queries to $max_info_queries"
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING GRAPH.CONFIG SET MAX_INFO_QUERIES $max_info_queries
  fi
}

config_rewrite() {
  echo "Rewriting configuration"
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG REWRITE
}

create_cluster() {

  local urls=""
  for host in $(seq 0 5); do
    urls="$urls $(get_host $host):$NODE_PORT"
  done

  wait_for_hosts "$urls"

  echo "Creating cluster with $urls"

  redis-cli --cluster create $urls --cluster-replicas $CLUSTER_REPLICAS $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING --cluster-yes

  if [[ $? -ne 0 ]]; then
    echo "Failed to create cluster"
    exit 1
  else
    touch $DATA_DIR/cluster_initialized
  fi
}

join_cluster() {

  local cluster_host=$(get_host 0)

  wait_for_hosts "$cluster_host:$NODE_PORT $NODE_HOST:$NODE_PORT"

  echo "Joining cluster on $cluster_host:$NODE_PORT"

  redis-cli --cluster add-node $NODE_HOST:$NODE_PORT $cluster_host:$NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING

  # If it was not successful, retry after 10 seconds
  if [[ $? -ne 0 ]]; then
    echo "Failed to join cluster. Retrying in 10 seconds"
    sleep 10
    join_cluster
  fi

}

run_node() {

  # Update .SO path for old instances
  sed -i "s|/FalkorDB/bin/src/bin/falkordb.so|/var/lib/falkordb/bin/falkordb.so|g" $NODE_CONF_FILE
  sed -i "s/\$ADMIN_PASSWORD/$ADMIN_PASSWORD/g" $NODE_CONF_FILE
  sed -i "s/\$LOG_LEVEL/$LOG_LEVEL/g" $NODE_CONF_FILE
  sed -i "s/\$NODE_HOST/$NODE_HOST/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_CACHE_SIZE/$FALKORDB_CACHE_SIZE/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_NODE_CREATION_BUFFER/$FALKORDB_NODE_CREATION_BUFFER/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_MAX_QUEUED_QUERIES/$FALKORDB_MAX_QUEUED_QUERIES/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_TIMEOUT_MAX/$FALKORDB_TIMEOUT_MAX/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_TIMEOUT_DEFAULT/$FALKORDB_TIMEOUT_DEFAULT/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_RESULT_SET_SIZE/$FALKORDB_RESULT_SET_SIZE/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_QUERY_MEM_CAPACITY/$FALKORDB_QUERY_MEM_CAPACITY/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_VKEY_MAX_ENTITY_COUNT/$FALKORDB_VKEY_MAX_ENTITY_COUNT/g" $NODE_CONF_FILE
  sed -i "s/\$FALKORDB_EFFECTS_THRESHOLD/$FALKORDB_EFFECTS_THRESHOLD/g" $NODE_CONF_FILE
  if [[ "$LDAP_ENABLED" == "true" ]]; then
    sed -i "s|\$LDAP_AUTH_SERVER_URL|$LDAP_AUTH_SERVER_URL|g" $NODE_CONF_FILE
    sed -i "s|\$LDAP_AUTH_CA_CERT_PATH|$LDAP_AUTH_CA_CERT_PATH|g" $NODE_CONF_FILE
    sed -i "s|\$INSTANCE_ID|$INSTANCE_ID|g" $NODE_CONF_FILE
    sed -i "s|\$LDAP_AUTH_PASSWORD|$LDAP_AUTH_PASSWORD|g" $NODE_CONF_FILE
  fi
  echo "dir $DATA_DIR/$i" >>$NODE_CONF_FILE

  if [[ "$LDAP_ENABLED" == "true" ]]; then
    add_ldap_config_to_conf
  fi

  if [[ $TLS == "true" ]]; then
    sed -i "s|/etc/ssl/certs/GlobalSign_Root_CA.pem|${ROOT_CA_PATH}|g" "$NODE_CONF_FILE"
    if ! grep -q "^tls-port $NODE_PORT" "$NODE_CONF_FILE"; then
      echo "port 0" >>$NODE_CONF_FILE
      echo "tls-port $NODE_PORT" >>$NODE_CONF_FILE
      echo "tls-cert-file $TLS_MOUNT_PATH/tls.crt" >>$NODE_CONF_FILE
      echo "tls-key-file $TLS_MOUNT_PATH/tls.key" >>$NODE_CONF_FILE
      echo "tls-client-cert-file $TLS_MOUNT_PATH/selfsigned-tls.crt" >>$NODE_CONF_FILE
      echo "tls-client-key-file $TLS_MOUNT_PATH/selfsigned-tls.key" >>$NODE_CONF_FILE
      echo "tls-ca-cert-file $ROOT_CA_PATH" >>$NODE_CONF_FILE
      echo "tls-cluster yes" >>$NODE_CONF_FILE
      echo "tls-auth-clients optional" >>$NODE_CONF_FILE
      echo "tls-replication yes" >>$NODE_CONF_FILE
    else
      sed -i "s|tls-port .*|tls-port $NODE_PORT|g" "$NODE_CONF_FILE"
      sed -i "s|tls-cert-file .*|tls-cert-file $TLS_MOUNT_PATH/tls.crt|g" "$NODE_CONF_FILE"
      sed -i "s|tls-key-file .*|tls-key-file $TLS_MOUNT_PATH/tls.key|g" "$NODE_CONF_FILE"
      if grep -q "^tls-client-cert-file " "$NODE_CONF_FILE"; then
        sed -i "s|tls-client-cert-file .*|tls-client-cert-file $TLS_MOUNT_PATH/selfsigned-tls.crt|g" "$NODE_CONF_FILE"
      else
        echo "tls-client-cert-file $TLS_MOUNT_PATH/selfsigned-tls.crt" >>$NODE_CONF_FILE
      fi
      if grep -q "^tls-client-key-file " "$NODE_CONF_FILE"; then
        sed -i "s|tls-client-key-file .*|tls-client-key-file $TLS_MOUNT_PATH/selfsigned-tls.key|g" "$NODE_CONF_FILE"
      else
        echo "tls-client-key-file $TLS_MOUNT_PATH/selfsigned-tls.key" >>$NODE_CONF_FILE
      fi
      sed -i "s|tls-ca-cert-file .*|tls-ca-cert-file $ROOT_CA_PATH|g" "$NODE_CONF_FILE"
      if grep -q "^tls-cluster " "$NODE_CONF_FILE"; then
        sed -i "s|tls-cluster .*|tls-cluster yes|g" "$NODE_CONF_FILE"
      else
        echo "tls-cluster yes" >>$NODE_CONF_FILE
      fi
      if grep -q "^tls-auth-clients " "$NODE_CONF_FILE"; then
        sed -i "s|tls-auth-clients .*|tls-auth-clients optional|g" "$NODE_CONF_FILE"
      else
        echo "tls-auth-clients optional" >>$NODE_CONF_FILE
      fi
      if grep -q "^tls-replication " "$NODE_CONF_FILE"; then
        sed -i "s|tls-replication .*|tls-replication yes|g" "$NODE_CONF_FILE"
      else
        echo "tls-replication yes" >>$NODE_CONF_FILE
      fi
    fi
  else
    echo "port $NODE_PORT" >>$NODE_CONF_FILE
  fi

  redis-server $NODE_CONF_FILE --logfile $FALKORDB_LOG_FILE_PATH &
  falkordb_pid=$!
  tail -F $FALKORDB_LOG_FILE_PATH &
}

ensure_node_conf_exists() {
  if [[ ! -f "$NODE_CONF_FILE" ]] || [[ "$REPLACE_NODE_CONF" -eq "1" ]]; then
    echo "Copying node.conf from /falkordb"
    cp /falkordb/node.conf "$NODE_CONF_FILE"
  fi
}

ensure_log_file_exists() {
  if [[ $SAVE_LOGS_TO_FILE -eq 1 ]] && [[ ! -f "$FALKORDB_LOG_FILE_PATH" ]]; then
    touch "$FALKORDB_LOG_FILE_PATH"
  fi
}

prepare_node_files_for_startup() {
  # Hostname rewrites must happen first so nodes.conf resolves against the current deployment.
  fix_namespace_in_config_files
  update_ips_in_nodes_conf
}

sync_ldap_server_url() {
  local config_output current_ldap_url
  local old_default="ldaps://ldap-auth-service.ldap-auth.svc.cluster.local:3389"

  if ! config_output=$(redis-cli --raw -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG GET ldap.servers 2>&1); then
    echo "Could not read ldap.servers from running config"
    return
  fi

  current_ldap_url=$(printf '%s\n' "$config_output" | sed -n '2p')

  if [[ -z "$current_ldap_url" || "$current_ldap_url" == ERR* ]]; then
    echo "ldap.servers not set or error reading config"
    return
  fi

  if [[ "$current_ldap_url" == "$old_default" ]]; then
    echo "Migrating ldap.servers from $old_default to $LDAP_AUTH_SERVER_URL"
    redis-cli --raw -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET ldap.servers "$LDAP_AUTH_SERVER_URL"
    config_rewrite
  else
    echo "ldap.servers is already up to date: $current_ldap_url"
  fi
}

sync_cluster_node_timeout() {
  local config_output current_timeout
  local desired_timeout="30000"

  if ! config_output=$(redis-cli --raw -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG GET cluster-node-timeout 2>&1); then
    echo "Could not read cluster-node-timeout from running config"
    return
  fi

  current_timeout=$(printf '%s\n' "$config_output" | sed -n '2p')

  if [[ -z "$current_timeout" || "$current_timeout" == ERR* ]]; then
    echo "cluster-node-timeout not set or error reading config"
    return
  fi

  if [[ "$current_timeout" != "$desired_timeout" ]]; then
    echo "Updating cluster-node-timeout from $current_timeout to $desired_timeout"
    redis-cli --raw -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET cluster-node-timeout "$desired_timeout"
  else
    echo "cluster-node-timeout is already up to date: $current_timeout"
  fi
}

post_start_configuration() {
  sync_cluster_node_timeout
  if [[ "$LDAP_ENABLED" == "true" ]]; then
    sync_ldap_server_url
  fi
  create_user
  set_memory_limit
  set_rdb_persistence_config
  set_aof_persistence_config
  set_max_info_queries
  config_rewrite
}

ensure_cluster_membership() {
  if [[ $NODE_INDEX -eq 0 && ! -f "$DATA_DIR/cluster_initialized" ]]; then
    echo "Creating cluster"
    create_cluster
  elif [[ $NODE_INDEX -gt 5 ]]; then
    echo "Joining cluster"
    join_cluster
  else
    echo "Cluster does not exist. Waiting for it to be created"
  fi
}

create_tls_rotation_job_script() {
  if [[ "$TLS" == "true" && $RUN_NODE -eq 1 ]]; then
    echo "Creating node certificate rotation job script"
    echo "
    #!/bin/bash
    set -e
    echo 'Refreshing node certificate'
    redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET tls-cert-file $TLS_MOUNT_PATH/tls.crt
    redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET tls-key-file $TLS_MOUNT_PATH/tls.key
    redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET tls-client-cert-file $TLS_MOUNT_PATH/selfsigned-tls.crt
    redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET tls-client-key-file $TLS_MOUNT_PATH/selfsigned-tls.key
    redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET tls-auth-clients optional
    redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET tls-ca-cert-file $ROOT_CA_PATH
    " > "$DATA_DIR/cert_rotate_node.sh"
    chmod +x "$DATA_DIR/cert_rotate_node.sh"
  fi
}

wait_forever() {
  while true; do
    sleep 1
  done
}

main() {
  init_environment
  trap handle_sigterm SIGTERM
  ensure_node_conf_exists
  ensure_log_file_exists
  prepare_node_files_for_startup
  run_node
  wait_for_node_ready
  post_start_configuration
  ensure_cluster_membership
  meet_unknown_nodes
  ensure_replica_connects_to_the_right_master_ip
  create_tls_rotation_job_script
  wait_forever
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
