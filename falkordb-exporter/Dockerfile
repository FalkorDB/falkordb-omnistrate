FROM falkordb/redis_exporter:v1.70.2-alpine as redis_exporter

COPY ./exporter-entrypoint.sh /entrypoint.sh

RUN chmod +x /exporter-entrypoint.sh

ENTRYPOINT [ "/exporter-entrypoint.sh" ]