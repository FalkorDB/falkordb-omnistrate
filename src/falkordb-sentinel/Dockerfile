ARG FALKORDB_VERSION=v4.16.0-alpine

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN apk update && \
    apk add --no-cache bash curl jq openssl supervisor gdb ca-certificates openssl libc6-compat libgcc libstdc++

ADD https://secure.globalsign.com/cacert/root-r1.crt /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt

RUN openssl x509 -in /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt -out /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.pem && \
  rm /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.crt && \
  chmod 644 /usr/local/share/ca-certificates/GlobalSign_Root_CA_R1.pem && \
  update-ca-certificates

RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY src/falkordb-sentinel/sentinel.conf .
COPY src/falkordb-sentinel/sentinel-entrypoint.sh /usr/local/bin/

# download valkey-ldap.so module
RUN mkdir -p /var/lib/falkordb/bin && \
  MACHINE=$(uname -m) && \
  echo "Detected machine: $MACHINE" && \
  if [ "$MACHINE" = "x86_64" ]; then \
    ARCH="amd64"; \
    EXPECTED_SHA="40f4a06713c98054924b50fa54b8b052eb723d7a3f54ad7bd2a0492cd171683f"; \
  elif [ "$MACHINE" = "aarch64" ]; then \
    ARCH="arm64"; \
    EXPECTED_SHA="b4490155c9de75cb4f8a1fd3f224b5da649b2c9a640bfe932c17ee3a58a38144"; \
  else \
    echo "Unsupported architecture: $MACHINE" && exit 1; \
  fi && \
  echo "Using ARCH: $ARCH, SHA: $EXPECTED_SHA" && \
  URL="https://github.com/FalkorDB/valkey-ldap/releases/download/v0.1.1/libvalkey_ldap-alpine-${ARCH}.so" && \
  echo "Downloading from: $URL" && \
  curl -L -o /var/lib/falkordb/bin/valkey_ldap.so "$URL" && \
  echo "${EXPECTED_SHA}  /var/lib/falkordb/bin/valkey_ldap.so" | sha256sum -c - && \
  chmod 755 /var/lib/falkordb/bin/valkey_ldap.so

RUN mkdir -p /var/lib/falkordb && \
  chown 1000 /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chown -R 1000 /var/lib/falkordb && \
  chmod +x /usr/local/bin/sentinel-entrypoint.sh

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "sentinel-entrypoint.sh" ]