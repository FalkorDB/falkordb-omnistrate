ARG FALKORDB_VERSION=v4.14.0

FROM falkordb/falkordb:$FALKORDB_VERSION

RUN apt-get update && apt-get install -y curl jq openssl cron supervisor gdb

RUN useradd -u 1000 -m falkordb && usermod -aG crontab falkordb && \
  touch /var/run/crond.pid && chown root:crontab /var/run/crond.pid && \
  touch /var/run/supervisord.pid && chown falkordb:falkordb /var/run/supervisord.pid && \
  chmod 660 /var/run/crond.pid && \
  chmod 660 /var/run/supervisord.pid && \
  chmod u+s /usr/sbin/cron


RUN mkdir -p /falkordb

WORKDIR /falkordb

COPY src/falkordb-sentinel/sentinel.conf .
COPY src/falkordb-sentinel/sentinel-entrypoint.sh /usr/local/bin/

RUN chown falkordb:falkordb /falkordb/* && \
  chmod +x /usr/local/bin/sentinel-entrypoint.sh && \
  mkdir -p /var/lib/falkordb && \
  chown falkordb:falkordb /var/lib/falkordb && \
  chmod 700 /var/lib/falkordb

ENV FALKORDB_HOME=/var/lib/falkordb

ENTRYPOINT [ "sentinel-entrypoint.sh" ]