#!/bin/bash

FALKORDB_USER=${FALKORDB_USER:-falkordb}
FALKORDB_PASSWORD=${FALKORDB_PASSWORD:-''}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-''}
RUN_METRICS=${RUN_METRICS:-1}
RUN_HEALTH_CHECK=${RUN_HEALTH_CHECK:-1}
TLS=${TLS:-false}
NODE_INDEX=${NODE_INDEX:-0}
INSTANCE_TYPE=${INSTANCE_TYPE:-''}
PERSISTENCE_RDB_CONFIG_INPUT=${PERSISTENCE_RDB_CONFIG_INPUT:-'low'}
PERSISTENCE_RDB_CONFIG=${PERSISTENCE_RDB_CONFIG:-'86400 1 21600 100 3600 10000'}
PERSISTENCE_AOF_CONFIG=${PERSISTENCE_AOF_CONFIG:-'everysec'}
FALKORDB_CACHE_SIZE=${FALKORDB_CACHE_SIZE:-25}
FALKORDB_NODE_CREATION_BUFFER=${FALKORDB_NODE_CREATION_BUFFER:-16384}
FALKORDB_MAX_QUEUED_QUERIES=${FALKORDB_MAX_QUEUED_QUERIES:-50}
FALKORDB_TIMEOUT_MAX=${FALKORDB_TIMEOUT_MAX:-0}
FALKORDB_TIMEOUT_DEFAULT=${FALKORDB_TIMEOUT_DEFAULT:-0}
FALKORDB_RESULT_SET_SIZE=${FALKORDB_RESULT_SET_SIZE:-10000}
FALKORDB_QUERY_MEM_CAPACITY=${FALKORDB_QUERY_MEM_CAPACITY:-0}
CLUSTER_REPLICAS=${CLUSTER_REPLICAS:-1}
IS_MULTI_ZONE=${IS_MULTI_ZONE:-0}

NODE_HOST=${NODE_HOST:-localhost}
NODE_PORT=${NODE_PORT:-6379}

ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/GlobalSign_Root_CA.pem}
TLS_MOUNT_PATH=${TLS_MOUNT_PATH:-/etc/tls}
DATA_DIR=${DATA_DIR:-/data}
DEBUG=${DEBUG:-0}
REPLACE_NODE_CONF=${REPLACE_NODE_CONF:-0}
TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
AUTH_CONNECTION_STRING="-a $ADMIN_PASSWORD --no-auth-warning"
SAVE_LOGS_TO_FILE=${SAVE_LOGS_TO_FILE:-1}
LOG_LEVEL=${LOG_LEVEL:-notice}
RESOURCE_ALIAS=${RESOURCE_ALIAS:-""}
EXTERNAL_DNS_SUFFIX=${EXTERNAL_DNS_SUFFIX:-""}

DATE_NOW=$(date +"%Y%m%d%H%M%S")
FALKORDB_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/falkordb_$DATE_NOW.log; else echo ""; fi)
NODE_CONF_FILE=$DATA_DIR/node.conf

handle_sigterm() {
  echo "Caught SIGTERM"
  echo "Stopping FalkorDB"

  if [[ ! -z $falkordb_pid ]]; then
    kill -TERM $falkordb_pid
  fi
}

trap handle_sigterm SIGTERM

log() {
  if [[ $DEBUG -eq 1 ]]; then
    echo $1
  fi
}

get_host() {
  local host_idx=$1
  echo "$RESOURCE_ALIAS-$host_idx.$EXTERNAL_DNS_SUFFIX"
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
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER on ">$FALKORDB_PASSWORD" ~* +INFO +PING +HELLO +AUTH +RESTORE +DUMP +DEL +EXISTS +UNLINK +TYPE +FLUSHALL +TOUCH +EXPIRE +PEXPIREAT +TTL +PTTL +EXPIRETIME +RENAME +RENAMENX +SCAN +DISCARD +EXEC +MULTI +UNWATCH +WATCH +ECHO +SLOWLOG +WAIT +WAITAOF +GRAPH.INFO +GRAPH.LIST +GRAPH.QUERY +GRAPH.RO_QUERY +GRAPH.EXPLAIN +GRAPH.PROFILE +GRAPH.DELETE +GRAPH.CONSTRAINT +GRAPH.SLOWLOG +GRAPH.BULK +GRAPH.CONFIG +CLUSTER +COMMAND
}

set_memory_limit() {
  declare -A memory_limit_instance_type_map
  memory_limit_instance_type_map=(
    ["e2-custom-small-1024"]="100MB"
    ["e2-custom-4-8192"]="6GB"
    ["e2-custom-8-16384"]="13GB"
    ["e2-custom-16-32768"]="30GB"
    ["e2-custom-32-65536"]="62GB"
  )
  if [[ -z $INSTANCE_TYPE ]]; then
    echo "INSTANCE_TYPE is not set"
    return
  fi

  memory_limit=$(echo $memory_limit_instance_type_map | jq -r ".\"$INSTANCE_TYPE\"")

  if [[ ! -z $memory_limit ]]; then
    echo "Setting maxmemory to $memory_limit"
    redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CONFIG SET maxmemory $MEMORY_LIMIT
  fi
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
    touch /data/cluster_initialized
  fi
}

join_cluster() {

  local cluster_host=$(get_host 0)

  wait_for_hosts "$cluster_host:$NODE_PORT $NODE_HOST:$NODE_PORT"

  echo "Joining cluster on $cluster_host:$NODE_PORT"

  redis-cli --cluster add-node $NODE_HOST:$NODE_PORT $cluster_host:$NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING

  touch /data/cluster_initialized
}

run_node() {

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
  echo "dir $DATA_DIR/$i" >>$NODE_CONF_FILE

  if [[ $TLS == "true" ]]; then
    echo "port 0" >>$NODE_CONF_FILE
    echo "tls-port $NODE_PORT" >>$NODE_CONF_FILE
    echo "tls-cert-file $TLS_MOUNT_PATH/tls.crt" >>$NODE_CONF_FILE
    echo "tls-key-file $TLS_MOUNT_PATH/tls.key" >>$NODE_CONF_FILE
    echo "tls-ca-cert-file $ROOT_CA_PATH" >>$NODE_CONF_FILE
    echo "tls-cluster yes" >>$NODE_CONF_FILE
    echo "tls-auth-clients no" >>$NODE_CONF_FILE
  else
    echo "port $NODE_PORT" >>$NODE_CONF_FILE
  fi

  redis-server $NODE_CONF_FILE --logfile $FALKORDB_LOG_FILE_PATH &
  falkordb_pid=$!
  tail -F $FALKORDB_LOG_FILE_PATH &
}

# If node.conf doesn't exist or $REPLACE_NODE_CONF=1, copy it from /falkordb
if [ ! -f $NODE_CONF_FILE ] || [ "$REPLACE_NODE_CONF" -eq "1" ]; then
  echo "Copying node.conf from /falkordb"
  cp /falkordb/node.conf $NODE_CONF_FILE
fi

# Create log file
touch $FALKORDB_LOG_FILE_PATH

run_node

sleep 10

create_user
set_memory_limit
set_rdb_persistence_config
set_aof_persistence_config

if [[ $NODE_INDEX -eq 0 && ! -f "/data/cluster_initialized" ]]; then
  # Create cluster
  echo "Creating cluster"
  create_cluster
elif [[ $NODE_INDEX -gt $CLUSTER_REPLICAS && ! -f "/data/cluster_initialized" ]]; then
  # Join cluster
  echo "Joining cluster"
  join_cluster
else
  echo "Cluster does not exist. Waiting for it to be created"
fi

if [[ $RUN_HEALTH_CHECK -eq 1 ]]; then
  # Check if healthcheck binary exists
  if [ -f /usr/local/bin/healthcheck ]; then
    echo "Starting Healthcheck"
    healthcheck | awk '{ print "**HEALTHCHECK**: " $0 }' >>$FALKORDB_LOG_FILE_PATH &
  else
    echo "Healthcheck binary not found"
  fi
fi

if [[ $RUN_METRICS -eq 1 ]]; then
  echo "Starting Metrics"
  exporter_url=$(if [[ $TLS == "true" ]]; then echo "rediss://$NODE_HOST:$NODE_PORT"; else echo "redis://localhost:$NODE_PORT"; fi)
  redis_exporter -skip-tls-verification -redis.password $ADMIN_PASSWORD -redis.addr $exporter_url -log-format json -is-cluster | awk '{ print "**EXPORTER**: " $0 }' >>$FALKORDB_LOG_FILE_PATH &
fi

while true; do
  sleep 1
done
