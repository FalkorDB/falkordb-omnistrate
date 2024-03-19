#!/bin/bash

HEALTH_CHECK_URL="${HEALTH_CHECK_HOST:-localhost}:${HEALTH_CHECK_PORT:-8081}/healthcheck"

call_healthcheck() {

  echo "Health check URL: $HEALTH_CHECK_URL"
  curl -sf $HEALTH_CHECK_URL
  
  if [ $? -ne 0 ]; then
    echo "Health check failed"
    exit 1
  fi
}

call_healthcheck