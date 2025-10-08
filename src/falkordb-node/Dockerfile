ARG FALKORDB_VERSION=v4.14.0-alpine

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN apk add --no-cache curl jq openssl supervisor gdb shadow

RUN mkdir -p /var/lib/falkordb

WORKDIR /falkordb

COPY src/falkordb-node/node.conf .
COPY src/falkordb-node/node-entrypoint.sh /usr/local/bin/
COPY scripts/download-debug-so.sh /falkordb/download-debug-so.sh

RUN chown falkordb:falkordb /var/lib/falkordb/* && \
  chmod +x /usr/local/bin/node-entrypoint.sh && \
  mkdir -p /var/lib/falkordb && \
  mkdir -p /var/lib/falkordb/scripts && \
  chown falkordb:falkordb /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chmod +x /falkordb/download-debug-so.sh

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "node-entrypoint.sh" ]