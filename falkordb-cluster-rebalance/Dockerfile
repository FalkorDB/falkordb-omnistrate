FROM python:3.11-slim-buster

RUN mkdir -p /ebalance

WORKDIR /rebalance

COPY falkordb-cluster-rebalance/ /rebalance/

RUN apt-get update && \
  apt-get install -y --no-install-recommends \
  git

RUN python3 -m venv /rebalance/venv && \
  . /rebalance/venv/bin/activate && \
  pip install --upgrade pip && \
  pip install -r /rebalance/src/requirements.txt


CMD ["venv/bin/python", "/rebalance/src/main.py"]