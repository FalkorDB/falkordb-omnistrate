#!/bin/bash

FALKORDB_USER=${FALKORDB_USER:-falkordb}
#FALKORDB_PASSWORD=${FALKORDB_PASSWORD:-''}
if [[ -f "/run/secrets/falkordbpassword" ]] && [[ -s "/run/secrets/falkordbpassword" ]]; then
  FALKORDB_PASSWORD=$(cat "/run/secrets/falkordbpassword")
elif [[ -n "$FALKORDB_PASSWORD" ]]; then
  FALKORDB_PASSWORD=$FALKORDB_PASSWORD
else
  FALKORDB_PASSWORD=''
fi

#ADMIN_PASSWORD=${ADMIN_PASSWORD:-''}
if [[ -f "/run/secrets/adminpassword" ]] && [[ -s "/run/secrets/adminpassword" ]]; then
  ADMIN_PASSWORD=$(cat "/run/secrets/adminpassword")
  export ADMIN_PASSWORD
elif [[ -n "$ADMIN_PASSWORD" ]]; then
  export ADMIN_PASSWORD=$ADMIN_PASSWORD
else
  export ADMIN_PASSWORD=''
fi

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
# If vars are <nil>, set it to 0
if [[ "$FALKORDB_QUERY_MEM_CAPACITY" == "<nil>" ]]; then
  FALKORDB_QUERY_MEM_CAPACITY=0
fi
if [[ "$FALKORDB_TIMEOUT_MAX" == "<nil>" ]]; then
  FALKORDB_TIMEOUT_MAX=0
fi
if [[ "$FALKORDB_TIMEOUT_DEFAULT" == "<nil>" ]]; then
  FALKORDB_TIMEOUT_DEFAULT=0
fi

SENTINEL_PORT=${SENTINEL_PORT:-26379}
SENTINEL_DOWN_AFTER=${SENTINEL_DOWN_AFTER:-30000}
SENTINEL_FAILOVER=${SENTINEL_FAILOVER:-180000}

# SENTINEL_HOST=${SENTINEL_HOST:-localhost}
SENTINEL_HOST=sentinel-$(echo $RESOURCE_ALIAS | cut -d "-" -f 2)-0.$LOCAL_DNS_SUFFIX

NODE_HOST=${NODE_HOST:-localhost}
NODE_PORT=${NODE_PORT:-6379}
MASTER_NAME=${MASTER_NAME:-master}
SENTINEL_QUORUM=${SENTINEL_QUORUM:-2}

FALKORDB_MASTER_HOST=''
FALKORDB_MASTER_PORT_NUMBER=${MASTER_PORT:-6379}
IS_REPLICA=${IS_REPLICA:-0}
ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/ca-certificates.crt}
TLS_MOUNT_PATH=${TLS_MOUNT_PATH:-/etc/tls}
DATA_DIR=${DATA_DIR:-"${FALKORDB_HOME}/data"}

# Add backward compatibility for /data folder
if [[ "$DATA_DIR" != '/data' ]]; then
  mkdir -p $DATA_DIR
  if [[ -d '/data' ]]; then
    # create simlink
    ln -s /data $DATA_DIR
  fi
fi

if [[ $(basename "$DATA_DIR") != 'data' ]];then DATA_DIR=$DATA_DIR/data;fi

DEBUG=${DEBUG:-0}
REPLACE_NODE_CONF=${REPLACE_NODE_CONF:-0}
TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
AUTH_CONNECTION_STRING="-a $ADMIN_PASSWORD --no-auth-warning"
SAVE_LOGS_TO_FILE=${SAVE_LOGS_TO_FILE:-1}
LOG_LEVEL=${LOG_LEVEL:-notice}

DATE_NOW=$(date +"%Y%m%d%H%M%S")


FALKORDB_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/falkordb_$DATE_NOW.log; else echo "/dev/null"; fi)
NODE_CONF_FILE=$DATA_DIR/node.conf
AOF_FILE_SIZE_TO_MONITOR=${AOF_FILE_SIZE_TO_MONITOR:-5} # 5MB

if [[ $OMNISTRATE_ENVIRONMENT_TYPE != "PROD" ]]; then
  DEBUG=1
fi


echo "Creating run_bgrewriteaof script"
echo "
    #!/bin/bash
    set -e
    AOF_FILE_SIZE_TO_MONITOR=\${AOF_FILE_SIZE_TO_MONITOR:-5}
    ROOT_CA_PATH=\${ROOT_CA_PATH:-/etc/ssl/certs/ca-certificates.crt}
    TLS_CONNECTION_STRING=$(if [[ \$TLS == "true" ]]; then echo "--tls --cacert \$ROOT_CA_PATH"; else echo ""; fi)
    size=0
    for file in $DATA_DIR/appendonlydir/appendonly.aof.*.incr.aof; do
      if [[ -f \"\$file\" ]]; then
        size=\$((size + \$(stat -c%s \"\$file\")))
      fi
    done
    if [ \$size -gt \$((AOF_FILE_SIZE_TO_MONITOR * 1024 * 1024)) ]; then
      echo \"File larger than \$AOF_FILE_SIZE_TO_MONITOR MB, running BGREWRITEAOF\"
      $(which redis-cli) -a \$(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING BGREWRITEAOF
    else
      echo \"File smaller than \$AOF_FILE_SIZE_TO_MONITOR MB, not running BGREWRITEAOF\"
    fi
    " > "$DATA_DIR/run_bgrewriteaof"
chmod +x "$DATA_DIR/run_bgrewriteaof"
ln -s "$DATA_DIR/run_bgrewriteaof" $FALKORDB_HOME/run_bgrewriteaof
echo "run_bgrewriteaof script created"


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

# Handle signals
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
  # sentinels_list=$(get_sentinels_list)

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

trap handle_sigterm SIGTERM

log() {
  if [[ $DEBUG -eq 1 ]]; then
    echo $1
  fi
}

get_self_host_ip() {
  if [[ $NODE_HOST == "localhost" ]]; then
    NODE_HOST_IP=$(curl ifconfig.me)
  else
    NODE_HOST_IP=$(getent hosts $NODE_HOST | awk '{ print $1 }')
    if [[ -z $NODE_HOST_IP ]]; then
      NODE_HOST_IP=$(curl ifconfig.me)
    fi
  fi
}

get_default_memory_limit() {
  echo "$(awk '/MemTotal/ {printf "%d\n", (($2 / 1024 - 2330) > 100 ? ($2 / 1024 - 2330) : 100)}' /proc/meminfo)MB"
}

get_memory_limit() {

  declare -A memory_limit_instance_type_map
  memory_limit_instance_type_map=(
    ["e2-standard-2"]="6GB"
    ["e2-standard-4"]="14GB"
    ["e2-custom-small-1024"]="100MB"
    ["e2-medium"]="2GB"
    ["e2-custom-4-8192"]="6GB"
    ["e2-custom-8-16384"]="13GB"
    ["e2-custom-16-32768"]="30GB"
    ["e2-custom-32-65536"]="62GB"
    ["t2.medium"]="2GB"
    ["m6i.large"]="6GB"
    ["m6i.xlarge"]="14GB"
    ["c6i.xlarge"]="6GB"
    ["c6i.2xlarge"]="13GB"
    ["c6i.4xlarge"]="30GB"
    ["c6i.8xlarge"]="62GB"
  )

  # if memory limit is 1200M or 2200M, set it to 1GB or 2GB respectively
  if [[ $MEMORY_LIMIT == *"M" ]]; then
    if [[ $MEMORY_LIMIT == "1200M" ]]; then
      MEMORY_LIMIT="1G"
    elif [[ $MEMORY_LIMIT == "2200M" ]]; then
      MEMORY_LIMIT="2G"
    fi
  fi

  if [[ -z $INSTANCE_TYPE && -z $MEMORY_LIMIT ]]; then
    echo "INSTANCE_TYPE is not set"
    MEMORY_LIMIT=$(get_default_memory_limit)
  fi

  instance_size_in_map=${memory_limit_instance_type_map[$INSTANCE_TYPE]}

  if [[ -n $instance_size_in_map && -z $MEMORY_LIMIT ]]; then
    MEMORY_LIMIT=$instance_size_in_map
  elif [[ -z $instance_size_in_map && -z $MEMORY_LIMIT ]]; then
    MEMORY_LIMIT=$(get_default_memory_limit)
    echo "INSTANCE_TYPE is not set. Setting to default memory limit"
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
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER on ">$FALKORDB_PASSWORD" ~* +INFO +CLIENT +DBSIZE +PING +HELLO +AUTH +DUMP +DEL +EXISTS +UNLINK +TYPE +FLUSHALL +TOUCH +EXPIRE +PEXPIREAT +TTL +PTTL +EXPIRETIME +RENAME +RENAMENX +SCAN +DISCARD +EXEC +MULTI +UNWATCH +WATCH +ECHO +SLOWLOG +WAIT +WAITAOF +READONLY +GRAPH.INFO +GRAPH.LIST +GRAPH.QUERY +GRAPH.RO_QUERY +GRAPH.EXPLAIN +GRAPH.PROFILE +GRAPH.DELETE +GRAPH.CONSTRAINT +GRAPH.SLOWLOG +GRAPH.BULK +GRAPH.CONFIG +GRAPH.COPY +GRAPH.MEMORY +MEMORY +BGREWRITEAOF '+MODULE|LIST'
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

if [ -f $NODE_CONF_FILE ]; then
  # Get current admin password
  CURRENT_ADMIN_PASSWORD=$(awk '/^requirepass / {print $2}' $NODE_CONF_FILE | sed 's/\"//g')
  # If current admin password is different from the new one, reset it
  if [[ "$CURRENT_ADMIN_PASSWORD" != "$ADMIN_PASSWORD" ]]; then
    RESET_ADMIN_PASSWORD=1
  fi
fi

# If node.conf doesn't exist or $REPLACE_NODE_CONF=1, copy it from /falkordb
if [ ! -f $NODE_CONF_FILE ] || [ "$REPLACE_NODE_CONF" -eq "1" ]; then
  echo "Copying node.conf from /falkordb"
  cp /falkordb/node.conf $NODE_CONF_FILE
fi

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
  if [[ -n "$LOCAL_DNS_SUFFIX" ]]; then
    echo "Current DNS suffix: $LOCAL_DNS_SUFFIX"
    
    # Escape dots in LOCAL_DNS_SUFFIX for safe use in sed replacement string
    # DNS suffixes primarily contain dots, hyphens, and alphanumeric characters
    local escaped_dns_suffix=$(echo "$LOCAL_DNS_SUFFIX" | sed 's/\./\\./g')
    
    # Check and fix node.conf
    if [[ -f "$NODE_CONF_FILE" ]]; then
      echo "Checking node.conf for DNS suffix mismatches"
      # Replace old DNS suffixes with current one for specific configuration parameters
      # This regex matches hostnames that have a multi-segment domain suffix (e.g., .svc.cluster.local, .namespace.svc.cluster.local)
      # and replaces the suffix while keeping the hostname part intact
      # Pattern: captures hostname (must contain at least one letter to avoid matching IPs), 
      # then replaces any .word.word or longer suffix with the current DNS suffix
      sed -i -E "s/([a-zA-Z0-9_-]*[a-zA-Z][a-zA-Z0-9_-]*)\.(([a-zA-Z0-9_-]+\.)+[a-zA-Z0-9_-]+)/\1.${escaped_dns_suffix}/g" "$NODE_CONF_FILE"
    fi
  else
    echo "LOCAL_DNS_SUFFIX not set, skipping DNS suffix fix"
  fi
}

# Create log files if they don't exist
if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then
  if [ "$RUN_NODE" -eq "1" ]; then
    touch $FALKORDB_LOG_FILE_PATH
  fi
fi

set_persistence_config
get_self_host_ip

# Fix namespace in config files before starting the server
# This must be called after node.conf is created/copied but before server starts
fix_namespace_in_config_files

if [ "$RUN_NODE" -eq "1" ]; then

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
  echo "dir $DATA_DIR" >>$NODE_CONF_FILE

  is_replica
  if [[ $IS_REPLICA -eq 1 ]]; then
    if ! grep -q "^replicaof " "$NODE_CONF_FILE"; then
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
      echo "tls-ca-cert-file $ROOT_CA_PATH" >>$NODE_CONF_FILE
      echo "tls-replication yes" >>$NODE_CONF_FILE
      echo "tls-auth-clients no" >>$NODE_CONF_FILE
    fi
  else
    if ! grep -q "^port $NODE_PORT" "$NODE_CONF_FILE"; then
      echo "port $NODE_PORT" >>$NODE_CONF_FILE
    fi
  fi

  redis-server $NODE_CONF_FILE --logfile $FALKORDB_LOG_FILE_PATH &
  falkordb_pid=$!
  tail -F $FALKORDB_LOG_FILE_PATH &

  sleep 10

  create_user

  # If node should be master, add it to sentinel
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
fi

set_max_info_queries
check_network_type_changes

# If TLS=true, create a script to rotate the certificate
if [[ "$TLS" == "true" ]]; then
  if [[ $RUN_NODE -eq 1 ]]; then
    echo "Creating node certificate rotation job script"
    echo "
    #!/bin/bash
    set -e
    echo 'Refreshing node certificate'
    redis-cli -p $NODE_PORT -a \$(cat /run/secrets/adminpassword) --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET tls-cert-file $TLS_MOUNT_PATH/tls.crt
    " >$DATA_DIR/cert_rotate_node.sh
    chmod +x $DATA_DIR/cert_rotate_node.sh
  fi
fi

while true; do
  sleep 1
done
