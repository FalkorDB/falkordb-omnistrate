#!/bin/bash

DATE_NOW=$(date +"%Y%m%d%H%M%S")

RUN_SENTINEL=${RUN_SENTINEL:-0}
TLS=${TLS:-false}
NODE_INDEX=${NODE_INDEX:-0}
DATA_DIR=${DATA_DIR:-"${FALKORDB_HOME}/data"}

SAVE_LOGS_TO_FILE=${SAVE_LOGS_TO_FILE:-1}
REPLACE_SENTINEL_CONF=${REPLACE_SENTINEL_CONF:-0}

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

LOG_LEVEL=${LOG_LEVEL:-notice}

NODE_HOST=${NODE_HOST:-localhost}
NODE_PORT=${NODE_PORT:-6379}
SENTINEL_HOST=sentinel-$(echo $RESOURCE_ALIAS | cut -d "-" -f 2)-0.$LOCAL_DNS_SUFFIX
SENTINEL_PORT=${SENTINEL_PORT:-26379}
ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/ca-certificates.crt}
TLS_MOUNT_PATH=${TLS_MOUNT_PATH:-/etc/tls}
TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
AUTH_CONNECTION_STRING="-a $ADMIN_PASSWORD --no-auth-warning"

MASTER_NAME=${MASTER_NAME:-master}
SENTINEL_QUORUM=${SENTINEL_QUORUM:-2}
SENTINEL_DOWN_AFTER=${SENTINEL_DOWN_AFTER:-1000}
SENTINEL_FAILOVER=${SENTINEL_FAILOVER:-1000}

# Add backward compatibility for /data folder
if [[ "$DATA_DIR" != '/data' ]]; then
  mkdir -p $DATA_DIR
  if [[ -d '/data' ]]; then
    # create simlink
    ln -s /data $DATA_DIR
  fi
fi

if [[ $(basename "$DATA_DIR") != 'data' ]]; then DATA_DIR=$DATA_DIR/data; fi

SENTINEL_CONF_FILE=$DATA_DIR/sentinel.conf
SENTINEL_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/sentinel_$DATE_NOW.log; else echo ""; fi)

handle_sigterm() {
  echo "Caught SIGTERM"
  echo "Stopping FalkorDB"

  redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL FLUSHCONFIG
  redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SHUTDOWN

  exit 0
}

trap handle_sigterm SIGTERM

wait_until_node_host_resolves() {
  # If $1 is an IP address, return
  if [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    return
  fi

  while true; do
    log "Checking if node host resolves $1:$2"
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
  if [[ $RUN_SENTINEL -eq 1 && -z $master_info ]]; then
    echo "Could not connect to sentinel, waiting 5 seconds and trying again"
    sleep 5
    get_master
    return
  fi

  FALKORDB_MASTER_HOST=$(echo $master_info | awk '{print $1}')
  FALKORDB_MASTER_PORT_NUMBER=$(echo $master_info | awk '{print $2}')
}

log() {
  if [[ $DEBUG -eq 1 ]]; then
    echo $1
  fi
}

# If sentinel.conf doesn't exist or $REPLACE_SENTINEL_CONF=1, copy it from /falkordb
if [ ! -f $SENTINEL_CONF_FILE ] || [ "$REPLACE_SENTINEL_CONF" -eq "1" ]; then
  echo "Copying sentinel.conf from /falkordb"
  cp /falkordb/sentinel.conf $SENTINEL_CONF_FILE
fi

# Create log files if they don't exist
if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then
  if [ "$RUN_SENTINEL" -eq "1" ]; then
    touch $SENTINEL_LOG_FILE_PATH
  fi
fi


create_user(){
  local acl_commands='~* +SENTINEL|get-master-addr-by-name +SENTINEL|remove +SENTINEL|flushconfig +SENTINEL|monitor'
  redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING ACL SETUSER falkordbUpgradeUser on ">$FALKORDB_POST_UPGRADE_PASSWORD" $acl_commands
  redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL FLUSHCONFIG
}


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

  # Start Sentinel supervisord service
  echo "
  [inet_http_server]
  port = 127.0.0.1:9001

  [supervisord]
  nodaemon=true
  logfile=/dev/null
  stdout_logfile=/dev/stdout
  stdout_logfile_maxbytes=0
  stderr_logfile=/dev/stderr
  stderr_logfile_maxbytes=0
  pidfile=/var/run/supervisord.pid

  [supervisorctl]
  serverurl=http://127.0.0.1:9001

  [rpcinterface:supervisor]
  supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

  [program:redis-sentinel]
  command=redis-server $SENTINEL_CONF_FILE --sentinel
  autorestart=true
  stdout_logfile=$SENTINEL_LOG_FILE_PATH
  stderr_logfile=$SENTINEL_LOG_FILE_PATH
  " >$DATA_DIR/supervisord.conf

  tail -F $SENTINEL_LOG_FILE_PATH &

  supervisord -c $DATA_DIR/supervisord.conf &

  sleep 10

  create_user

  if [[ "$RUN_NODE" -eq "1" ]]; then
    log "Master Name: $MASTER_NAME\Master Host: $NODE_HOST\Master Port: $NODE_PORT\nSentinel Quorum: $SENTINEL_QUORUM"
    wait_until_node_host_resolves $SENTINEL_HOST $SENTINEL_PORT
    get_master
    wait_until_node_host_resolves $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER
    response=$(redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL monitor $MASTER_NAME $FALKORDB_MASTER_HOST $FALKORDB_MASTER_PORT_NUMBER $SENTINEL_QUORUM)
    log "Response from SENTINEL MONITOR command: $response"
    if [[ "$response" == "ERR Invalid IP address or hostname specified" ]]; then
      echo """
        The hostname $NODE_HOST for the node $HOSTNAME was resolved successfully the first time but failed to do so a second time,
        this  caused the SENTINEL MONITOR command failed.
      """
      exit 1
    fi

    redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME auth-pass $ADMIN_PASSWORD
    redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME failover-timeout $SENTINEL_FAILOVER
    redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME down-after-milliseconds $SENTINEL_DOWN_AFTER
    redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL set $MASTER_NAME parallel-syncs 1
  fi
fi


# If TLS=true, create a job to rotate the certificate
if [[ "$TLS" == "true" ]]; then
  if [[ $RUN_SENTINEL -eq 1 ]]; then
    echo "Creating sentinel certificate rotation job."
    echo "
    #!/bin/bash
    set -e
    echo 'Restarting sentinel'
    supervisorctl -c $DATA_DIR/supervisord.conf restart redis-sentinel
    " >$DATA_DIR/cert_rotate_sentinel.sh
    chmod +x $DATA_DIR/cert_rotate_sentinel.sh
  fi
fi

while true; do
  sleep 1
done
