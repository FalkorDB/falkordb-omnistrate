ARG ALPINE_IMAGE=alpine:3.22

FROM ${ALPINE_IMAGE} AS builder

RUN mkdir -p /rebalance

WORKDIR /rebalance

COPY src/falkordb-cluster-rebalance/ /rebalance/

RUN apk upgrade --no-cache && \
  apk add --no-cache git py3-pip py3-virtualenv python3

RUN python3 -m venv /rebalance/venv && \
  . /rebalance/venv/bin/activate && \
  pip install --no-cache-dir --upgrade pip && \
  pip install --no-cache-dir -r /rebalance/src/requirements.txt && \
  pip uninstall -y pip

FROM ${ALPINE_IMAGE} AS redis-cli

RUN apk upgrade --no-cache && \
  apk add --no-cache redis

FROM ${ALPINE_IMAGE}

RUN apk upgrade --no-cache && \
  apk add --no-cache python3

WORKDIR /rebalance

COPY --from=builder /rebalance/ /rebalance/
COPY --from=redis-cli /usr/bin/redis-cli /usr/bin/redis-cli

# Create a non-root user
RUN adduser -D -u 1000 rebalance && \
    chown -R rebalance:rebalance /rebalance

USER rebalance

CMD ["venv/bin/python", "-u", "/rebalance/src/main.py"]