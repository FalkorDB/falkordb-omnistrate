FROM debian:bullseye-slim

ARG TARGETARCH

RUN DEBIAN_FRONTEND=noninteractive apt-get update -y \
	&& apt-get install -y --no-install-recommends debconf \
	&& apt-get upgrade -y \
	&& apt-get install -y --no-install-recommends openssl openssl-provider-legacy ca-certificates libssl3t64 \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/*

COPY src/healthcheck_rs/bin/healthcheck-${TARGETARCH} /usr/local/bin/healthcheck
RUN chmod +x /usr/local/bin/healthcheck

ENTRYPOINT ["/usr/local/bin/healthcheck"]