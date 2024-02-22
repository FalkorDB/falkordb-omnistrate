#!/usr/bin/env sh

sed -i "s/\$SENTINEL_PORT/$SENTINEL_PORT/g" /redis/sentinel.conf
sed -i "s/\$SENTINEL_QUORUM/$SENTINEL_QUORUM/g" /redis/sentinel.conf
sed -i "s/\$SENTINEL_DOWN_AFTER/$SENTINEL_DOWN_AFTER/g" /redis/sentinel.conf
sed -i "s/\$SENTINEL_FAILOVER/$SENTINEL_FAILOVER/g" /redis/sentinel.conf
sed -i "s/\$REDIS_PASSWORD/$REDIS_PASSWORD/g" /redis/sentinel.conf
sed -i "s/\$MASTER_NAME/$MASTER_NAME/g" /redis/sentinel.conf
sed -i "s/\$MASTER_PORT/$MASTER_PORT/g" /redis/sentinel.conf

redis-server /redis/sentinel.conf --sentinel