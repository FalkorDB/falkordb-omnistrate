FROM rust:1-slim-bullseye as healthcheck_builder

RUN apt-get update -y && apt-get install -y pkg-config build-essential cmake libtool openssl libssl-dev

WORKDIR /healthcheck

COPY ../healthcheck_rs/ /healthcheck

RUN cargo build --release

FROM debian:bullseye-slim

RUN apt-get update -y \
	&& apt-get upgrade -y \
	&& apt-get install -y --no-install-recommends openssl ca-certificates \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/*

COPY --from=healthcheck_builder /healthcheck/target/release/healthcheck /usr/local/bin/healthcheck

ENTRYPOINT ["/usr/local/bin/healthcheck"]