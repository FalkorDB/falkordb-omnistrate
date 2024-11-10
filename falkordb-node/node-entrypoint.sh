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

RUN_SENTINEL=${RUN_SENTINEL:-0}
RUN_NODE=${RUN_NODE:-1}
RUN_METRICS=${RUN_METRICS:-1}
RUN_HEALTH_CHECK=${RUN_HEALTH_CHECK:-1}
RUN_HEALTH_CHECK_SENTINEL=${RUN_HEALTH_CHECK_SENTINEL:-1}
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
SENTINEL_DOWN_AFTER=${SENTINEL_DOWN_AFTER:-1000}
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
ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/GlobalSign_Root_CA.pem}
TLS_MOUNT_PATH=${TLS_MOUNT_PATH:-/etc/tls}
DATA_DIR=${DATA_DIR:-/data}
DEBUG=${DEBUG:-0}
REPLACE_NODE_CONF=${REPLACE_NODE_CONF:-0}
REPLACE_SENTINEL_CONF=${REPLACE_SENTINEL_CONF:-0}
TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
SAVE_LOGS_TO_FILE=${SAVE_LOGS_TO_FILE:-1}
LOG_LEVEL=${LOG_LEVEL:-notice}

DATE_NOW=$(date +"%Y%m%d%H%M%S")

FALKORDB_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/falkordb_$DATE_NOW.log; else echo ""; fi)
SENTINEL_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/sentinel_$DATE_NOW.log; else echo ""; fi)
NODE_CONF_FILE=$DATA_DIR/node.conf
SENTINEL_CONF_FILE=$DATA_DIR/sentinel.conf

if [[ $OMNISTRATE_ENVIRONMENT_TYPE != "PROD" ]]; then
  DEBUG=1
fi

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
    redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL failover $MASTER_NAME
    sleep 5
    tries=5
    while true; do
      master_info=$(redis-cli -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING info replication | grep role)
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

  sentinels_list=$(redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL sentinels $MASTER_NAME)
  echo $sentinels_list >/tmp/sentinels_list.txt
  sentinels_list=$(IFS=' ' read -r -a sentinels <<<$(cat /tmp/sentinels_list.txt))
  sentinels_count=$(echo -n ${sentinels[@]} | grep -Fo name | wc -l)
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

handle_sigterm() {
  echo "Caught SIGTERM"
  echo "Stopping FalkorDB"
  # sentinels_list=$(get_sentinels_list)

  if [[ $RUN_NODE -eq 1 && ! -z $falkordb_pid ]]; then
    #DO NOT USE is_replica FUNCTION
    role=$(redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING info replication | grep role)
    
    if [[ "$role" =~ ^role:master ]];then IS_REPLICA=0 ;fi
    echo "skipped"
    remove_master_from_group
  fi

  if [[ $RUN_SENTINEL -eq 1 && ! -z $sentinel_pid ]]; then
    redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL FLUSHCONFIG
    redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SHUTDOWN
  fi

  if [[ $RUN_METRICS -eq 1 && ! -z $redis_exporter_pid ]]; then
    kill -TERM $redis_exporter_pid
  fi

  if [[ $RUN_HEALTH_CHECK -eq 1 && ! -z $healthcheck_pid ]]; then
    kill -TERM $healthcheck_pid
  fi

  if [[ $RUN_HEALTH_CHECK_SENTINEL -eq 1 && ! -z $sentinel_healthcheck_pid ]]; then
    kill -TERM $sentinel_healthcheck_pid
  fi
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

get_memory_limit() {

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
    return
  fi

  instance_size_in_map=${memory_limit_instance_type_map[$INSTANCE_TYPE]}

  if [[ -n $instance_size_in_map && -z $MEMORY_LIMIT ]];then
    MEMORY_LIMIT=$instance_size_in_map
  elif [[ -z $instance_size_in_map && -z $MEMORY_LIMIT ]];then
    echo "INSTANCE_TYPE is not set. Setting 100MB"
    MEMORY_LIMIT="100MB"
  fi

  echo "Memory Limit: $MEMORY_LIMIT"
}

wait_until_sentinel_host_resolves() {
  while true; do
    log "Checking if sentinel host resolves $SENTINEL_HOST"
    if [[ $(getent hosts $SENTINEL_HOST) ]]; then
      sentinel_response=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL masters)
      sentinel_response_code=$?
      log "Sentinel Response: $sentinel_response_code - $sentinel_response"
      if [[ $? -eq 0 ]] && [[ $sentinel_response != *"ERR"* ]]; then
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
      host_response=$(redis-cli -h $1 -p $2 -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING PING)
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

get_master() {
  master_info=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD $TLS_CONNECTION_STRING --no-auth-warning SENTINEL get-master-addr-by-name $MASTER_NAME)
  redisRetVal=$?
  echo "Master Info: $master_info"

  # If RUN_SENTINEL is 1 and could not connect to sentinel, wait and try again
  if [[ $RUN_SENTINEL -eq 1 && $redisRetVal -ne 0 ]]; then
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

create_user() {
  echo "Creating falkordb user"

  if [[ $RESET_ADMIN_PASSWORD -eq 1 ]]; then
    echo "Resetting admin password"
    redis-cli -p $NODE_PORT -a $CURRENT_ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET requirepass $ADMIN_PASSWORD
    redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET masterauth $ADMIN_PASSWORD

  fi

  redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER on ">$FALKORDB_PASSWORD" ~* +INFO +PING +HELLO +AUTH +RESTORE +DUMP +DEL +EXISTS +UNLINK +TYPE +FLUSHALL +TOUCH +EXPIRE +PEXPIREAT +TTL +PTTL +EXPIRETIME +RENAME +RENAMENX +SCAN +DISCARD +EXEC +MULTI +UNWATCH +WATCH +ECHO +SLOWLOG +WAIT +WAITAOF +GRAPH.INFO +GRAPH.LIST +GRAPH.QUERY +GRAPH.RO_QUERY +GRAPH.EXPLAIN +GRAPH.PROFILE +GRAPH.DELETE +GRAPH.CONSTRAINT +GRAPH.SLOWLOG +GRAPH.BULK +GRAPH.CONFIG

  config_rewrite
}

config_rewrite() {
  # Config rewrite
  echo "Rewriting config"
  redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG REWRITE
}

if [ -f $NODE_CONF_FILE ]; then
  # Get current admin password
  CURRENT_ADMIN_PASSWORD=$(cat $NODE_CONF_FILE | grep -oP '(?<=requirepass ).*' | sed 's/\"//g')
  # If current admin password is different from the new one, reset it
  if [[ $CURRENT_ADMIN_PASSWORD != $ADMIN_PASSWORD ]]; then
    RESET_ADMIN_PASSWORD=1
  fi
fi

# If node.conf doesn't exist or $REPLACE_NODE_CONF=1, copy it from /falkordb
if [ ! -f $NODE_CONF_FILE ] || [ "$REPLACE_NODE_CONF" -eq "1" ]; then
  echo "Copying node.conf from /falkordb"
  cp /falkordb/node.conf $NODE_CONF_FILE
fi

# If sentinel.conf doesn't exist or $REPLACE_SENTINEL_CONF=1, copy it from /falkordb
if [ ! -f $SENTINEL_CONF_FILE ] || [ "$REPLACE_SENTINEL_CONF" -eq "1" ]; then
  echo "Copying sentinel.conf from /falkordb"
  cp /falkordb/sentinel.conf $SENTINEL_CONF_FILE
fi

# Create log files if they don't exist
if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then
  if [ "$RUN_NODE" -eq "1" ]; then
    touch $FALKORDB_LOG_FILE_PATH
  fi
  if [ "$RUN_SENTINEL" -eq "1" ]; then
    touch $SENTINEL_LOG_FILE_PATH
  fi
fi

set_persistence_config
get_self_host_ip

if [ "$RUN_NODE" -eq "1" ]; then

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
  echo "dir $DATA_DIR" >>$NODE_CONF_FILE

  is_replica
  if [[ $IS_REPLICA -eq 1 ]]; then
    echo "replicaof $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER" >>$NODE_CONF_FILE
    echo "Starting Replica"
  else
    echo "Starting Master"
  fi

  if [[ $TLS == "true" ]]; then
    echo "port 0" >>$NODE_CONF_FILE
    echo "tls-port $NODE_PORT" >>$NODE_CONF_FILE
    echo "tls-cert-file $TLS_MOUNT_PATH/tls.crt" >>$NODE_CONF_FILE
    echo "tls-key-file $TLS_MOUNT_PATH/tls.key" >>$NODE_CONF_FILE
    echo "tls-ca-cert-file $ROOT_CA_PATH" >>$NODE_CONF_FILE
    echo "tls-replication yes" >>$NODE_CONF_FILE
    echo "tls-auth-clients no" >>$NODE_CONF_FILE
  else
    echo "port $NODE_PORT" >>$NODE_CONF_FILE
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
    res=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL monitor $MASTER_NAME $NODE_HOST $NODE_PORT $SENTINEL_QUORUM)
    if [[ $? -ne 0 || ($res == *"ERR"* && $res != *"Duplicate master name"*) ]]; then
      echo "Could not add master to sentinel: $res"
      exit 1
    fi

    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME auth-pass $ADMIN_PASSWORD
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME failover-timeout $SENTINEL_FAILOVER
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME down-after-milliseconds $SENTINEL_DOWN_AFTER
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME parallel-syncs 1
  fi

  # Set maxmemory based on instance type
  get_memory_limit
  if [[ ! -z $MEMORY_LIMIT ]]; then
    echo "Setting maxmemory to $MEMORY_LIMIT"
    redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET maxmemory $MEMORY_LIMIT
  fi

  # Set persistence config
  echo "Setting persistence config: CONFIG SET save '$PERSISTENCE_RDB_CONFIG'"
  redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET save "$PERSISTENCE_RDB_CONFIG"

  if [[ $PERSISTENCE_AOF_CONFIG != "no" ]]; then
    echo "Setting AOF persistence: $PERSISTENCE_AOF_CONFIG"
    redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET appendonly yes
    redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET appendfsync $PERSISTENCE_AOF_CONFIG
  fi

  config_rewrite
fi

if [[ "$RUN_SENTINEL" -eq "1" ]] && ([[ "$NODE_INDEX" == "0" || "$NODE_INDEX" == "1" ]]); then
  sed -i "s/\$ADMIN_PASSWORD/$ADMIN_PASSWORD/g" $SENTINEL_CONF_FILE
  sed -i "s/\$FALKORDB_USER/$FALKORDB_USER/g" $SENTINEL_CONF_FILE
  sed -i "s/\$FALKORDB_PASSWORD/$FALKORDB_PASSWORD/g" $SENTINEL_CONF_FILE
  sed -i "s/\$LOG_LEVEL/$LOG_LEVEL/g" $SENTINEL_CONF_FILE

  sed -i "s/\$SENTINEL_HOST/$NODE_HOST/g" $SENTINEL_CONF_FILE

  echo "Starting Sentinel"

  if [[ $TLS == "true" ]]; then
    echo "port 0" >>$SENTINEL_CONF_FILE
    echo "tls-port $SENTINEL_PORT" >>$SENTINEL_CONF_FILE
    echo "tls-cert-file $TLS_MOUNT_PATH/tls.crt" >>$SENTINEL_CONF_FILE
    echo "tls-key-file $TLS_MOUNT_PATH/tls.key" >>$SENTINEL_CONF_FILE
    echo "tls-ca-cert-file $ROOT_CA_PATH" >>$SENTINEL_CONF_FILE
    echo "tls-replication yes" >>$SENTINEL_CONF_FILE
    echo "tls-auth-clients no" >>$SENTINEL_CONF_FILE
  else
    echo "port $SENTINEL_PORT" >>$SENTINEL_CONF_FILE
  fi

  redis-server $SENTINEL_CONF_FILE --sentinel --logfile $SENTINEL_LOG_FILE_PATH &
  sentinel_pid=$!
  tail -F $SENTINEL_LOG_FILE_PATH &

  sleep 10

  # If FALKORDB_MASTER_HOST is not empty, add monitor to sentinel
  if [[ ! -z $FALKORDB_MASTER_HOST ]]; then
    log "Master Name: $MASTER_NAME\Master Host: $FALKORDB_MASTER_HOST\Master Port: $FALKORDB_MASTER_PORT_NUMBER\nSentinel Quorum: $SENTINEL_QUORUM"
    wait_until_node_host_resolves $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER
    redis-cli -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL monitor $MASTER_NAME $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER $SENTINEL_QUORUM
    redis-cli -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME auth-pass $ADMIN_PASSWORD
    redis-cli -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME failover-timeout $SENTINEL_FAILOVER
    redis-cli -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME down-after-milliseconds $SENTINEL_DOWN_AFTER
    redis-cli -p $SENTINEL_PORT --user $FALKORDB_USER -a $FALKORDB_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME parallel-syncs 1
  fi
fi

if [ -f /usr/local/bin/healthcheck ]; then
  if [[ $RUN_NODE -eq 1 ]] && [[ $RUN_HEALTH_CHECK -eq 1 ]]; then
    echo "Starting Healthcheck"
    healthcheck &
    healthcheck_pid=$!
  fi

  if [[ $RUN_SENTINEL -eq 1 ]] && [[ $RUN_HEALTH_CHECK_SENTINEL -eq 1 ]] && [[ "$NODE_INDEX" == "1" || "$NODE_INDEX" == "0" ]]; then
    echo "Starting Sentinel Healthcheck"
    healthcheck sentinel &
    sentinel_healthcheck_pid=$!
  fi
else
  echo "Healthcheck binary not found"
fi

if [[ $RUN_METRICS -eq 1 ]]; then
  echo "Starting Metrics"
  exporter_url=$(if [[ $TLS == "true" ]]; then echo "rediss://$NODE_HOST:$NODE_PORT"; else echo "redis://localhost:$NODE_PORT"; fi)
  redis_exporter -skip-tls-verification -redis.password $ADMIN_PASSWORD -redis.addr $exporter_url -log-format json -tls-server-min-version TLS1.3 &
  redis_exporter_pid=$!
fi

if [[ $DEBUG -eq 1 && $RUN_SENTINEL -eq 1 ]] && [[ "$NODE_INDEX" == "1" || "$NODE_INDEX" == "0" ]]; then
  # Check for crossed namespace
  echo "Checking for crossed namespace"
  while true; do
    sentinels=$(redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL sentinels $MASTER_NAME)
    # Check if the hostname contains`instance-X` where is not equal to INSTANCE_ID
    for text in $sentinels; do
      if [[ $text == *"instance-"* && $text != *"$INSTANCE_ID"* ]]; then
        echo "Crossed namespace detected"
        # Dump config files
        echo "Sentinels: $sentinels"
        dump_conf_files
      fi
    done
    sleep 5
  done
fi


while true; do
  sleep 1
done
