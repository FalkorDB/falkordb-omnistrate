FROM falkordb/falkordb:edge

ENV MASTER_NAME=master MASTER_PORT=6379

CMD redis-server --slaveof $MASTER_NAME $MASTER_PORT --loadmodule /FalkorDB/bin/src/falkordb.so
