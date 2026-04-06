FROM python:3.13-slim

RUN mkdir -p /rebalance

WORKDIR /rebalance

COPY src/falkordb-cluster-rebalance/ /rebalance/

RUN apt-get update && \
  apt-get install -y --no-install-recommends \
  git \
  redis && \
  rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /rebalance/venv && \
  . /rebalance/venv/bin/activate && \
  pip install --upgrade pip && \
  pip install -r /rebalance/src/requirements.txt

# Create a non-root user
RUN useradd -m -u 1000 rebalance && \
    chown -R rebalance:rebalance /rebalance

USER rebalance

CMD ["venv/bin/python", "-u", "/rebalance/src/main.py"]