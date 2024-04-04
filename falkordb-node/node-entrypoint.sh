#!/bin/bash

FALKORDB_USER=${FALKORDB_USER:-falkordb}
FALKORDB_PASSWORD=${FALKORDB_PASSWORD:-''}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-''}
RUN_SENTINEL=${RUN_SENTINEL:-0}
RUN_NODE=${RUN_NODE:-1}
RUN_METRICS=${RUN_METRICS:-1}
RUN_HEALTH_CHECK=${RUN_HEALTH_CHECK:-1}
TLS=${TLS:-false}
NODE_INDEX=${NODE_INDEX:-0}
INSTANCE_TYPE=${INSTANCE_TYPE:-''}

SENTINEL_PORT=${SENTINEL_PORT:-26379}
SENTINEL_DOWN_AFTER=${SENTINEL_DOWN_AFTER:-1000}
SENTINEL_FAILOVER=${SENTINEL_FAILOVER:-1000}

SENTINEL_HOST=${SENTINEL_HOST:-localhost}
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


NODE_CONF_FILE=$DATA_DIR/node.conf
SENTINEL_CONF_FILE=$DATA_DIR/sentinel.conf

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

  memory_limit_instance_type_map="{\"e2-custom-small-1024\":\"100000000\",\"e2-small\":\"840000000\",\"e2-medium\": \"23000000000\",\"e2-custom-2-6144\":\"4000000000\",\"e2-custom-4-10240\": \"7590000000\",\"e2-custom-8-18432\": \"15290000000\",\"e2-custom-16-34816\":\"31120000000\"}"

  if [[ -z $INSTANCE_TYPE ]]; then
    echo "INSTANCE_TYPE is not set"
    return
  fi

  MEMORY_LIMIT=$(echo $memory_limit_instance_type_map | jq -r ".\"$INSTANCE_TYPE\"")

  echo "Memory Limit: $MEMORY_LIMIT"

}

wait_until_sentinel_host_resolves() {
  while true; do
    log "Checking if sentinel host resolves $SENTINEL_HOST"
    if [[ $(getent hosts $SENTINEL_HOST) ]]; then
      sentinel_response=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL masters)
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
  master_info=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD $TLS_CONNECTION_STRING --no-auth-warning SENTINEL get-master-addr-by-name $MASTER_NAME)
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
    if [[ $TLS == "true" ]]; then
      FALKORDB_MASTER_HOST=$NODE_HOST
    else
      FALKORDB_MASTER_HOST=$NODE_HOST_IP
    fi
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

get_self_host_ip

if [ "$RUN_NODE" -eq "1" ]; then
 
  # If TLS is enabled, use NODE_HOST; otherwise, use NODE_HOST_IP
  if [[ $TLS == "true" ]]; then
    sed -i "s/\$NODE_HOST/$NODE_HOST/g" $NODE_CONF_FILE
  else
    sed -i "s/\$NODE_HOST/$NODE_HOST_IP/g" $NODE_CONF_FILE
  fi
  
  sed -i "s/\$NODE_PORT/$NODE_PORT/g" $NODE_CONF_FILE
  sed -i "s/\$ADMIN_PASSWORD/$ADMIN_PASSWORD/g" $NODE_CONF_FILE
  echo "dir $DATA_DIR" >> $NODE_CONF_FILE

  is_replica
  if [[ $IS_REPLICA -eq 1 ]]; then
    echo "replicaof $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER" >> $NODE_CONF_FILE
    echo "Starting Replica"
  else
    echo "Starting Master"
  fi

  if [[ $TLS == "true" ]]; then
    echo "port 0" >> $NODE_CONF_FILE
    echo "tls-port $NODE_PORT" >> $NODE_CONF_FILE
    echo "tls-cert-file $TLS_MOUNT_PATH/tls.crt" >> $NODE_CONF_FILE
    echo "tls-key-file $TLS_MOUNT_PATH/tls.key" >> $NODE_CONF_FILE
    echo "tls-ca-cert-file $ROOT_CA_PATH" >> $NODE_CONF_FILE
    echo "tls-replication yes" >> $NODE_CONF_FILE
    echo "tls-auth-clients no" >> $NODE_CONF_FILE
  else
    echo "port $NODE_PORT" >> $NODE_CONF_FILE
  fi

  redis-server $NODE_CONF_FILE &

  sleep 10


  # If node should be master, add it to sentinel
  if [[ $IS_REPLICA -eq 0 && $RUN_SENTINEL -eq 1 ]]; then
    echo "Adding master to sentinel"
    wait_until_sentinel_host_resolves

    if [[ $TLS == "true" ]]; then
      wait_until_node_host_resolves $NODE_HOST $NODE_PORT
      log "Master Name: $MASTER_NAME\nNode Host: $NODE_HOST\nNode Port: $NODE_PORT\nSentinel Quorum: $SENTINEL_QUORUM"
      redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL monitor $MASTER_NAME $NODE_HOST $NODE_PORT $SENTINEL_QUORUM
      if [[ $? -ne 0 ]]; then
        echo "Could not add master to sentinel"
        exit 1
      fi
    else
      log "Master Name: $MASTER_NAME\nNode IP: $NODE_HOST_IP\nNode Port: $NODE_PORT\nSentinel Quorum: $SENTINEL_QUORUM"
      redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL monitor $MASTER_NAME $NODE_HOST_IP $NODE_PORT $SENTINEL_QUORUM
      if [[ $? -ne 0 ]]; then
        echo "Could not add master to sentinel"
        exit 1
      fi
    fi

    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME auth-pass $ADMIN_PASSWORD
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME failover-timeout $SENTINEL_FAILOVER
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME down-after-milliseconds $SENTINEL_DOWN_AFTER
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME parallel-syncs 1
  fi

  echo "Creating falkordb user"
  redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING ACL SETUSER $FALKORDB_USER on ">$FALKORDB_PASSWORD" ~* +INFO +PING +HELLO +AUTH +RESTORE +DUMP +DEL +EXISTS +UNLINK +TYPE +FLUSHALL +TOUCH +EXPIRE +PEXPIREAT +TTL +PTTL +EXPIRETIME +RENAME +RENAMENX +SCAN +DISCARD +EXEC +MULTI +UNWATCH +WATCH +ECHO +SLOWLOG +WAIT +WAITAOF +GRAPH.INFO +GRAPH.LIST +GRAPH.QUERY +GRAPH.RO_QUERY +GRAPH.EXPLAIN +GRAPH.PROFILE +GRAPH.DELETE +GRAPH.CONSTRAINT +GRAPH.SLOWLOG +GRAPH.BULK +GRAPH.CONFIG

  # Set maxmemory based on instance type
  get_memory_limit
  if [[ ! -z $MEMORY_LIMIT ]]; then
    echo "Setting maxmemory to $MEMORY_LIMIT"
    redis-cli -p $NODE_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING CONFIG SET maxmemory $MEMORY_LIMIT
  fi

fi


if [ "$RUN_SENTINEL" -eq "1" ]; then
  echo "Starting Sentinel"
  
  sed -i "s/\$ADMIN_PASSWORD/$ADMIN_PASSWORD/g" $SENTINEL_CONF_FILE

  # When LB is in place, change external dns to internal ip
  sed -i "s/\$SENTINEL_HOST/$NODE_EXTERNAL_DNS/g" $SENTINEL_CONF_FILE

  echo "Starting Sentinel"

  if [[ $TLS == "true" ]]; then
    echo "port 0" >> $SENTINEL_CONF_FILE
    echo "tls-port $SENTINEL_PORT" >> $SENTINEL_CONF_FILE
    echo "tls-cert-file $TLS_MOUNT_PATH/tls.crt" >> $SENTINEL_CONF_FILE
    echo "tls-key-file $TLS_MOUNT_PATH/tls.key" >> $SENTINEL_CONF_FILE
    echo "tls-ca-cert-file $ROOT_CA_PATH" >> $SENTINEL_CONF_FILE
    echo "tls-replication yes" >> $SENTINEL_CONF_FILE
    echo "tls-auth-clients no" >> $SENTINEL_CONF_FILE
  else
    echo "port $SENTINEL_PORT" >> $SENTINEL_CONF_FILE
    echo "sentinel resolve-hostnames yes" >> $SENTINEL_CONF_FILE
  fi

  redis-server $SENTINEL_CONF_FILE --sentinel &

  sleep 10

  # If FALKORDB_MASTER_HOST is not empty, add monitor to sentinel
  if [[ ! -z $FALKORDB_MASTER_HOST ]]; then
    log "Master Name: $MASTER_NAME\Master Host: $FALKORDB_MASTER_HOST\Master Port: $FALKORDB_MASTER_PORT_NUMBER\nSentinel Quorum: $SENTINEL_QUORUM"
    wait_until_node_host_resolves $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER
    redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL monitor $MASTER_NAME $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER $SENTINEL_QUORUM
    redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME auth-pass $ADMIN_PASSWORD
    redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME failover-timeout $SENTINEL_FAILOVER
    redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME down-after-milliseconds $SENTINEL_DOWN_AFTER
    redis-cli -p $SENTINEL_PORT -a $ADMIN_PASSWORD --no-auth-warning $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME parallel-syncs 1
  fi
fi


if [[ $RUN_METRICS -eq 1 ]]; then
  echo "Starting Metrics"
  redis_exporter -skip-tls-verification -redis.password $ADMIN_PASSWORD -redis.addr $NODE_HOST_IP:$NODE_PORT &
fi

if [[ $RUN_HEALTH_CHECK -eq 1 ]]; then
  # Check if healthcheck binary exists
  if [ -f /usr/local/bin/healthcheck ]; then
    echo "Starting Healthcheck"
    healthcheck &
  else
    echo "Healthcheck binary not found"
  fi
fi

while true; do
  sleep 1
done