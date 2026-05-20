ARG FALKORDB_VERSION=v4.18.7

FROM falkordb/falkordb-server:$FALKORDB_VERSION

RUN mkdir -p /usr/share/debconf && \
    printf '#!/bin/sh\nexport DEBIAN_HAS_FRONTEND=1\nexec "$@"' > /usr/share/debconf/frontend && \
    chmod +x /usr/share/debconf/frontend && \
    apt-get update && \
    apt-get -f install -y && \
    apt-mark hold tzdata && \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && \
    apt-mark unhold tzdata && \
    ln -sf /bin/true /usr/sbin/update-rc.d && \
    apt-get install -y --no-install-recommends bash curl jq openssl supervisor ca-certificates && \
    apt-get install -y --only-upgrade libgnutls30t64 && \
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
    EXPECTED_SHA="d61b18b488be29f1d04cdaa60e046cc3bc69fb2c6330af7d078ff8592d0e72f5"; \
  elif [ "$MACHINE" = "aarch64" ]; then \
    ARCH="arm64"; \
    EXPECTED_SHA="1a262a4f5d01f37cde6bbdf9e07d461b82f551fea49c4c1cf086153a8bc394bf"; \
  else \
    echo "Unsupported architecture: $MACHINE" && exit 1; \
  fi && \
  echo "Using ARCH: $ARCH, SHA: $EXPECTED_SHA" && \
  URL="https://github.com/FalkorDB/valkey-ldap/releases/download/v0.1.1/libvalkey_ldap-${ARCH}.so" && \
  echo "Downloading from: $URL" && \
  curl -L --retry 3 --retry-delay 5 -o /var/lib/falkordb/bin/valkey_ldap.so "$URL" && \
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