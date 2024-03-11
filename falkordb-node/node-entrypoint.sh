#!/bin/bash

FALKORDB_PASSWORD=${FALKORDB_PASSWORD:-''}
RUN_SENTINEL=${RUN_SENTINEL:-0}
RUN_NODE=${RUN_NODE:-1}

SENTINEL_PORT=${SENTINEL_PORT:-26379}
SENTINEL_DOWN_AFTER=${SENTINEL_DOWN_AFTER:-1000}
SENTINEL_FAILOVER=${SENTINEL_FAILOVER:-1000}

SENTINEL_HOST=${SENTINEL_HOST:-'localhost'}
NODE_HOST=${NODE_HOST:-'localhost'}
NODE_PORT=${NODE_PORT:-6379}
MASTER_NAME=${MASTER_NAME:-'master'}
SENTINEL_QUORUM=${SENTINEL_QUORUM:-2}

FALKORDB_MASTER_HOST=''
FALKORDB_MASTER_PORT_NUMBER=${MASTER_PORT:-'6379'}
IS_REPLICA=${IS_REPLICA:-0}

get_master() {
  master_info=$(redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $FALKORDB_PASSWORD --no-auth-warning SENTINEL get-master-addr-by-name $MASTER_NAME)
  redisRetVal=$?
  echo "Master Info: $master_info"
  echo "Redis Ret Val: $redisRetVal"

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
  
  # Get node name format: "node-X"
  nodeHostName=$(echo $NODE_HOST | grep -Eo 'node-[0-9]*')
  nodeHostNumber=$(echo $nodeHostName | grep -Eo '[0-9]*')
  # If NODE_HOST starts with node-X, where X > 0, wait until FALKORDB_MASTER_HOST is not empty
  if [[ $nodeHostNumber -gt 0 && -z $FALKORDB_MASTER_HOST ]]; then
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

  if [[ $FALKORDB_MASTER_HOST == $NODE_HOST && $FALKORDB_MASTER_PORT_NUMBER == $NODE_PORT ]]; then
    # This node is the master 
    IS_REPLICA=0
    return
  else
    # This node is a replica
    IS_REPLICA=1
    return
  fi

}

if [ "$RUN_NODE" -eq "1" ]; then
  sed -i "s/\$NODE_HOST/$NODE_HOST/g" /falkordb/node.conf
  sed -i "s/\$NODE_PORT/$NODE_PORT/g" /falkordb/node.conf
  sed -i "s/\$FALKORDB_PASSWORD/$FALKORDB_PASSWORD/g" /falkordb/node.conf

  is_replica
  if [[ $IS_REPLICA -eq 1 ]]; then
    echo "replicaof $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER" >> /falkordb/node.conf
    echo "Starting Replica"
  else
    echo "Starting Master"
  fi

  redis-server /falkordb/node.conf &

  sleep 10

  # If node should be master, add it to sentinel
  if [[ $IS_REPLICA -eq 0 && $RUN_SENTINEL -eq 1 ]]; then
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $FALKORDB_PASSWORD SENTINEL monitor $MASTER_NAME $NODE_HOST $NODE_PORT $SENTINEL_QUORUM
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $FALKORDB_PASSWORD SENTINEL set $MASTER_NAME auth-pass $FALKORDB_PASSWORD
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $FALKORDB_PASSWORD SENTINEL set $MASTER_NAME failover-timeout $SENTINEL_FAILOVER
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $FALKORDB_PASSWORD SENTINEL set $MASTER_NAME down-after-milliseconds $SENTINEL_DOWN_AFTER
    redis-cli -h $SENTINEL_HOST -p $SENTINEL_PORT -a $FALKORDB_PASSWORD SENTINEL set $MASTER_NAME parallel-syncs 1
  fi

fi


if [ "$RUN_SENTINEL" -eq "1" ]; then
  sed -i "s/\$SENTINEL_PORT/$SENTINEL_PORT/g" /falkordb/sentinel.conf
  sed -i "s/\$FALKORDB_PASSWORD/$FALKORDB_PASSWORD/g" /falkordb/sentinel.conf

  echo "Starting Sentinel"

  redis-server /falkordb/sentinel.conf --sentinel &

  sleep 10

  # If FALKORDB_MASTER_HOST is not empty, add monitor to sentinel
  if [[ ! -z $FALKORDB_MASTER_HOST ]]; then
    redis-cli -p $SENTINEL_PORT SENTINEL -a $FALKORDB_PASSWORD monitor $MASTER_NAME $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER $SENTINEL_QUORUM
    redis-cli -p $SENTINEL_PORT SENTINEL -a $FALKORDB_PASSWORD set $MASTER_NAME auth-pass $FALKORDB_PASSWORD
    redis-cli -p $SENTINEL_PORT SENTINEL -a $FALKORDB_PASSWORD set $MASTER_NAME failover-timeout $SENTINEL_FAILOVER
    redis-cli -p $SENTINEL_PORT SENTINEL -a $FALKORDB_PASSWORD set $MASTER_NAME down-after-milliseconds $SENTINEL_DOWN_AFTER
    redis-cli -p $SENTINEL_PORT SENTINEL -a $FALKORDB_PASSWORD set $MASTER_NAME parallel-syncs 1
  fi
fi


while true; do
  sleep 1
done