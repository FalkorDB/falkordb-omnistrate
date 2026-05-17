FROM python:3.13-alpine

RUN mkdir -p /rebalance

WORKDIR /rebalance

COPY src/falkordb-cluster-rebalance/ /rebalance/

RUN apk add --no-cache git redis

RUN python3 -m venv /rebalance/venv && \
  . /rebalance/venv/bin/activate && \
  pip install --upgrade pip && \
  pip install -r /rebalance/src/requirements.txt

# Create a non-root user
RUN adduser -D -u 1000 rebalance && \
    chown -R rebalance:rebalance /rebalance

USER rebalance

CMD ["venv/bin/python", "-u", "/rebalance/src/main.py"]