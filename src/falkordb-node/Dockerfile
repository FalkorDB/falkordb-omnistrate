ARG FALKORDB_VERSION=v4.16.0-alpine

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN apk update && \
    apk add --no-cache bash curl jq openssl supervisor gdb ca-certificates openssl libc6-compat libgcc libstdc++

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
RUN mkdir -p /var/lib/falkordb/bin && \
  MACHINE=$(uname -m) && \
  echo "Detected machine: $MACHINE" && \
  if [ "$MACHINE" = "x86_64" ]; then \
    ARCH="amd64"; \
    EXPECTED_SHA="0859a29330adff45b55c700cb088dfc5684bc08e352222d6df4bef612145c594"; \
  elif [ "$MACHINE" = "aarch64" ]; then \
    ARCH="arm64"; \
    EXPECTED_SHA="3e7f1a48951d9210d98bdc6715a91aec9851b366a1dd457102588c9d6ff84f81"; \
  else \
    echo "Unsupported architecture: $MACHINE" && exit 1; \
  fi && \
  echo "Using ARCH: $ARCH, SHA: $EXPECTED_SHA" && \
  URL="https://github.com/FalkorDB/valkey-ldap/releases/download/v0.1.0/libvalkey_ldap-alpine-${ARCH}.so" && \
  echo "Downloading from: $URL" && \
  curl -L -o /var/lib/falkordb/bin/valkey_ldap.so "$URL" && \
  ls -lh /var/lib/falkordb/bin/valkey_ldap.so && \
  sha256sum /var/lib/falkordb/bin/valkey_ldap.so && \
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