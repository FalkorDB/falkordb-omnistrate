FROM falkordb/falkordb:edge

ENV MASTER_HOST=localhost MASTER_PORT=6379 NODE_PORT=6379 RUN_NODE=1 RUN_SENTINEL=0
ENV SENTINEL_PORT=26379 SENTINEL_QUORUM=2 SENTINEL_DOWN_AFTER=1000 SENTINEL_FAILOVER=1000 REDIS_PASSWORD=redis1234

# Install curl
RUN apt-get update && apt-get install -y curl

RUN mkdir -p /redis

WORKDIR /redis

COPY sentinel.conf .
COPY node.conf .
COPY node-entrypoint.sh /usr/local/bin/

RUN chown redis:redis /redis/* && \
  chmod +x /usr/local/bin/node-entrypoint.sh

CMD ["node-entrypoint.sh"]