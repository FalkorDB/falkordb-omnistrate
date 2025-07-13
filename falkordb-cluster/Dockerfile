ARG FALKORDB_VERSION=v4.8.8

FROM falkordb/falkordb:$FALKORDB_VERSION

RUN apt-get update && apt-get install -y curl jq openssl cron gdb

RUN useradd -u 1000 -m falkordb && usermod -aG crontab falkordb && \
  touch /var/run/crond.pid && chown root:crontab /var/run/crond.pid \
  && chmod 660 /var/run/crond.pid \
  && chmod u+s /usr/sbin/cron


WORKDIR /falkordb

COPY falkordb-cluster/node.conf .
COPY falkordb-cluster/cluster-entrypoint.sh /usr/local/bin/
COPY --from=redis_exporter /redis_exporter /usr/local/bin/
COPY --from=healthcheck_builder /healthcheck/target/release/healthcheck /usr/local/bin/healthcheck
COPY scripts/download-debug-so.sh .

RUN chown falkordb:falkordb /falkordb/* && \
  chmod +x /usr/local/bin/cluster-entrypoint.sh && \
  mkdir -p /var/lib/falkordb && \
  chown falkordb:falkordb /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb && \
  chmod +x ./download-debug-so.sh

ENV FALKORDB_HOME=/var/lib/falkordb

CMD ["cluster-entrypoint.sh"]