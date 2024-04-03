
FROM rust:1-slim-buster as healthcheck_builder

RUN apt-get update && \
    apt install -y pkg-config libssl-dev openssl && \
    apt-get clean

WORKDIR /healthcheck

COPY ./ /healthcheck

RUN cargo build --release
