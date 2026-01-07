ARG FALKORDB_VERSION=v4.16.0-alpine

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

# download valkey-ldap.so module
ARG TARGETARCH
RUN ARCH=${TARGETARCH:-amd64} && \
  if [ "$ARCH" = "amd64" ]; then \
    EXPECTED_SHA="71406b86934844d26a088d4f6da6b5aebfdcb3d528d53c61b16d66ab4b964771"; \
  elif [ "$ARCH" = "arm64" ]; then \
    EXPECTED_SHA="3c98648ac3a8c51b79c91c4bbdcbece79f5640c6b7c9c9b0a71a98af8c7100c1"; \
  else \
    echo "Unsupported architecture: $ARCH" && exit 1; \
  fi && \
  curl -o /var/lib/falkordb/bin/valkey_ldap.so https://github.com/FalkorDB/valkey-ldap/releases/download/v0.0.4/libvalkey_ldap-${ARCH}.so && \
  echo "${EXPECTED_SHA}  /var/lib/falkordb/bin/valkey_ldap.so" | sha256sum -c - && \
  chmod 755 /var/lib/falkordb/bin/valkey_ldap.so

RUN mkdir -p /var/lib/falkordb && \
  mkdir -p /var/lib/falkordb/scripts && \
  chown 1000 /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chown -R 1000 /var/lib/falkordb/* && \
  chmod +x /usr/local/bin/node-entrypoint.sh && \
  chmod +x /falkordb/download-debug-so.sh

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "node-entrypoint.sh" ]