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
FALKORDB_RESULT_SET_SIZE=${FALKORDB_RESULT_SET_SIZE:-10000}
FALKORDB_QUERY_MEM_CAPACITY=${FALKORDB_QUERY_MEM_CAPACITY:-0}
FALKORDB_TIMEOUT_MAX=${FALKORDB_TIMEOUT_MAX:-0}
FALKORDB_TIMEOUT_DEFAULT=${FALKORDB_TIMEOUT_DEFAULT:-0}
FALKORDB_VKEY_MAX_ENTITY_COUNT=${FALKORDB_VKEY_MAX_ENTITY_COUNT:-4611686000000000000}
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

DATE_NOW=$(date +"%Y%m%d%H%M%S")
FALKORDB_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/falkordb_$DATE_NOW.log; else echo ""; fi)
NODE_CONF_FILE=$DATA_DIR/node.conf


if [[ $OMNISTRATE_ENVIRONMENT_TYPE != "PROD" ]];then
  DEBUG=1
fi


meet_unknown_nodes(){
  # Had to add sleep until things are stable (nodes that can communicate should be given time to do so)
  sleep 60
  # Look for nodes that have 0@0 in the nodes.conf and meet them again"
  if [[ -f "$DATA_DIR/nodes.conf" && -s "$DATA_DIR/nodes.conf" ]];then
    discrepancy=0
    while IFS= read -r line;do
     #if [[ $line =~ .*@0.* || $line =~ .*fail.* ]];then
      if [[ ! $line =~ .*myself.* ]];then
        discrepancy=$(( $discrepancy + 1 ))
        hostname=$(echo $line | awk '{print $2}' | cut -d',' -f2| cut -d':' -f1)
        ip=$(getent hosts "$hostname" | awk '{print $1}')

        tout=$(( $(date +%s) + 300 ))
        while true;do
          if [[ $(date +%s) -gt $tout ]];then 
            echo "Timedout after 5 minutes while trying to ping $ip"
            exit 1
          fi
          sleep 10
          echo "pinging: $hostname"
          PONG=$(redis-cli -h $(echo $hostname | cut -d'.' -f1) $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING PING)

          if [[ -n $ip && $PONG == "PONG" ]];then
            break
          fi

        done

        redis-cli $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING CLUSTER MEET $ip $NODE_PORT
        echo "Found $discrepancy IP discrepancy in line: $line"

      fi

    done < "$DATA_DIR/nodes.conf"

  fi

  if [[ $discrepancy -eq 0 ]];then
    echo "Did not find IP discrepancies between nodes."
  fi

  return 0
}

update_ips_in_nodes_conf(){
  # Replace old ip with new one (external ip)
  if [[ -f "$DATA_DIR/nodes.conf" && -s "$DATA_DIR/nodes.conf" ]];then
    res=$(cat $DATA_DIR/nodes.conf | grep myself | awk '{print $2}' | cut -d',' -f1)
    external_ip=$(getent hosts $NODE_HOST | awk '{print $1}')
    if [[ -z $external_ip ]];then
      echo "Could not resolve hostname, trying again: $NODE_HOST"
      sleep 3
      update_ips_in_nodes_conf
      return
    fi
    echo "The old ip is: $res"
    echo "The new ip is: $external_ip"
    sed -i "s/$res/$external_ip:$NODE_PORT@1$NODE_PORT/" $DATA_DIR/nodes.conf
    cat $DATA_DIR/nodes.conf
  else
    echo "First time running the node.."
  fi
  return 0
}

update_ips_in_nodes_conf


handle_sigterm() {
  echo "Caught SIGTERM"
  echo "Stopping FalkorDB"

  if [[ ! -z $falkordb_pid ]]; then
    kill -TERM $falkordb_pid
  fi

  if [[ $RUN_METRICS -eq 1 && ! -z $redis_exporter_pid ]]; then
    kill -TERM $redis_exporter_pid
  fi

  if [[ $RUN_HEALTH_CHECK -eq 1 && ! -z $healthcheck_pid ]]; then
    kill -TERM $healthcheck_pid
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
  redis-cli -p $NODE_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER on ">$FALKORDB_PASSWORD" ~* +INFO +PING +HELLO +AUTH +RESTORE +DUMP +DEL +EXISTS +UNLINK +TYPE +FLUSHALL +TOUCH +EXPIRE +PEXPIREAT +TTL +PTTL +EXPIRETIME +RENAME +RENAMENX +SCAN +DISCARD +EXEC +MULTI +UNWATCH +WATCH +ECHO +SLOWLOG +WAIT +WAITAOF +GRAPH.INFO +GRAPH.LIST +GRAPH.QUERY +GRAPH.RO_QUERY +GRAPH.EXPLAIN +GRAPH.PROFILE +GRAPH.DELETE +GRAPH.CONSTRAINT +GRAPH.SLOWLOG +GRAPH.BULK +GRAPH.CONFIG +CLUSTER +COMMAND
}

get_default_memory_limit() {
  echo "$(awk '/MemTotal/ {printf "%d\n", (($2 / 1024 - 2330) > 100 ? ($2 / 1024 - 2330) : 100)}' /proc/meminfo)MB"
}

set_memory_limit() {
  declare -A memory_limit_instance_type_map
  memory_limit_instance_type_map=(
    ["e2-custom-small-1024"]="100MB"
    ["e2-medium"]="2GB"
    ["e2-custom-4-8192"]="6GB"
    ["e2-custom-8-16384"]="13GB"
    ["e2-custom-16-32768"]="30GB"
    ["e2-custom-32-65536"]="62GB"
    ["t2.medium"]="2GB"
    ["c6i.xlarge"]="6GB"
    ["c6i.2xlarge"]="13GB"
    ["c6i.4xlarge"]="30GB"
    ["c6i.8xlarge"]="62GB"
  )
  if [[ -z $INSTANCE_TYPE ]]; then
    echo "INSTANCE_TYPE is not set"
    MEMORY_LIMIT=$(get_default_memory_limit)
  fi

  instance_size_in_map=${memory_limit_instance_type_map[$INSTANCE_TYPE]}

  if [[ -n $instance_size_in_map && -z $MEMORY_LIMIT ]];then
    MEMORY_LIMIT=$instance_size_in_map
  elif [[ -z $instance_size_in_map && -z $MEMORY_LIMIT ]];then
    MEMORY_LIMIT=$(get_default_memory_limit)
    echo "INSTANCE_TYPE is not set. Setting to default memory limit"
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
    touch /data/cluster_initialized
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

config_rewrite

if [[ $NODE_INDEX -eq 0 && ! -f "/data/cluster_initialized" ]]; then
  # Create cluster
  echo "Creating cluster"
  create_cluster
elif [[ $NODE_INDEX -gt 5 ]]; then
  # Join cluster
  echo "Joining cluster"
  join_cluster
else
  echo "Cluster does not exist. Waiting for it to be created"
fi

# Run this before health check to prevent client connections until discrepancies are resolved.
meet_unknown_nodes

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
  redis_exporter -skip-tls-verification -redis.password $ADMIN_PASSWORD -redis.addr $exporter_url -log-format json -is-cluster -tls-server-min-version TLS1.3 >>$FALKORDB_LOG_FILE_PATH &
  redis_exporter_pid=$!
fi

while true; do
  sleep 1
done
