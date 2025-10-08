ARG FALKORDB_VERSION=v4.14.0-alpine

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN apk add --no-cache curl jq openssl supervisor gdb

RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY src/falkordb-sentinel/sentinel.conf .
COPY src/falkordb-sentinel/sentinel-entrypoint.sh /usr/local/bin/

# create falkordb user and group
RUN addgroup -S falkordb && \
    adduser -S falkordb -G falkordb

RUN chown falkordb:falkordb /falkordb/* && \
  chmod +x /usr/local/bin/sentinel-entrypoint.sh && \
  mkdir -p /var/lib/falkordb && \
  chown falkordb:falkordb /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "sentinel-entrypoint.sh" ]