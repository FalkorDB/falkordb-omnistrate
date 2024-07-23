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
HOST_COUNT=${HOST_COUNT:-6}
CLUSTER_REPLICAS=${CLUSTER_REPLICAS:-1}

NODE_HOST=${NODE_HOST:-localhost}
NODE_PORT=${NODE_PORT:-6379}

FALKORDB_MASTER_HOST=''
ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/GlobalSign_Root_CA.pem}
TLS_MOUNT_PATH=${TLS_MOUNT_PATH:-/etc/tls}
DATA_DIR=${DATA_DIR:-/data}
DEBUG=${DEBUG:-0}
REPLACE_NODE_CONF=${REPLACE_NODE_CONF:-0}
TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
AUTH_CONNECTION_STRING="-a $ADMIN_PASSWORD --no-auth-warning"
SAVE_LOGS_TO_FILE=${SAVE_LOGS_TO_FILE:-1}
LOG_LEVEL=${LOG_LEVEL:-notice}

DATE_NOW=$(date +"%Y%m%d%H%M%S")
FALKORDB_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/falkordb_$DATE_NOW.log; else echo ""; fi)
NODE_CONF_FILE=$DATA_DIR/node.conf

log() {
  if [[ $DEBUG -eq 1 ]]; then
    echo $1
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

get_host() {
  local host_idx=$1
  # substitute -0 from NODE_HOST with host_idx
  echo $NODE_HOST | sed "s/-$NODE_INDEX/-$host_idx/g"
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
        sleep 10
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

check_if_cluster_exists_in_host() {
  local host=$1
  local port=$2

  log "Checking if cluster exists on $host:$port"
  wait_until_node_host_resolves $host $port

  local cluster_info=$(redis-cli -h $host -p $port $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CLUSTER INFO)
  local cluster_info_code=$?

  log "Cluster Info: $cluster_info_code - $cluster_info"

  if [[ $cluster_info_code -eq 0 ]] && [[ $cluster_info =~ "cluster_known_nodes:" ]]; then
    echo "Cluster exists on $host"
    return 0
  else
    echo "Cluster does not exist on $host"
    return 1
  fi
}

check_if_cluster_exists() {
  local checks=$1
  local checked=0

  for i in $(seq 0 $(($HOST_COUNT - 1))); do
    
    if [[ $i -eq $NODE_INDEX ]]; then
      log "Skipping self"
      continue
    fi

    local host=$(get_host $i)
    local port=$NODE_PORT
    log "Checking if cluster exists on $host:$port"
    check_if_cluster_exists_in_host $host $port
    checked=$(($checked + 1))
    if [[ $? -eq 0 ]]; then
      return 0
    fi
    if [[ $checked -eq $checks ]]; then
      break
    fi
  done

  return 1

}

create_cluster() {

  local urls=""

  for host in $(seq 0 $(($HOST_COUNT - 1))); do
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

  local node_idx=0

  while true; do
    local host=$(get_host $node_idx)
    local port=$NODE_PORT
    check_if_cluster_exists_in_host $host $port
    if [[ $? -eq 0 ]]; then
      break
    fi
    node_idx=$(($node_idx + 1))
  done

  local host=$(get_host $node_idx)
  local port=$(($START_PORT + $node_idx))

  echo "Joining cluster on $host:$port"

  redis-cli --cluster add-node $NODE_HOST:$NODE_PORT $host:$port $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING

  touch /data/cluster_initialized

}

run_node() {
  local node_idx=$1

  sed -i "s/\$ADMIN_PASSWORD/$ADMIN_PASSWORD/g" $NODE_CONF_FILE
  sed -i "s/\$LOG_LEVEL/$LOG_LEVEL/g" $NODE_CONF_FILE
  echo "dir $DATA_DIR/$i" >> $NODE_CONF_FILE

  if [[ $TLS == "true" ]]; then
    echo "port 0" >> $NODE_CONF_FILE
    echo "tls-port $NODE_PORT" >> $NODE_CONF_FILE
    echo "tls-cert-file $TLS_MOUNT_PATH/tls.crt" >> $NODE_CONF_FILE
    echo "tls-key-file $TLS_MOUNT_PATH/tls.key" >> $NODE_CONF_FILE
    echo "tls-ca-cert-file $ROOT_CA_PATH" >> $NODE_CONF_FILE
    echo "tls-cluster yes" >> $NODE_CONF_FILE
    echo "tls-auth-clients no" >> $NODE_CONF_FILE
  else
    echo "port $NODE_PORT" >> $NODE_CONF_FILE
  fi

  redis-server $NODE_CONF_FILE --logfile $FALKORDB_LOG_FILE_PATH &
  falkordb_pids="$falkordb_pids $!"
  tail -f $FALKORDB_LOG_FILE_PATH &
}

# If node.conf doesn't exist or $REPLACE_NODE_CONF=1, copy it from /falkordb
if [ ! -f $NODE_CONF_FILE ] || [ "$REPLACE_NODE_CONF" -eq "1" ]; then
  echo "Copying node.conf from /falkordb"
  cp /falkordb/node.conf $NODE_CONF_FILE
fi

# Create log file
touch $FALKORDB_LOG_FILE_PATH

set_persistence_config

run_node

# Check if cluster exist on any other host. 
# If it doesn't exist, and it's node 0, create one. If not, wait for it to be created
# If it does exist, join the cluster

check_if_cluster_exists 2 
cluster_exists=$?

if [[ $cluster_exists -eq 0 && $NODE_INDEX -eq 0 && ! -f "/data/cluster_initialized" ]]; then
  # Create cluster
  echo "Creating cluster"
  create_cluster
elif [[ $cluster_exists -eq 0 && ! -f "/data/cluster_initialized" ]]; then
  # Join cluster
  echo "Joining cluster"
  join_cluster
else
  echo "Cluster does not exist. Waiting for it to be created"
fi


if [[ $RUN_METRICS -eq 1 ]]; then
  echo "Starting Metrics"
  exporter_url=$(if [[ $TLS == "true" ]]; then echo "rediss://$NODE_HOST:$NODE_PORT"; else echo "redis://$NODE_HOST:$NODE_PORT"; fi)
  redis_exporter -skip-tls-verification -redis.password $ADMIN_PASSWORD -redis.addr $exporter_url &
  redis_exporter_pid=$!
fi

if [[ $RUN_HEALTH_CHECK -eq 1 ]]; then
  # Check if healthcheck binary exists
  if [ -f /usr/local/bin/healthcheck ]; then
    echo "Starting Healthcheck"
    healthcheck &
    healthcheck_pid=$!
  else
    echo "Healthcheck binary not found"
  fi
fi


while true; do
  sleep 1
done