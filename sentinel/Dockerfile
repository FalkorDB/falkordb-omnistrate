FROM redis:7.2.4-bookworm@sha256:fe98b2d39d462d06a7360e2860dd6ceff930745e3731eccb3c1406dd0dd7f744

ENV SENTINEL_PORT=26379 SENTINEL_QUORUM=2 SENTINEL_DOWN_AFTER=1000 SENTINEL_FAILOVER=1000 REDIS_PASSWORD=redis1234 MASTER_NAME=master MASTER_PORT=6379

RUN mkdir -p /redis

WORKDIR /redis

COPY sentinel.conf .
COPY sentinel-entrypoint.sh /usr/local/bin/

RUN chown redis:redis /redis/* && \
  chmod +x /usr/local/bin/sentinel-entrypoint.sh

EXPOSE $SENTINEL_PORT

CMD ["sentinel-entrypoint.sh"]