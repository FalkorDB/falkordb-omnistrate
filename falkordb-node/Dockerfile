ARG FALKORDB_VERSION=v4.2.1

FROM oliver006/redis_exporter:alpine as redis_exporter


FROM rust:1-slim-buster as healthcheck_builder

RUN apt-get update -y && apt-get install -y pkg-config build-essential cmake libtool openssl libssl-dev

WORKDIR /healthcheck

COPY ../healthcheck_rs/ /healthcheck

RUN cargo build --release


FROM falkordb/falkordb:$FALKORDB_VERSION

RUN apt-get update && apt-get install -y curl jq openssl supervisor

RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY falkordb-node/sentinel.conf .
COPY falkordb-node/node.conf .
COPY falkordb-node/node-entrypoint.sh /usr/local/bin/
COPY --from=redis_exporter /redis_exporter /usr/local/bin/
COPY --from=healthcheck_builder /healthcheck/target/release/healthcheck /usr/local/bin/healthcheck

RUN chown redis:redis /falkordb/* && \
  chmod +x /usr/local/bin/node-entrypoint.sh

COPY falkordb-node/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

CMD ["/usr/bin/supervisord"]
#CMD [ "node-entrypoint.sh" ]