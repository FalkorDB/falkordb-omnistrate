FROM falkordb/falkordb:edge

ENV MASTER_NAME=master MASTER_PORT=6379 REPLICA_PORT=6379

CMD redis-server --replicaof $MASTER_NAME $MASTER_PORT --loadmodule /FalkorDB/bin/src/falkordb.so --replica-announce-ip $REPLICA_IP --replica-announce-port $REPLICA_PORT
