ARG FALKORDB_VERSION=v4.0.11

FROM oliver006/redis_exporter:alpine as redis_exporter


FROM rust:1-slim-buster as healthcheck_builder

RUN apt-get update -y && apt-get install -y pkg-config build-essential cmake libtool openssl libssl-dev

WORKDIR /healthcheck

COPY ../healthcheck_rs/ /healthcheck

RUN cargo build --release


FROM falkordb/falkordb:$FALKORDB_VERSION

RUN apt-get update && apt-get install -y curl jq openssl python3 python3-pip python3.11-venv git

RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY falkordb-cluster/node.conf .
COPY falkordb-cluster/cluster-entrypoint.sh /usr/local/bin/
COPY falkordb-cluster/rebalance/ /falkordb/rebalance/
COPY --from=redis_exporter /redis_exporter /usr/local/bin/
COPY --from=healthcheck_builder /healthcheck/target/release/healthcheck /usr/local/bin/healthcheck

RUN chown redis:redis /falkordb/* && \
  chmod +x /usr/local/bin/cluster-entrypoint.sh

RUN python3 -m venv /falkordb/venv && \
  . /falkordb/venv/bin/activate && \
  pip install --upgrade pip && \
  pip install -r /falkordb/rebalance/requirements.txt


CMD ["cluster-entrypoint.sh"]