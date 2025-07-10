#!/bin/bash

#ADMIN_PASSWORD=${ADMIN_PASSWORD:-''}
if [[ -f "/run/secrets/adminpassword" ]] && [[ -s "/run/secrets/adminpassword" ]]; then
  ADMIN_PASSWORD=$(cat "/run/secrets/adminpassword")
  export ADMIN_PASSWORD
elif [[ -n "$ADMIN_PASSWORD" ]]; then
  export ADMIN_PASSWORD=$ADMIN_PASSWORD
else
  export ADMIN_PASSWORD=''
fi

if [[ $RUN_METRICS -eq 1 ]]; then
  echo "Starting Metrics"
  aof_metric_export=$(if [[ $PERSISTENCE_AOF_CONFIG != "no" ]]; then echo "-include-aof-file-size"; else echo ""; fi)
  exporter_url=$(if [[ $TLS == "true" ]]; then echo "rediss://localhost:$NODE_PORT"; else echo "redis://localhost:$NODE_PORT"; fi)
  redis_exporter -skip-tls-verification -redis.password $ADMIN_PASSWORD -redis.addr $exporter_url -log-format json -is-cluster -tls-server-min-version TLS1.3 -include-system-metrics -is-falkordb -slowlog-history-enabled $aof_metric_export
fi
