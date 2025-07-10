FROM falkordb/redis_exporter:v1.70.2-alpine as redis_exporter

COPY falkordb-exporter/exporter-entrypoint.sh /usr/local/bin/exporter-entrypoint.sh

RUN chmod +x /usr/local/bin/exporter-entrypoint.sh

ENTRYPOINT [ "/usr/local/bin/exporter-entrypoint.sh" ]