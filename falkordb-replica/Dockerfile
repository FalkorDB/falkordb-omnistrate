FROM falkordb/falkordb:edge

ENV MASTER_NAME=master MASTER_PORT=6379 REPLICA_PORT=6379 IS_MASTER=0

# Install curl
RUN apt-get update && apt-get install -y curl

COPY replica-entrypoint.sh /usr/local/bin/

RUN chmod +x /usr/local/bin/replica-entrypoint.sh

EXPOSE $REPLICA_PORT

CMD ["replica-entrypoint.sh"]