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

if [ -n "$RUN_METRICS" ] && [ "$RUN_METRICS" -eq "$RUN_METRICS" ] 2>/dev/null && [ "$RUN_METRICS" -eq 1 ]; then
  echo "Starting Metrics"
  aof_metric_export=$(if [ "$PERSISTENCE_AOF_CONFIG" != "no" ]; then echo "-include-aof-file-size"; else echo ""; fi)
  redis_url=$(if [ "$TLS" = "true" ]; then echo "rediss://localhost:$redis_port"; else echo "redis://localhost:$redis_port"; fi)
  exporter_address="0.0.0.0:$exporter_port"
  # shellcheck disable=SC2068
  redis_exporter -skip-tls-verification -redis.password $ADMIN_PASSWORD -redis.addr $redis_url -web.listen-address $exporter_address -log-format json -tls-server-min-version TLS1.3 -include-system-metrics -is-falkordb -slowlog-history-enabled $aof_metric_export $@
fi
