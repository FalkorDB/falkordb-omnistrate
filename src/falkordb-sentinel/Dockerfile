ARG FALKORDB_VERSION=v4.14.0-alpine

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN apk update && \
    apk add --no-cache bash curl jq openssl supervisor gdb ca-certificates openssl

ADD http://secure.globalsign.com/cacert/root-r1.crt /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt

RUN openssl x509 -in /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt -out /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.pem && \
  rm /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt && \
  chmod 644 /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.pem && \
  update-ca-certificates

RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY src/falkordb-sentinel/sentinel.conf .
COPY src/falkordb-sentinel/sentinel-entrypoint.sh /usr/local/bin/

RUN chown 1000 /falkordb/* && \
  chmod +x /usr/local/bin/sentinel-entrypoint.sh && \
  mkdir -p /var/lib/falkordb && \
  chown 1000 /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "sentinel-entrypoint.sh" ]