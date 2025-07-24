FROM falkordb/redis_exporter:v1.70.2-alpine as redis_exporter

FROM alpine:latest

COPY --from=redis_exporter /redis_exporter /usr/local/bin/redis_exporter

COPY falkordb-exporter/exporter-entrypoint.sh /usr/local/bin/exporter-entrypoint.sh

RUN mkdir -p /falkordb

WORKDIR /falkordb

ENV USER=falkordb
ENV GROUPNAME=$USER
ENV UID=1000
ENV GID=1000

ENV FALKORDB_HOME=/var/lib/falkordb

RUN mkdir -p /var/lib/falkordb && \
    chmod 700 /var/lib/falkordb && \
    #chmod +x /usr/local/bin/exporter-entrypoint.sh && \
    addgroup \
    --gid "$GID" \
    "$GROUPNAME" \
    && adduser \
    --disabled-password \
    --home "${FALKORDB_HOME}" \
    --ingroup "$GROUPNAME" \
    --no-create-home \
    --uid "$UID" \
    $USER && \
    chown -R "$USER":"$GROUPNAME" "${FALKORDB_HOME}" && \
    chmod 700 "${FALKORDB_HOME}" && \
    chown -R "$USER":"$GROUPNAME" /falkordb

ENTRYPOINT [ "/usr/local/bin/exporter-entrypoint.sh" ]