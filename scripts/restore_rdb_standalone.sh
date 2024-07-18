CONTEXT=${CONTEXT:-'gke_app-plane-dev-f7a2434f_us-central1_c-hcjx5tis6bc'}
NAMESPACE=${NAMESPACE:-'instance-k3g93zqgy'}
RDB_URL=${RDB_URL:-'https://storage.googleapis.com/falkordb_rdbs_test_eu/redis-graph-backup_bk20240716-180700-10519663-ddccm-dev-1_of_1-12-0-16383.rdb?x-goog-signature=02665452fcd4d41eecd4a71a46fadbc647f964ae19da0efa259504423438778f8d3f803dcee96990b6daa05790037387b769ea06d2e7d6241cc2025215a5924cfe87f12102529305b6715b1a82e87bbf55f1d316be19b04beef276cdf504bd225ed17c0cc6ed7040cad130fe8fa6982e36ff742b5307c35f904ca59e93d193020ea80582629a05bdad61c1e52294aad032fe208f77f19524d1a88501295a660a55680f3aa85c590e112ef8f4c2a9dffc112fc0c0f9ece904c9ec3bc789c530097acdcbe8034a31b66c6739d241e8a758463a6082133104e7b70c11e1e46a499bb4d540f24791bc64fd060c87c7a393cd03f3d812848fd93ba6cf6c2dfeb25211&x-goog-algorithm=GOOG4-RSA-SHA256&x-goog-credential=falkordb-rdb-storage-reader%40pipelines-development-f7a2434f.iam.gserviceaccount.com%2F20240717%2Feu%2Fstorage%2Fgoog4_request&x-goog-date=20240717T174207Z&x-goog-expires=3600&x-goog-signedheaders=host'}

kubectl config use-context $CONTEXT

exec_node_0() {
  echo "Running: kubectl -n $NAMESPACE exec node-s-0 -c service -- $@"
  kubectl -n $NAMESPACE exec node-s-0 -c service -- $@
}


delete_config_files() {
  exec_node_0 rm -rf /data/node.conf /data/appendonly.aof
}


ADMIN_PASSWORD=$(exec_node_0 cat /data/node.conf | grep "requirepass" | awk '{print $2}' | sed 's/"//g')

total_time=$(date +%s)
# Copy RDB into instance's disk
echo "$(date): Downloading RDB"
download_time=$(date +%s)
exec_node_0 curl -o /data/new.rdb $RDB_URL
echo "$(date): Downloaded RDB. Elapsed time: $(($(date +%s) - $download_time)) seconds"

# Turn off persistence(RDB and AOF) in instance
echo "$(date): Turning off persistence"
kubectl -n $NAMESPACE exec node-s-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD config set save ""
exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD config set appendonly no

# Replace old RDB with new RDB
echo "$(date): Replacing old RDB with new RDB"
exec_node_0 mv /data/dump.rdb /data/dump.rdb.old
exec_node_0 cp /data/new.rdb /data/dump.rdb

delete_config_files

# Delete pod
echo "$(date): Deleting pods"
kubectl -n $NAMESPACE delete pod node-s-0

time_restart=$(date +%s)

# Wait for instance to be recreated and complete loading the new RDB
echo "$(date): Waiting for instance to be recreated"
sleep 5
kubectl -n $NAMESPACE wait --for=condition=Ready pod/node-s-0 --timeout=10m

is_loading=$(exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info persistence | grep "loading:1")

while [ ! -z "$is_loading" ]; do
  sleep 5
  is_loading=$(exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info persistence | grep "loading:1")
  echo "$(date): Instance is still loading - $(exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info keyspace | grep db)"
done
echo "$(date): Instance has finished loading"

echo "$(date): Elapsed time: $(($(date +%s) - $time_restart)) seconds"
echo "$(date): Elapsed time since start: $(($(date +%s) - $total_time)) seconds"

echo "$(date): Restoration completed." 
echo "$(date): Total elapsed time: $(($(date +%s) - $total_time)) seconds"