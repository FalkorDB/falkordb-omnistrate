
FROM rust:1-slim-buster as healthcheck_builder

RUN apt-get update && \
    apt install -y pkg-config libssl-dev

WORKDIR /healthcheck

COPY ./ /healthcheck

RUN cargo build --release
