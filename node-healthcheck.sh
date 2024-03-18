#!/bin/bash

TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)

db_info=$(redis-cli $TLS_CONNECTION_STRING -a ADMIN_PASSWORD --no-auth-warning info replication)  
redisRetVal=$?

if [ $redisRetVal -ne 0 ]; then
  echo "Could not connect to FalkorDB"
  exit 1
fi

ROLE=$(echo "$db_info" | grep -Eo 'role:\w+')

# If it's master, check if redis is running
if [[ "$ROLE" == "role:master" ]]; then
  echo "Master is running"            
  exit 0
fi

# If it's replica, check if it's connected and synced with master
progress=$(echo $db_info | grep -Eo 'master_sync_in_progress:\d')
if [ -z "$progress" ]; then
  echo "Replica is not connected to master"
  exit 1
fi

progress=${progress#*:}
if [ $progress -eq 1 ]; then
  echo "Replica is syncing"
  exit 1
fi
