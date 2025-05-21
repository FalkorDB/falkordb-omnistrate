ARG FALKORDB_VERSION=v4.8.6

FROM falkordb/redis_exporter:v1.70.2-alpine as redis_exporter


FROM rust:1-slim-buster as healthcheck_builder

RUN apt-get update -y && apt-get install -y pkg-config build-essential cmake libtool openssl libssl-dev

WORKDIR /healthcheck

COPY ../healthcheck_rs/ /healthcheck

RUN cargo build --release


FROM falkordb/falkordb:$FALKORDB_VERSION

RUN apt-get update && apt-get install -y curl jq openssl cron supervisor gdb

RUN useradd -u 1000 -m falkordb && usermod -aG crontab falkordb && \
  touch /var/run/crond.pid && chown root:crontab /var/run/crond.pid && \
  touch /var/run/supervisord.pid && chown falkordb:falkordb /var/run/supervisord.pid && \
  chmod 660 /var/run/crond.pid && \
  chmod 660 /var/run/supervisord.pid && \
  chmod u+s /usr/sbin/cron


RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY falkordb-node/sentinel.conf .
COPY falkordb-node/node.conf .
COPY falkordb-node/node-entrypoint.sh /usr/local/bin/
COPY --from=redis_exporter /redis_exporter /usr/local/bin/
COPY --from=healthcheck_builder /healthcheck/target/release/healthcheck /usr/local/bin/healthcheck
COPY scripts/download-debug-so.sh .

RUN chown falkordb:falkordb /falkordb/* && \
  chmod +x /usr/local/bin/node-entrypoint.sh && \
  mkdir -p /var/lib/falkordb && \
  chown falkordb:falkordb /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chmod +x ./download-debug-so.sh

ENV FALKORDB_HOME=/var/lib/falkordb

CMD [ "node-entrypoint.sh" ]