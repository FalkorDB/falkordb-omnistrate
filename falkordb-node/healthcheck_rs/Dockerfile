
FROM rust:1-slim-buster as healthcheck_builder

RUN apt-get update -y && apt-get install -y pkg-config build-essential cmake libtool openssl libssl-dev

WORKDIR /healthcheck

COPY ./ /healthcheck

RUN cargo build --release
