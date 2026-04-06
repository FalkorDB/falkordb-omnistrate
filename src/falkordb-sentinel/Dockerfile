ARG FALKORDB_VERSION=v4.16.9

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN apt-get update && \
    apt-get install -y --no-install-recommends bash curl jq openssl supervisor ca-certificates && \
    rm -rf /var/lib/apt/lists/*

ADD https://secure.globalsign.com/cacert/root-r1.crt /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt

RUN openssl x509 -in /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt \
    -out /tmp/GlobalSign_Root_CA_R1.crt && \
  mv /tmp/GlobalSign_Root_CA_R1.crt /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt && \
  chmod 644 /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt && \
  update-ca-certificates

RUN rm -f /etc/apt/sources.list.d/nodesource.sources && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY src/falkordb-sentinel/sentinel.conf .
COPY src/falkordb-sentinel/sentinel-entrypoint.sh /usr/local/bin/
COPY src/falkordb-sentinel/split-brain-monitor.sh /usr/local/bin/

RUN mkdir -p /var/lib/falkordb && \
  chown 1000 /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chown -R 1000 /var/lib/falkordb && \
  chmod +x /usr/local/bin/sentinel-entrypoint.sh && \
  chmod +x /usr/local/bin/split-brain-monitor.sh

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "sentinel-entrypoint.sh" ]