
FROM rust:alpine3.19 as healthcheck_builder

RUN apk add musl-dev

WORKDIR /healthcheck

COPY ./ /healthcheck

RUN cargo build --release
