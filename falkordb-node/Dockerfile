FROM oliver006/redis_exporter:alpine as redis_exporter


FROM golang:alpine3.19 as healthcheck_builder

WORKDIR /healthchek

COPY ./healthcheck /healthchek

RUN go build -o /healthchek/healthcheck


FROM falkordb/falkordb:edge

# Install curl
RUN apt-get update && apt-get install -y curl

RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY sentinel.conf .
COPY node.conf .
COPY node-entrypoint.sh /usr/local/bin/
COPY --from=redis_exporter /redis_exporter /usr/local/bin/
COPY --from=healthcheck_builder /healthchek/healthcheck /usr/local/bin/

RUN chown redis:redis /falkordb/* && \
  chmod +x /usr/local/bin/node-entrypoint.sh

CMD ["node-entrypoint.sh"]