#!/usr/bin/env sh

MASTER_NAME=${MASTER_NAME:-"master"}
MASTER_PORT=${MASTER_PORT:-"6379"}

REPLICA_IP=$(curl ifconfig.me)
REPLICA_PORT=${REPLICA_PORT:-"6379"}

IS_MASTER=${IS_MASTER:-0}

echo "Replica IP: $REPLICA_IP and Port: $REPLICA_PORT"

if [ $IS_MASTER -eq 1 ]; then
  echo "Starting as master"
  redis-server --loadmodule /FalkorDB/bin/src/falkordb.so --replica-announce-ip $REPLICA_IP --replica-announce-port $REPLICA_PORT
else
  echo "Starting as replica"
  redis-server --replicaof $MASTER_NAME $MASTER_PORT --loadmodule /FalkorDB/bin/src/falkordb.so --replica-announce-ip $REPLICA_IP --replica-announce-port $REPLICA_PORT
fi