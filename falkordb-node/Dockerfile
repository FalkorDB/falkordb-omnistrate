FROM oliver006/redis_exporter:alpine as redis_exporter


FROM rust:alpine3.19 as healthcheck_builder

RUN apk add musl-dev

WORKDIR /healthcheck

COPY ./healthcheck_rs/ /healthcheck

RUN cargo build --release


FROM falkordb/falkordb:edge

RUN apt-get update && apt-get install -y curl jq

RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY sentinel.conf .
COPY node.conf .
COPY node-entrypoint.sh /usr/local/bin/
COPY --from=redis_exporter /redis_exporter /usr/local/bin/
COPY --from=healthcheck_builder /healthcheck/target/release/healthcheck /usr/local/bin/healthcheck

RUN chown redis:redis /falkordb/* && \
  chmod +x /usr/local/bin/node-entrypoint.sh

CMD ["node-entrypoint.sh"]