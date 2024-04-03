
FROM rust:1-slim-buster as healthcheck_builder

RUN apt-get update && \
    apt install -y pkg-config libssl-dev openssl libudev-dev librust-openssl-sys-dev && \
    apt-get clean

WORKDIR /healthcheck

COPY ./ /healthcheck

RUN cargo build --release
