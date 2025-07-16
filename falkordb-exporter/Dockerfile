FROM falkordb/redis_exporter:v1.70.2-alpine as redis_exporter

FROM alpine:latest

COPY --from=redis_exporter /redis_exporter /usr/local/bin/redis_exporter

COPY falkordb-exporter/exporter-entrypoint.sh /usr/local/bin/exporter-entrypoint.sh

RUN chown falkordb:falkordb /falkordb/* && \
  mkdir -p /var/lib/falkordb && \
  chown falkordb:falkordb /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chmod +x /usr/local/bin/exporter-entrypoint.sh

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "/usr/local/bin/exporter-entrypoint.sh" ]