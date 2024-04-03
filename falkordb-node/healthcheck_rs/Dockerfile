
FROM ekidd/rust-musl-builder as healthcheck_builder

RUN apk add musl-dev pkgconfig alpine-sdk openssl-dev

WORKDIR /healthcheck

COPY ./ /healthcheck

RUN cargo build --release
