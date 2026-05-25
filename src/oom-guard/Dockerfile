FROM rust:1-slim-bookworm AS builder

RUN apt-get update -y && apt-get install -y pkg-config build-essential cmake libtool openssl libssl-dev

WORKDIR /oom-guard

COPY src/oom-guard/ /oom-guard

RUN cargo build --release

FROM debian:bookworm-slim

RUN DEBIAN_FRONTEND=noninteractive apt-get update -y \
	&& apt-get upgrade -y \
	&& apt-get install -y --no-install-recommends openssl ca-certificates \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/*

COPY --from=builder /oom-guard/target/release/oom-guard /usr/local/bin/oom-guard

ENTRYPOINT ["/usr/local/bin/oom-guard"]
