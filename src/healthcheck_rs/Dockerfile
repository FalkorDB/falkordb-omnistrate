FROM rust:1-slim-trixie AS healthcheck_builder

RUN apt-get update -y && apt-get install -y pkg-config build-essential cmake libtool openssl libssl-dev

WORKDIR /healthcheck

COPY src/healthcheck_rs/ /healthcheck

RUN cargo build --release

FROM debian:trixie-slim

RUN DEBIAN_FRONTEND=noninteractive apt-get update -y \
	&& apt-get install -y --no-install-recommends debconf \
	&& apt-get upgrade -y \
	&& apt-get install -y --no-install-recommends openssl openssl-provider-legacy ca-certificates libssl3t64 \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/*

COPY --from=healthcheck_builder /healthcheck/target/release/healthcheck /usr/local/bin/healthcheck

ENTRYPOINT ["/usr/local/bin/healthcheck"]