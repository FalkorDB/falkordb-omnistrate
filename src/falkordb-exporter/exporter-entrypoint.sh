#!/bin/sh
if [[ "$DATA_DIR" != '/data' ]]; then
  mkdir -p $DATA_DIR
  if [[ -d '/data' ]]; then
    # create simlink
    ln -s /data $DATA_DIR
  fi
fi

if [ -f "/run/secrets/adminpassword" ] && [ -s "/run/secrets/adminpassword" ]; then
  ADMIN_PASSWORD=$(cat "/run/secrets/adminpassword")
  export ADMIN_PASSWORD
elif [ -n "$ADMIN_PASSWORD" ]; then
  export ADMIN_PASSWORD=$ADMIN_PASSWORD
else
  export ADMIN_PASSWORD=''
fi

# get port from the first argument or use default
if [ -n "$1" ]; then
  redis_port=$1
else
  redis_port=${redis_port:-6379}
fi

# get exporter port from the second argument or use default
if [ -n "$2" ]; then
  exporter_port=$2
else
  exporter_port=${exporter_port:-9121}
fi

if [ -n "$3" ]; then
  is_node=$3
else
  is_node=${is_node:-"1"}
fi

if [ -n "$4" ]; then
  is_cluster=$4
else
  is_cluster=${is_cluster:-:""}
fi

if [ -n "$RUN_METRICS" ] && [ "$RUN_METRICS" -eq "$RUN_METRICS" ] 2>/dev/null && [ "$RUN_METRICS" -eq 1 ]; then
  aof_metric_export=$(if [ "$PERSISTENCE_AOF_CONFIG" != "no" ]; then echo "-include-aof-file-size"; else echo ""; fi)
  extra_args=$(if [ "$is_node" -eq "1" ]; then echo "$is_cluster --is-falkordb -slowlog-history-enabled $aof_metric_export"; else echo ""; fi)
  redis_url=$(if [ "$TLS" = "true" ]; then echo "rediss://localhost:$redis_port"; else echo "redis://localhost:$redis_port"; fi)
  exporter_address="0.0.0.0:$exporter_port"
  echo "Starting Metrics Exporter on $exporter_address for Redis at $redis_url"
  # shellcheck disable=SC2068
  redis_exporter -skip-tls-verification -redis.password $ADMIN_PASSWORD -redis.addr $redis_url -web.listen-address $exporter_address -log-format json -tls-server-min-version TLS1.3 -include-system-metrics $extra_args
fi
