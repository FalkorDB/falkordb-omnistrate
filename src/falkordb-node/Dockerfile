ARG FALKORDB_VERSION=v4.14.4-alpine

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN apk update && \
    apk add --no-cache bash curl jq openssl supervisor gdb ca-certificates openssl

ADD https://secure.globalsign.com/cacert/root-r1.crt /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt

RUN openssl x509 -in /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt -out /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.pem && \
  rm /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt && \
  chmod 644 /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.pem && \
  update-ca-certificates

RUN mkdir -p /var/lib/falkordb

WORKDIR /falkordb

COPY src/falkordb-node/node.conf .
COPY src/falkordb-node/node-entrypoint.sh /usr/local/bin/
COPY scripts/download-debug-so.sh /falkordb/download-debug-so.sh

RUN mkdir -p /var/lib/falkordb && \
  mkdir -p /var/lib/falkordb/scripts && \
  chown 1000 /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chown -R 1000 /var/lib/falkordb/* && \
  chmod +x /usr/local/bin/node-entrypoint.sh && \
  chmod +x /falkordb/download-debug-so.sh

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "node-entrypoint.sh" ]