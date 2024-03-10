#!/bin/bash

RUN_SENTINEL=${RUN_SENTINEL:-0}
RUN_NODE=${RUN_NODE:-1}

NODE_IP=${NODE_IP:-'localhost'}
NODE_PORT=${NODE_PORT:-6379}

MASTER_NAME=${MASTER_NAME:-'master'}
IS_REPLICA=${IS_REPLICA:-0}

is_replica() {
  master_info=$(redis-cli -h ${SENTINEL_HOST:-'localhost'} -p $SENTINEL_PORT SENTINEL get-master-addr-by-name $MASTER_NAME)
  redisRetVal=$?
  echo "Master Info: $master_info"
  echo "Redis Ret Val: $redisRetVal"
  # Try to connect to sentinel
  # If sentinel is running, ask for the master
  # If the master is not the current node, then it is a replica
  # If the master is the current node, then it is a master
  # If sentinel is not running, then it is a master
  MY_IP=$(curl -sS ifconfig.me)

  echo "My IP: $MY_IP"

  # Check if NODE_IP is empty
  if [[ -z $NODE_IP ]] || [[ $NODE_IP == 'localhost' ]]; then
    NODE_IP=$MY_IP
  fi

  if [[ $redisRetVal -ne 0 ]]; then
    REDIS_MASTER_HOST=${MASTER_HOST:-'localhost'}
    REDIS_MASTER_PORT_NUMBER=${MASTER_PORT:-'6379'}
    IS_REPLICA=0
    return
  fi

  REDIS_MASTER_HOST=${master_info[0]}
  REDIS_MASTER_PORT_NUMBER=${master_info[1]}

  if [[ $REDIS_MASTER_HOST == $MY_IP && $REDIS_MASTER_PORT_NUMBER == $NODE_PORT ]]; then
    IS_REPLICA=0
    return
  else
    IS_REPLICA=1
    return
  fi

}

if [ "$RUN_NODE" -eq "1" ]; then
  sed -i "s/\$NODE_IP/$NODE_IP/g" /redis/node.conf
  sed -i "s/\$NODE_PORT/$NODE_PORT/g" /redis/node.conf

  is_replica
  if [[ $IS_REPLICA -eq 1 ]]; then
    echo "replicaof $REDIS_MASTER_HOST $REDIS_MASTER_PORT_NUMBER\n" >> /redis/node.conf
    echo "Starting Replica"
  else
    echo "Starting Master"
  fi

  redis-server /redis/node.conf &

  # if [[ $IS_REPLICA -eq 0 && $RUN_SENTINEL -eq 1 ]]; then
  #   # Add master to sentinel
  #   redis-cli -h ${SENTINEL_HOST:-'localhost'} -p $SENTINEL_PORT SENTINEL monitor master $NODE_IP $NODE_PORT $SENTINEL_QUORUM
  # fi

fi


if [ "$RUN_SENTINEL" -eq "1" ]; then
  sed -i "s/\$SENTINEL_PORT/$SENTINEL_PORT/g" /redis/sentinel.conf
  sed -i "s/\$SENTINEL_QUORUM/$SENTINEL_QUORUM/g" /redis/sentinel.conf
  sed -i "s/\$SENTINEL_DOWN_AFTER/$SENTINEL_DOWN_AFTER/g" /redis/sentinel.conf
  sed -i "s/\$SENTINEL_FAILOVER/$SENTINEL_FAILOVER/g" /redis/sentinel.conf
  sed -i "s/\$REDIS_PASSWORD/$REDIS_PASSWORD/g" /redis/sentinel.conf
  sed -i "s/\$MASTER_NAME/$MASTER_HOST/g" /redis/sentinel.conf
  sed -i "s/\$MASTER_PORT/$MASTER_PORT/g" /redis/sentinel.conf

  echo "Starting Sentinel"

  redis-server /redis/sentinel.conf --sentinel &
fi


while true; do
  sleep 1
done