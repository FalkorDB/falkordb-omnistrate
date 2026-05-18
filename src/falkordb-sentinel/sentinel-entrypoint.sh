#!/bin/bash

read_secret_or_env() {
  local secret_path=$1
  local env_name=$2

  if [[ -f "$secret_path" ]] && [[ -s "$secret_path" ]]; then
    cat "$secret_path"
  else
    printf '%s' "${!env_name:-}"
  fi
}

resolve_host_ip() {
  local host=$1
  local description=${2:-$1}
  local timeout_seconds=${3:-300}
  local deadline=$((SECONDS + timeout_seconds))
  local resolved_ip

  if [[ $host =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$host"
    return 0
  fi

  while true; do
    resolved_ip=$(getent hosts "$host" 2>/dev/null | awk '{print $1; exit}')
    if [[ -n "$resolved_ip" ]]; then
      echo "$resolved_ip"
      return 0
    fi

    if (( SECONDS >= deadline )); then
      echo "Timed out trying to resolve ip for $description: $host" >&2
      return 1
    fi

    echo "Waiting for $description to resolve: $host" >&2
    sleep 3
  done
}

load_credentials() {
  FALKORDB_USER=${FALKORDB_USER:-falkordb}
  FALKORDB_PASSWORD=$(read_secret_or_env "/run/secrets/falkordbpassword" "FALKORDB_PASSWORD")
  ADMIN_PASSWORD=$(read_secret_or_env "/run/secrets/adminpassword" "ADMIN_PASSWORD")
  export ADMIN_PASSWORD
}

initialize_defaults() {
  RUN_SENTINEL=${RUN_SENTINEL:-0}
  TLS=${TLS:-false}
  NODE_INDEX=${NODE_INDEX:-0}
  DATA_DIR=${DATA_DIR:-"${FALKORDB_HOME}/data"}
  SAVE_LOGS_TO_FILE=${SAVE_LOGS_TO_FILE:-1}
  REPLACE_SENTINEL_CONF=${REPLACE_SENTINEL_CONF:-0}
  LOG_LEVEL=${LOG_LEVEL:-notice}
  NODE_HOST=${NODE_HOST:-localhost}
  NODE_PORT=${NODE_PORT:-6379}
  SENTINEL_HOST=sentinel-$(echo $RESOURCE_ALIAS | cut -d "-" -f 2)-0.$LOCAL_DNS_SUFFIX
  SENTINEL_PORT=${SENTINEL_PORT:-26379}
  ROOT_CA_PATH=${ROOT_CA_PATH:-/etc/ssl/certs/ca-certificates.crt}
  BASE_ROOT_CA_PATH=$ROOT_CA_PATH
  TLS_MOUNT_PATH=${TLS_MOUNT_PATH:-/etc/tls}
  SELFSIGNED_CA_PATH="$TLS_MOUNT_PATH/selfsigned-ca.crt"
  MASTER_NAME=${MASTER_NAME:-master}
  SENTINEL_QUORUM=${SENTINEL_QUORUM:-2}
  SENTINEL_DOWN_AFTER=${SENTINEL_DOWN_AFTER:-30000}
  SENTINEL_FAILOVER=${SENTINEL_FAILOVER:-180000}
}

prepare_data_dir() {
  # Ensure DATA_DIR ends with /data
  if [[ $(basename "$DATA_DIR") != 'data' ]]; then
    DATA_DIR="$DATA_DIR/data"
  fi

  # If DATA_DIR is /data (the volume mount itself), nothing to do
  if [[ "$DATA_DIR" == '/data' ]]; then
    return
  fi

  # Create parent directory for DATA_DIR
  mkdir -p "$(dirname "$DATA_DIR")"

  # If the /data volume mount exists and DATA_DIR is not yet created,
  # symlink DATA_DIR -> /data so all files land on the persistent volume.
  # Otherwise just create the directory.
  if [[ -d '/data' ]] && [[ ! -e "$DATA_DIR" ]]; then
    ln -s /data "$DATA_DIR"
  elif [[ ! -e "$DATA_DIR" ]]; then
    mkdir -p "$DATA_DIR"
  fi

  COMBINED_CA_PATH="$DATA_DIR/selfsigned-tls-combined.pem"
}

prepare_tls_ca_bundle() {
  if [[ "$TLS" == "true" ]] && [[ -f "$SELFSIGNED_CA_PATH" ]]; then
    if ! cat "$BASE_ROOT_CA_PATH" "$SELFSIGNED_CA_PATH" > "$COMBINED_CA_PATH"; then
      echo "Failed to create combined CA cert file"
      exit 1
    fi
    ROOT_CA_PATH="$COMBINED_CA_PATH"
  fi
}

initialize_runtime_paths() {
  TLS_CONNECTION_STRING=$(if [[ $TLS == "true" ]]; then echo "--tls --cacert $ROOT_CA_PATH"; else echo ""; fi)
  AUTH_CONNECTION_STRING="-a $ADMIN_PASSWORD --no-auth-warning"
  DATE_NOW=$(date +"%Y%m%d%H%M%S")
  SENTINEL_CONF_FILE=$DATA_DIR/sentinel.conf
  SENTINEL_LOG_FILE_PATH=$(if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then echo $DATA_DIR/sentinel_$DATE_NOW.log; else echo "/dev/null"; fi)
}

init_environment() {
  load_credentials
  initialize_defaults
  prepare_data_dir
  prepare_tls_ca_bundle
  initialize_runtime_paths
}

handle_sigterm() {
  echo "Caught SIGTERM"
  echo "Stopping FalkorDB"

  redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SENTINEL FLUSHCONFIG
  redis-cli -p $SENTINEL_PORT $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING SHUTDOWN

  exit 0
}

wait_for_node_ready() {
  local port=${1:-$NODE_PORT}
  echo "Waiting for FalkorDB to be ready on port $port"
  while true; do
    if [[ $(redis-cli -p $port $AUTH_CONNECTION_STRING $TLS_CONNECTION_STRING PING 2>/dev/null) == "PONG" ]]; then
      echo "FalkorDB is ready"
      break
    fi
    sleep 1
  done
}

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

fix_namespace_in_config_files() {
  # Use INSTANCE_ID environment variable to get the current namespace
  if [[ -n "$INSTANCE_ID" ]]; then
    echo "Current namespace: $INSTANCE_ID"
    
    # Check and fix sentinel.conf only (sentinel entrypoint should only check sentinel.conf)
    if [[ -f "$SENTINEL_CONF_FILE" ]]; then
      echo "Checking sentinel.conf for namespace mismatches"
      # Replace instance-X pattern with current namespace, where X can contain hyphens, underscores, and alphanumeric characters
      sed -i -E "s/instance-[a-zA-Z0-9_\-]+/${INSTANCE_ID}/g" "$SENTINEL_CONF_FILE"
    fi
  else
    echo "INSTANCE_ID not set, skipping namespace fix"
  fi
  
  # Fix DNS suffix mismatches when snapshot is restored in different cluster
  if [[ -n "$DNS_SUFFIX" ]]; then
    echo "Current DNS suffix: $DNS_SUFFIX"
    
    # Escape special sed characters (&, \, /) in DNS_SUFFIX for safe use in replacement string
    local escaped_dns_suffix=$(echo "$DNS_SUFFIX" | sed 's/[&\\/]/\\&/g')
    
    # Check and fix sentinel.conf
    if [[ -f "$SENTINEL_CONF_FILE" ]]; then
      echo "Checking sentinel.conf for DNS suffix mismatches"
      # Replace old DNS suffixes with current one
      # This regex matches the Omnistrate DNS suffix structure: hc-<ID>.<REGION>.<CLOUD>.<HASH>.<TLD>
      sed -i -E "s/([a-zA-Z0-9_-]*[a-zA-Z][a-zA-Z0-9_-]*)\.hc-[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.[a-zA-Z0-9-]+\.[a-f0-9]+\.[a-zA-Z]+/\1.${escaped_dns_suffix}/g" "$SENTINEL_CONF_FILE"
    fi
  else
    echo "DNS_SUFFIX not set, skipping DNS suffix fix"
  fi
}

ensure_sentinel_conf_exists() {
  # If sentinel.conf doesn't exist or $REPLACE_SENTINEL_CONF=1, copy it from /falkordb
  if [ ! -f $SENTINEL_CONF_FILE ] || [ "$REPLACE_SENTINEL_CONF" -eq "1" ]; then
    echo "Copying sentinel.conf from /falkordb"
    cp /falkordb/sentinel.conf $SENTINEL_CONF_FILE
  fi
}

ensure_log_file_exists() {
  if [[ $SAVE_LOGS_TO_FILE -eq 1 ]]; then
    if [ "$RUN_SENTINEL" -eq "1" ]; then
      touch $SENTINEL_LOG_FILE_PATH
    fi
  fi
}

strip_stale_sentinel_state() {
  # Strip accumulated sentinel dynamic state from previous deployment
  # Sentinel will rediscover replicas and other sentinels from the current deployment
  if [[ -f "$SENTINEL_CONF_FILE" ]]; then
    echo "Stripping stale sentinel state (known-replica, known-sentinel) from sentinel.conf"
    sed -i '/^sentinel known-replica /d' "$SENTINEL_CONF_FILE"
    sed -i '/^sentinel known-sentinel /d' "$SENTINEL_CONF_FILE"
  fi
}

run_sentinel() {
  if [[ "$RUN_SENTINEL" -eq "1" ]] && ([[ "$NODE_INDEX" == "0" || "$NODE_INDEX" == "1" ]]); then
    sed -i "s/\$ADMIN_PASSWORD/$ADMIN_PASSWORD/g" $SENTINEL_CONF_FILE
    sed -i "s/\$FALKORDB_USER/$FALKORDB_USER/g" $SENTINEL_CONF_FILE
    sed -i "s/\$FALKORDB_PASSWORD/$FALKORDB_PASSWORD/g" $SENTINEL_CONF_FILE
    sed -i "s/\$LOG_LEVEL/$LOG_LEVEL/g" $SENTINEL_CONF_FILE

    sed -i "s/\$SENTINEL_HOST/$NODE_HOST/g" $SENTINEL_CONF_FILE

    echo "Starting Sentinel"

    if [[ $TLS == "true" ]]; then
      sed -i "s|/etc/ssl/certs/GlobalSign_Root_CA.pem|${ROOT_CA_PATH}|g" "$SENTINEL_CONF_FILE"
      if ! grep -q "^tls-port $SENTINEL_PORT" "$SENTINEL_CONF_FILE"; then
        echo "port 0" >>$SENTINEL_CONF_FILE
        echo "tls-port $SENTINEL_PORT" >>$SENTINEL_CONF_FILE
        echo "tls-cert-file $TLS_MOUNT_PATH/tls.crt" >>$SENTINEL_CONF_FILE
        echo "tls-key-file $TLS_MOUNT_PATH/tls.key" >>$SENTINEL_CONF_FILE
        echo "tls-client-cert-file $TLS_MOUNT_PATH/selfsigned-tls.crt" >>$SENTINEL_CONF_FILE
        echo "tls-client-key-file $TLS_MOUNT_PATH/selfsigned-tls.key" >>$SENTINEL_CONF_FILE
        echo "tls-ca-cert-file $ROOT_CA_PATH" >>$SENTINEL_CONF_FILE
        echo "tls-replication yes" >>$SENTINEL_CONF_FILE
        echo "tls-auth-clients optional" >>$SENTINEL_CONF_FILE
      else
        sed -i "s|tls-port .*|tls-port $SENTINEL_PORT|g" "$SENTINEL_CONF_FILE"
        sed -i "s|tls-cert-file .*|tls-cert-file $TLS_MOUNT_PATH/tls.crt|g" "$SENTINEL_CONF_FILE"
        sed -i "s|tls-key-file .*|tls-key-file $TLS_MOUNT_PATH/tls.key|g" "$SENTINEL_CONF_FILE"
        if grep -q "^tls-client-cert-file " "$SENTINEL_CONF_FILE"; then
          sed -i "s|tls-client-cert-file .*|tls-client-cert-file $TLS_MOUNT_PATH/selfsigned-tls.crt|g" "$SENTINEL_CONF_FILE"
        else
          echo "tls-client-cert-file $TLS_MOUNT_PATH/selfsigned-tls.crt" >>$SENTINEL_CONF_FILE
        fi
        if grep -q "^tls-client-key-file " "$SENTINEL_CONF_FILE"; then
          sed -i "s|tls-client-key-file .*|tls-client-key-file $TLS_MOUNT_PATH/selfsigned-tls.key|g" "$SENTINEL_CONF_FILE"
        else
          echo "tls-client-key-file $TLS_MOUNT_PATH/selfsigned-tls.key" >>$SENTINEL_CONF_FILE
        fi
        sed -i "s|tls-ca-cert-file .*|tls-ca-cert-file $ROOT_CA_PATH|g" "$SENTINEL_CONF_FILE"
        if grep -q "^tls-replication " "$SENTINEL_CONF_FILE"; then
          sed -i "s|tls-replication .*|tls-replication yes|g" "$SENTINEL_CONF_FILE"
        else
          echo "tls-replication yes" >>$SENTINEL_CONF_FILE
        fi
        if grep -q "^tls-auth-clients " "$SENTINEL_CONF_FILE"; then
          sed -i "s|tls-auth-clients .*|tls-auth-clients optional|g" "$SENTINEL_CONF_FILE"
        else
          echo "tls-auth-clients optional" >>$SENTINEL_CONF_FILE
        fi
      fi
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

    # Add split-brain-monitor only if NODE_INDEX is 0 and HOSTNAME starts with sentinel-
    if [[ "$NODE_INDEX" == "0" && "$HOSTNAME" =~ ^sentinel.* ]]; then
      echo "Adding split-brain-monitor to supervisord configuration"
      echo "
  [program:split-brain-monitor]
  command=/usr/local/bin/split-brain-monitor.sh
  autorestart=true
  stdout_logfile=/dev/stdout
  stdout_logfile_maxbytes=0
  stderr_logfile=/dev/stderr
  stderr_logfile_maxbytes=0
  startretries=3
  startsecs=10
    " >>$DATA_DIR/supervisord.conf
    else
      echo "Skipping split-brain-monitor (NODE_INDEX=$NODE_INDEX, HOSTNAME=$HOSTNAME)"
    fi

    tail -F $SENTINEL_LOG_FILE_PATH &

    supervisord -c $DATA_DIR/supervisord.conf &

    wait_for_node_ready $SENTINEL_PORT
    
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
}

create_tls_rotation_job_script() {
  if [[ "$TLS" == "true" && $RUN_SENTINEL -eq 1 ]]; then
    echo "Creating sentinel certificate rotation job."
    cat >"$DATA_DIR/cert_rotate_sentinel.sh" <<EOF
#!/bin/bash
set -e
if [[ -f "$SELFSIGNED_CA_PATH" ]]; then
  cat "$BASE_ROOT_CA_PATH" "$SELFSIGNED_CA_PATH" > "$COMBINED_CA_PATH"
fi
echo 'Restarting sentinel'
supervisorctl -c $DATA_DIR/supervisord.conf restart redis-sentinel
EOF
    chmod +x $DATA_DIR/cert_rotate_sentinel.sh
  fi
}

wait_forever() {
  while true; do
    sleep 1
  done
}

main() {
  init_environment
  trap handle_sigterm SIGTERM
  ensure_sentinel_conf_exists
  ensure_log_file_exists
  fix_namespace_in_config_files
  strip_stale_sentinel_state
  run_sentinel
  create_tls_rotation_job_script
  wait_forever
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
