FROM quay.io/opstree/redis:v7.0.15 AS redis

FROM falkordb/falkordb:latest AS falkordb

RUN apt update && apt install -y
COPY --from=redis /usr/bin/entrypoint.sh /usr/bin/entrypoint.sh
COPY --from=redis /node-conf /node-conf/
COPY --from=redis /etc/redis/ /etc/redis/
ENTRYPOINT [ "/usr/bin/entrypoint.sh" ]