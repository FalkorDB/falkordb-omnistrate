FROM debian:bookworm-slim

ARG TARGETARCH

RUN DEBIAN_FRONTEND=noninteractive apt-get update -y \
	&& apt-get upgrade -y \
	&& apt-get install -y --no-install-recommends ca-certificates \
	&& apt-get install -y --only-upgrade libssl3 openssl \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/*

COPY src/oom-guard/bin/oom-guard-${TARGETARCH} /usr/local/bin/oom-guard
RUN chmod +x /usr/local/bin/oom-guard

ENTRYPOINT ["/usr/local/bin/oom-guard"]
