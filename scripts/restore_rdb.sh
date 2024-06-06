CONTEXT=${CONTEXT:-'gke_app-plane-dev-f7a2434f_us-central1_c-hcjx5tis6bc'}
NAMESPACE=${NAMESPACE:-'instance-6m6izdn81'}
RDB_URL=${RDB_URL:-'https://storage.googleapis.com/falkordb_rdbs_test_eu/dump_60gb.rdb?x-goog-signature=2a9706d78ade85b0631554fb2cfff7c8fc5d2d33eae17c6a4bcbdb9902d5dc0983d287e11639bbfa86e12b6c062f0daa3b93ae24acb72cf2cf6a36f07296be864f5221cd353c34b42d07cbee64a81ceb75d3196902ee197509018d607ae7591590db948ca6924aa8088d8c5d36a98a0b4256bc04fc8bb5d687b531b05e22f1e23546269a42e73cfca66af08b7e683542d853f07a44075892972efc98e3d088f06569ffca4ada327d8f37fbb929b52676b0709f24f00779e427234f795739848d66837c0eb27ab83290d99ac44d3156be6f9baeb83783d8c46a676209676b689262d162082ab833fa04a5cdf7e71432407e1013c4e70f1a6d95edb1eb038d6aca&x-goog-algorithm=GOOG4-RSA-SHA256&x-goog-credential=falkordb-rdb-storage-reader%40pipelines-development-f7a2434f.iam.gserviceaccount.com%2F20240530%2Feu%2Fstorage%2Fgoog4_request&x-goog-date=20240530T125036Z&x-goog-expires=604800&x-goog-signedheaders=host'}

kubectl config use-context $CONTEXT

exec_node_0() {
  echo "Running: kubectl -n $NAMESPACE exec node-sz-0 -c service -- $@"
  kubectl -n $NAMESPACE exec node-sz-0 -c service -- $@
}

exec_node_1() {
  kubectl -n $NAMESPACE exec node-sz-1 -c service -- $@
}

exec_sentinel_0() {
  kubectl -n $NAMESPACE exec sentinel-sz-0 -c service -- $@
}

delete_config_files() {
  exec_node_0 rm -rf /data/node.conf /data/sentinel.conf /data/appendonly.aof
  exec_node_1 rm -rf /data/node.conf /data/sentinel.conf /data/appendonly.aof
  exec_sentinel_0 rm -rf /data/sentinel.conf
}


ADMIN_PASSWORD=$(exec_node_0 cat /data/node.conf | grep "requirepass" | awk '{print $2}' | sed 's/"//g')

# MAke sure node-sz-0 is master and node-sz-1 is replica
exec_sentinel_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD -p 26379 sentinel remove master
exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD -p 26379 sentinel remove master
exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD -p 26379 sentinel remove master

replicaof=$(exec_node_1 cat /data/node.conf | grep "replicaof" | awk '{print $2 "\t" $3}')

echo "$(date): Node 1 is replica of: $replicaof"
exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD replicaof "no one"
exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD config rewrite

total_time=$(date +%s)
# Copy RDB into master's disk
echo "$(date): Downloading RDB"
download_time=$(date +%s)
exec_node_0 curl -o /data/new.rdb $RDB_URL & exec_node_1 curl -o /data/new.rdb $RDB_URL && fg
echo "$(date): Downloaded RDB. Elapsed time: $(($(date +%s) - $download_time)) seconds"

# Turn off persistence(RDB and AOF) in master
echo "$(date): Turning off persistence"
kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD config set save ""
exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD config set appendonly no

# Replace old RDB with new RDB
echo "$(date): Replacing old RDB with new RDB"
exec_node_0 mv /data/dump.rdb /data/dump.rdb.old
exec_node_0 cp /data/new.rdb /data/dump.rdb
exec_node_1 cp /data/new.rdb /data/dump.rdb

delete_config_files

# Delete pod
echo "$(date): Deleting pods"
kubectl -n $NAMESPACE delete pod node-sz-0
kubectl -n $NAMESPACE delete pod sentinel-sz-0

time_restart=$(date +%s)

# Wait for master to be recreated and complete loading the new RDB
echo "$(date): Waiting for master to be recreated"
sleep 5
kubectl -n $NAMESPACE wait --for=condition=Ready pod/node-sz-0 --timeout=10m
kubectl -n $NAMESPACE wait --for=condition=Ready pod/sentinel-sz-0 --timeout=10m

is_loading=$(exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info persistence | grep "loading:1")

while [ ! -z "$is_loading" ]; do
  sleep 5
  is_loading=$(exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info persistence | grep "loading:1")
  echo "$(date): Master is still loading - $(exec_node_0 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info keyspace | grep db)"
done
echo "$(date): Master has finished loading"

echo "$(date): Elapsed time: $(($(date +%s) - $time_restart)) seconds"
echo "$(date): Elapsed time since start: $(($(date +%s) - $total_time)) seconds"


exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD config set replica-priority 0
exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD config rewrite

kubectl -n $NAMESPACE delete pod node-sz-1
sleep 5
kubectl -n $NAMESPACE wait --for=condition=Ready pod/node-sz-1 --timeout=10m

exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD replicaof $replicaof

replica_sync=$(exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info replication | grep "master_sync_in_progress:1")
while [ ! -z "$replica_sync" ]; do
  sleep 5
  replica_sync=$(exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info replication | grep "master_sync_in_progress:1")
  echo "$(date): Replica is still syncing - $(exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD info keyspace | grep db)"
done

exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD config set replica-priority 100
exec_node_1 redis-cli --no-auth-warning -a $ADMIN_PASSWORD config rewrite

echo "$(date): Replica is synced"

echo "$(date): Restoration completed." 
echo "$(date): Total elapsed time: $(($(date +%s) - $total_time)) seconds"



# DB Size: 58GB
# RDB Size: 1.8GB
# Graphs: 64k
# Time to copy RDB: 59 seconds
# Time until master is ready: 355 seconds
# Time until master & replica is synced: 638 seconds