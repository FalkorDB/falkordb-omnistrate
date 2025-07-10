FROM falkordb/redis_exporter:v1.70.2-alpine as redis_exporter

COPY falkordb-exporter/exporter-entrypoint.sh /exporter-entrypoint.sh

RUN chmod +x /exporter-entrypoint.sh

ENTRYPOINT [ "/exporter-entrypoint.sh" ]