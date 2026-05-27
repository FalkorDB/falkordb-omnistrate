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

load_credentials() {
  FALKORDB_USER=${FALKORDB_USER:-falkordb}
  FALKORDB_PASSWORD=$(read_secret_or_env "/run/secrets/falkordbpassword" "FALKORDB_PASSWORD")
  ADMIN_PASSWORD=$(read_secret_or_env "/run/secrets/adminpassword" "ADMIN_PASSWORD")
  export ADMIN_PASSWORD
}

initialize_defaults() {
  FALKORDB_UPGRADE_PASSWORD=${FALKORDB_UPGRADE_PASSWORD:-''}
  RUN_SENTINEL=${RUN_SENTINEL:-0}
  RUN_NODE=${RUN_NODE:-1}
  RUN_METRICS=${RUN_METRICS:-1}
  RUN_HEALTH_CHECK=${RUN_HEALTH_CHECK:-1}
  RUN_HEALTH_CHECK_SENTINEL=${RUN_HEALTH_CHECK_SENTINEL:-1}
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
  SENTINEL_PORT=${SENTINEL_PORT:-26379}
  SENTINEL_DOWN_AFTER=${SENTINEL_DOWN_AFTER:-30000}
  SENTINEL_FAILOVER=${SENTINEL_FAILOVER:-180000}
  SENTINEL_HOST=sentinel-$(echo $RESOURCE_ALIAS | cut -d "-" -f 2)-0.$LOCAL_DNS_SUFFIX
  NODE_HOST=${NODE_HOST:-localhost}
  NODE_PORT=${NODE_PORT:-6379}
  MASTER_NAME=${MASTER_NAME:-master}
  SENTINEL_QUORUM=${SENTINEL_QUORUM:-2}
  FALKORDB_MASTER_HOST=''
  FALKORDB_MASTER_PORT_NUMBER=${MASTER_PORT:-6379}
  IS_REPLICA=${IS_REPLICA:-0}
  ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/ca-certificates.crt}
  BASE_ROOT_CA_PATH=$ROOT_CA_PATH
  TLS_MOUNT_PATH=${TLS_MOUNT_PATH:-/etc/tls}
  SELFSIGNED_CA_PATH="$TLS_MOUNT_PATH/selfsigned-ca.crt"
  DATA_DIR=${DATA_DIR:-"${FALKORDB_HOME}/data"}
  LDAP_ENABLED=${LDAP_ENABLED:-false}
  if [[ "$RUN_SENTINEL" -eq 1 && "$LDAP_ENABLED" == "true" ]]; then
    echo "WARNING: LDAP is not supported with RUN_SENTINEL=1, disabling LDAP"
    LDAP_ENABLED=false
  fi
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

  COMBINED_CA_PATH="$DATA_DIR/selfsigned-tls-combined.pem"
}

prepare_tls_ca_bundle() {
  if [[ "$TLS" == "true" ]] && [[ -f "$SELFSIGNED_CA_PATH" ]]; then
    if ! cat "$BASE_ROOT_CA_PATH" "$SELFSIGNED_CA_PATH" > "$COMBINED_CA_PATH"; then
      echo "Failed to create combined CA cert file"
      exit 1
    fi
    ROOT_CA_PATH="$COMBINED_CA_PATH"
  fi
}

initialize_runtime_paths() {
  DEBUG=${DEBUG:-0}
  REPLACE_NODE_CONF=${REPLACE_NODE_CONF:-0}
  TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
  AUTH_CONNECTION_STRING="-a $ADMIN_PASSWORD --no-auth-warning"
  SAVE_LOGS_TO_FILE=${SAVE_LOGS_TO_FILE:-1}
  LOG_LEVEL=${LOG_LEVEL:-notice}
  DATE_NOW=$(date +"%Y%m%d%H%M%S")
  FALKORDB_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/falkordb_$DATE_NOW.log; else echo "/dev/null"; fi)
  NODE_CONF_FILE=$DATA_DIR/node.conf
  AOF_FILE_SIZE_TO_MONITOR=${AOF_FILE_SIZE_TO_MONITOR:-5}

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
  echo "Creating run_bgrewriteaof script"
  cat > "$DATA_DIR/run_bgrewriteaof" <<'BGREWRITE_EOF'
#!/bin/bash
set -e
AOF_FILE_SIZE_TO_MONITOR=${AOF_FILE_SIZE_TO_MONITOR:-5}
ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/ca-certificates.crt}
DATA_DIR=${DATA_DIR:-/var/lib/falkordb/data}
TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
size=0
shopt -s nullglob
if [[ $(basename "$DATA_DIR") != 'data' ]]; then DATA_DIR="$DATA_DIR/data"; fi
for file in $DATA_DIR/appendonlydir/appendonly.aof.*.incr.aof; do
  if [ -f "$file" ]; then
    file_size=$(stat -c%s "$file")
    size=$((size + file_size))
  fi
done
if [ $size -gt $((AOF_FILE_SIZE_TO_MONITOR * 1024 * 1024)) ]; then
  echo "File larger than $AOF_FILE_SIZE_TO_MONITOR MB, running BGREWRITEAOF"
  $(which redis-cli) -a $(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING BGREWRITEAOF
else
  echo "File smaller than $AOF_FILE_SIZE_TO_MONITOR MB, not running BGREWRITEAOF"
fi
BGREWRITE_EOF
  chmod +x "$DATA_DIR/run_bgrewriteaof"
  ln -s "$DATA_DIR/run_bgrewriteaof" $FALKORDB_HOME/run_bgrewriteaof
  echo "run_bgrewriteaof script created"
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

dump_conf_files() {
  echo "Dumping configuration files"

  if [ -f $NODE_CONF_FILE ]; then
    cat $NODE_CONF_FILE
  fi

  if [ -f $SENTINEL_CONF_FILE ]; then
    cat $SENTINEL_CONF_FILE
  fi
}

remove_master_from_group() {
  # If it's master and sentinel is running, trigger and wait for failover
  if [[ $IS_REPLICA -eq 0 && $RUN_SENTINEL -eq 1 ]]; then
    echo "Removing master from sentinel"
    redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL failover $MASTER_NAME
    sleep 5
    tries=5
    while true; do
      master_info=$(redis-cli $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING info replication | grep role)
      if [[ $master_info == *"role:master"* ]]; then
        echo "Master is still alive"
        sleep 2
      else
        echo "Master is down"
        break
      fi
      tries=$((tries - 1))
      if [[ $tries -eq 0 ]]; then
        echo "Master did not failover"
        break
      fi
    done
  fi
}

get_sentinels_list() {
  echo "Getting sentinels list"

  sentinels_list=$(redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL sentinels $MASTER_NAME)
  echo $sentinels_list >/tmp/sentinels_list.txt
  sentinels_list=$(IFS=' ' read -r -a sentinels <<<$(cat /tmp/sentinels_list.txt))
  sentinels_count=$(echo -n "${sentinels[@]}" | grep -Fo name | wc -l)
  # Parse sentinels into an array of "{ip} {port}"
  sentinels_list=''
  for ((i = 0; i < $sentinels_count; i++)); do
    sentinel_ip=${sentinels[i * 28 + 3]}
    sentinel_port=${sentinels[i * 28 + 5]}
    sentinels_list="$sentinels_list $sentinel_ip:$sentinel_port "
  done
  return $sentinels_list
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

  if [[ $RUN_NODE -eq 1 && ! -z $falkordb_pid ]]; then
    #DO NOT USE is_replica FUNCTION
    role=$(redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING info replication | grep role)
    if [[ "$role" =~ ^role:master ]]; then IS_REPLICA=0; fi
    echo "Running BGREWRITEAOF before shutdown"
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING BGREWRITEAOF
    wait_for_bgrewrite_to_finish
    remove_master_from_group
    redis-cli $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SHUTDOWN
  fi

  exit 0
}

log() {
  if [[ $DEBUG -eq 1 ]]; then
    echo $1
  fi
}

get_self_host_ip() {
  if [[ $NODE_HOST == "localhost" ]]; then
    NODE_HOST_IP=$(curl ifconfig.me)
  else
    NODE_HOST_IP=$(resolve_host_ip "$NODE_HOST" "self node host") || {
      echo "Failed to resolve self node host: $NODE_HOST"
      exit 1
    }
  fi
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

get_memory_limit() {
  # if memory limit is 1200M or 2200M, set it to 1GB or 2GB respectively
  if [[ $MEMORY_LIMIT == *"M" ]]; then
    if [[ $MEMORY_LIMIT == "1200M" ]]; then
      MEMORY_LIMIT="1G"
    elif [[ $MEMORY_LIMIT == "2200M" ]]; then
      MEMORY_LIMIT="2G"
    fi
  fi

  if [[ -z $MEMORY_LIMIT ]]; then
    MEMORY_LIMIT=$(get_default_memory_limit)
  fi

  echo "Memory Limit: $MEMORY_LIMIT"
}

wait_until_sentinel_host_resolves() {
  while true; do
    log "Checking if sentinel host resolves $SENTINEL_HOST"
    if [[ $(getent hosts $SENTINEL_HOST) ]]; then
      sentinel_response=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ping)
      
      log "Sentinel Response: $sentinel_response"
      if [[ $sentinel_response == "PONG" ]]; then
        echo "Sentinel host resolved"
        break
      fi
    fi
    echo "Waiting for sentinel host to resolve"
    sleep 5
  done

}

wait_until_node_host_resolves() {

  # If $1 is an IP address, return
  if [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    return
  fi

  while true; do
    log "Checking if node host resolves $1"
    if [[ $(getent hosts $1) ]]; then
      host_response=$(redis-cli -h $1 -p $2 $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING PING)

      log "Host Response: $host_response"
      if [[ $host_response == "PONG" ]]; then
        echo "Node host resolved"
        sleep 10
        break
      fi
    fi
    echo "Waiting for node host to resolve"
    sleep 5
  done
}

get_master() {
  master_info=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD $TLS_CONNECTION_STRING --no-auth-warning SENTINEL get-master-addr-by-name $MASTER_NAME)

  echo "Master Info: $master_info"

  # If RUN_SENTINEL is 1 and could not connect to sentinel, wait and try again
  if [[ $RUN_SENTINEL -eq 1 && -z $master_info && ! $HOSTNAME =~ ^node.*0 ]]; then
    echo "Could not connect to sentinel, waiting 5 seconds and trying again"
    sleep 5
    get_master
    return
  fi

  FALKORDB_MASTER_HOST=$(echo $master_info | awk '{print $1}')
  FALKORDB_MASTER_PORT_NUMBER=$(echo $master_info | awk '{print $2}')
}

is_replica() {
  get_master

  # If NODE_HOST starts with node-X, where X > 0, wait until FALKORDB_MASTER_HOST is not empty
  if [[ $NODE_INDEX -gt 0 && -z $FALKORDB_MASTER_HOST ]]; then
    echo "Waiting for master to be available"
    sleep 5
    is_replica
    return
  fi

  # IF host is empty, then this node is the master
  if [[ -z $FALKORDB_MASTER_HOST ]]; then
    FALKORDB_MASTER_HOST=$NODE_HOST
    FALKORDB_MASTER_PORT_NUMBER=$NODE_PORT
    IS_REPLICA=0
    return
  fi

  if [[ ($FALKORDB_MASTER_HOST == $NODE_HOST || $FALKORDB_MASTER_HOST == $NODE_HOST_IP) && $FALKORDB_MASTER_PORT_NUMBER == $NODE_PORT ]]; then
    # This node is the master
    IS_REPLICA=0
    return
  else
    # This node is a replica
    IS_REPLICA=1
    return
  fi

}

set_persistence_config() {
  if [[ $PERSISTENCE_RDB_CONFIG_INPUT == "low" ]]; then
    PERSISTENCE_RDB_CONFIG='86400 1 21600 100 3600 10000'
  elif [[ $PERSISTENCE_RDB_CONFIG_INPUT == "medium" ]]; then
    PERSISTENCE_RDB_CONFIG='21600 1 3600 100 300 10000'
  elif [[ $PERSISTENCE_RDB_CONFIG_INPUT == "high" ]]; then
    PERSISTENCE_RDB_CONFIG='3600 1 300 100 60 10000'
  else
    PERSISTENCE_RDB_CONFIG='86400 1 21600 100 3600 10000'
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

create_user() {
  echo "Creating falkordb user"

  if [[ $RESET_ADMIN_PASSWORD -eq 1 ]]; then
    echo "Resetting admin password"
    redis-cli -p $NODE_PORT -a $CURRENT_ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET requirepass $ADMIN_PASSWORD
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET masterauth $ADMIN_PASSWORD
  fi
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER reset
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER on ">$FALKORDB_PASSWORD" ~* +INFO +CLIENT +DBSIZE +PING +HELLO +AUTH +DUMP +DEL +EXISTS +UNLINK +TYPE +FLUSHALL +TOUCH +EXPIRE +PEXPIREAT +TTL +PTTL +EXPIRETIME +RENAME +RENAMENX +SCAN +DISCARD +EXEC +MULTI +UNWATCH +WATCH +ECHO +SLOWLOG +WAIT +WAITAOF +READONLY +MONITOR +GRAPH.INFO +GRAPH.LIST +GRAPH.QUERY +GRAPH.RO_QUERY +GRAPH.EXPLAIN +GRAPH.PROFILE +GRAPH.DELETE +GRAPH.CONSTRAINT +GRAPH.SLOWLOG +GRAPH.BULK +GRAPH.CONFIG +GRAPH.COPY +GRAPH.MEMORY +GRAPH.UDF +MEMORY +BGREWRITEAOF '+MODULE|LIST'
  config_rewrite
}

config_rewrite() {
  # Config rewrite
  echo "Rewriting config"
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG REWRITE
}

handle_network_type_changed() {
  # the node itself should have it's replica-announce-ip parameter set to the new $NODE_HOST value
  # if sentinel is running, set the sentinel announce-ip parameter to the new $NODE_HOST value
  if [[ $RUN_NODE -eq 1 ]]; then
    echo "Setting replica-announce-ip to $NODE_HOST"
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET replica-announce-ip $NODE_HOST
    config_rewrite
  fi
  if [[ $RUN_SENTINEL -eq 1 ]]; then
    echo "Setting sentinel announce-ip to $NODE_HOST"
    redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET announce-ip $NODE_HOST
    redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL FLUSHCONFIG
  fi
}

check_network_type_changes() {
  # Check if network type has changed
  if [[ -f $DATA_DIR/network_type ]]; then
    current_network_type=$(cat "$DATA_DIR/network_type")
    if [[ "$current_network_type" != "$NETWORKING_TYPE" ]]; then
      echo "Network type has changed from $current_network_type to $NETWORKING_TYPE"
      echo "$NETWORKING_TYPE" >"$DATA_DIR/network_type"
      # If network type has changed, rewrite config
      handle_network_type_changed
    fi
  else
    echo "Network type file not found, creating it"
    echo "$NETWORKING_TYPE" >"$DATA_DIR/network_type"
  fi
}

check_admin_password_change() {
  if [ -f $NODE_CONF_FILE ]; then
    # Get current admin password
    CURRENT_ADMIN_PASSWORD=$(awk '/^requirepass / {print $2}' $NODE_CONF_FILE | sed 's/\"//g')
    # If current admin password is different from the new one, reset it
    if [[ "$CURRENT_ADMIN_PASSWORD" != "$ADMIN_PASSWORD" ]]; then
      RESET_ADMIN_PASSWORD=1
    fi
  fi
}

ensure_node_conf_exists() {
  # If node.conf doesn't exist or $REPLACE_NODE_CONF=1, copy it from /falkordb
  if [ ! -f $NODE_CONF_FILE ] || [ "$REPLACE_NODE_CONF" -eq "1" ]; then
    echo "Copying node.conf from /falkordb"
    cp /falkordb/node.conf $NODE_CONF_FILE
  fi
}

fix_namespace_in_config_files() {
  # Use INSTANCE_ID environment variable to get the current namespace
  if [[ -n "$INSTANCE_ID" ]]; then
    echo "Current namespace: $INSTANCE_ID"
    
    # Check and fix node.conf only (node entrypoint should only check node.conf)
    if [[ -f "$NODE_CONF_FILE" ]]; then
      echo "Checking node.conf for namespace mismatches"
      # Replace instance-X pattern with current namespace, where X can contain hyphens, underscores, and alphanumeric characters
      sed -i -E "s/instance-[a-zA-Z0-9_\-]+/${INSTANCE_ID}/g" "$NODE_CONF_FILE"
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
      echo "Checking node.conf for DNS suffix mismatches"
      # Replace old DNS suffixes with current one
      # This regex matches the Omnistrate DNS suffix structure: hc-<ID>.<REGION>.<CLOUD>.<HASH>.<TLD>
      sed -i -E "s/([a-zA-Z0-9_-]*[a-zA-Z][a-zA-Z0-9_-]*)\.hc-[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.[a-f0-9]+\.[a-zA-Z]+/\1.${escaped_dns_suffix}/g" "$NODE_CONF_FILE"
    fi
  else
    echo "DNS_SUFFIX not set, skipping DNS suffix fix"
  fi
}

ensure_log_file_exists() {
  if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then
    if [ "$RUN_NODE" -eq "1" ]; then
      touch $FALKORDB_LOG_FILE_PATH
    fi
  fi
}

run_node() {
  # Update .SO path for old instances
  sed -i "s|/FalkorDB/bin/src/bin/falkordb.so|/var/lib/falkordb/bin/falkordb.so|g" $NODE_CONF_FILE
  sed -i "s/\$NODE_HOST/$NODE_HOST/g" $NODE_CONF_FILE
  sed -i "s/\$NODE_PORT/$NODE_PORT/g" $NODE_CONF_FILE
  sed -i "s/\$ADMIN_PASSWORD/$ADMIN_PASSWORD/g" $NODE_CONF_FILE
  sed -i "s/\$LOG_LEVEL/$LOG_LEVEL/g" $NODE_CONF_FILE
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
  echo "dir $DATA_DIR" >>$NODE_CONF_FILE

  if [[ "$LDAP_ENABLED" == "true" ]]; then
    add_ldap_config_to_conf
  fi

  is_replica
  if [[ $IS_REPLICA -eq 1 ]]; then
    if grep -q "^replicaof " "$NODE_CONF_FILE"; then
      sed -i "s|^replicaof .*|replicaof $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER|" "$NODE_CONF_FILE"
    else
      echo "replicaof $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER" >>"$NODE_CONF_FILE"
    fi
    echo "Starting Replica"
  else
    echo "Starting Master"
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
      echo "tls-replication yes" >>$NODE_CONF_FILE
      echo "tls-auth-clients optional" >>$NODE_CONF_FILE
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
      if grep -q "^tls-replication " "$NODE_CONF_FILE"; then
        sed -i "s|tls-replication .*|tls-replication yes|g" "$NODE_CONF_FILE"
      else
        echo "tls-replication yes" >>$NODE_CONF_FILE
      fi
      if grep -q "^tls-auth-clients " "$NODE_CONF_FILE"; then
        sed -i "s|tls-auth-clients .*|tls-auth-clients optional|g" "$NODE_CONF_FILE"
      else
        echo "tls-auth-clients optional" >>$NODE_CONF_FILE
      fi
    fi
  else
    if ! grep -q "^port $NODE_PORT" "$NODE_CONF_FILE"; then
      echo "port $NODE_PORT" >>$NODE_CONF_FILE
    fi
  fi

  redis-server $NODE_CONF_FILE --logfile $FALKORDB_LOG_FILE_PATH &
  falkordb_pid=$!
  tail -F $FALKORDB_LOG_FILE_PATH &
}

add_master_to_sentinel() {
  # If node should be master, add it to sentinel — but only if sentinel does not already
  # have a *different* node as master (which would mean a failover happened while this node
  # was restarting and we must not overwrite it).
  if [[ $IS_REPLICA -eq 0 && $RUN_SENTINEL -eq 1 ]]; then
    echo "Adding master to sentinel"
    wait_until_sentinel_host_resolves
    wait_until_node_host_resolves $NODE_HOST $NODE_PORT

    log "Master Name: $MASTER_NAME\nNode Host: $NODE_HOST\nNode Port: $NODE_PORT\nSentinel Quorum: $SENTINEL_QUORUM"
    res=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL monitor $MASTER_NAME $NODE_HOST $NODE_PORT $SENTINEL_QUORUM)
    if [[ $res == *"ERR"* && $res != *"Duplicate master name"* ]]; then
      echo "Could not add master to sentinel: $res"
      exit 1
    fi

    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME auth-pass $ADMIN_PASSWORD
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME failover-timeout $SENTINEL_FAILOVER
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME down-after-milliseconds $SENTINEL_DOWN_AFTER
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME parallel-syncs 1
  fi
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

post_start_configuration() {
  if [[ "$LDAP_ENABLED" == "true" ]]; then
    sync_ldap_server_url
  fi
  # Set maxmemory based on instance type
  get_memory_limit
  if [[ ! -z $MEMORY_LIMIT ]]; then
    echo "Setting maxmemory to $MEMORY_LIMIT"
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET maxmemory $MEMORY_LIMIT
  fi

  # Set persistence config
  echo "Setting persistence config: CONFIG SET save '$PERSISTENCE_RDB_CONFIG'"
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET save "$PERSISTENCE_RDB_CONFIG"

  if [[ $PERSISTENCE_AOF_CONFIG != "no" ]]; then
    echo "Setting AOF persistence: $PERSISTENCE_AOF_CONFIG"
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET appendonly yes
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET appendfsync $PERSISTENCE_AOF_CONFIG
  fi

  config_rewrite
}

create_tls_rotation_job_script() {
  if [[ "$TLS" == "true" && $RUN_NODE -eq 1 ]]; then
    echo "Creating node certificate rotation job script"
    cat >"$DATA_DIR/cert_rotate_node.sh" <<EOF
#!/bin/bash
set -e
echo 'Refreshing node certificate'
tls_ca_path="$BASE_ROOT_CA_PATH"
if [[ -f "$SELFSIGNED_CA_PATH" ]]; then
  cat "$BASE_ROOT_CA_PATH" "$SELFSIGNED_CA_PATH" > "$COMBINED_CA_PATH"
  tls_ca_path="$COMBINED_CA_PATH"
fi
TLS_CONNECTION_STRING="--tls --cacert \$tls_ca_path"
redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning \$TLS_CONNECTION_STRING CONFIG SET tls-cert-file $TLS_MOUNT_PATH/tls.crt
redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning \$TLS_CONNECTION_STRING CONFIG SET tls-key-file $TLS_MOUNT_PATH/tls.key
redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning \$TLS_CONNECTION_STRING CONFIG SET tls-client-cert-file $TLS_MOUNT_PATH/selfsigned-tls.crt
redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning \$TLS_CONNECTION_STRING CONFIG SET tls-client-key-file $TLS_MOUNT_PATH/selfsigned-tls.key
redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning \$TLS_CONNECTION_STRING CONFIG SET tls-auth-clients optional
redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning \$TLS_CONNECTION_STRING CONFIG SET tls-ca-cert-file \$tls_ca_path
EOF
    chmod +x $DATA_DIR/cert_rotate_node.sh
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
  check_admin_password_change
  ensure_node_conf_exists
  ensure_log_file_exists
  set_persistence_config
  get_self_host_ip
  fix_namespace_in_config_files

  if [ "$RUN_NODE" -eq "1" ]; then
    run_node
    wait_for_node_ready
    create_user
    add_master_to_sentinel
    post_start_configuration
    set_max_info_queries
  fi

  check_network_type_changes
  create_tls_rotation_job_script
  wait_forever
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
