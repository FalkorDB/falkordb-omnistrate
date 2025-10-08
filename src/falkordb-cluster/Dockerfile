ARG FALKORDB_VERSION=v4.14.0-alpine

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN apk add --no-cache curl jq openssl supervisor gdb

WORKDIR /falkordb

COPY src/falkordb-cluster/node.conf .
COPY src/falkordb-cluster/cluster-entrypoint.sh /usr/local/bin/
COPY scripts/download-debug-so.sh .

# create falkordb user and group
RUN addgroup -S falkordb && \
    adduser -S falkordb -G falkordb

RUN chown falkordb:falkordb /falkordb/* && \
  chmod +x /usr/local/bin/cluster-entrypoint.sh && \
  mkdir -p /var/lib/falkordb && \
  chown falkordb:falkordb /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chmod +x ./download-debug-so.sh

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT ["cluster-entrypoint.sh"]