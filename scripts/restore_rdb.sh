CONTEXT=${CONTEXT:-'gke_app-plane-dev-f7a2434f_europe-west1_c-hcp3s8rxopv'}
NAMESPACE=${NAMESPACE:-'instance-srq226f0o'}
RDB_URL=${RDB_URL:-'https://storage.googleapis.com/falkordb_rdbs_test_eu/dump_60gb.rdb?x-goog-signature=2a9706d78ade85b0631554fb2cfff7c8fc5d2d33eae17c6a4bcbdb9902d5dc0983d287e11639bbfa86e12b6c062f0daa3b93ae24acb72cf2cf6a36f07296be864f5221cd353c34b42d07cbee64a81ceb75d3196902ee197509018d607ae7591590db948ca6924aa8088d8c5d36a98a0b4256bc04fc8bb5d687b531b05e22f1e23546269a42e73cfca66af08b7e683542d853f07a44075892972efc98e3d088f06569ffca4ada327d8f37fbb929b52676b0709f24f00779e427234f795739848d66837c0eb27ab83290d99ac44d3156be6f9baeb83783d8c46a676209676b689262d162082ab833fa04a5cdf7e71432407e1013c4e70f1a6d95edb1eb038d6aca&x-goog-algorithm=GOOG4-RSA-SHA256&x-goog-credential=falkordb-rdb-storage-reader%40pipelines-development-f7a2434f.iam.gserviceaccount.com%2F20240530%2Feu%2Fstorage%2Fgoog4_request&x-goog-date=20240530T125036Z&x-goog-expires=604800&x-goog-signedheaders=host'}

kubectl config use-context $CONTEXT

ADMIN_PASSWORD=$(kubectl -n $NAMESPACE exec node-sz-0 -c service -- cat /data/node.conf | grep "requirepass" | awk '{print $2}' | sed 's/"//g')

# MAke sure node-sz-0 is master and node-sz-1 is replica
echo "$(date): Checking if node-sz-0 is master"
master=$(kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD info replication | grep "role:master")
if [ -z "$master" ]; then
  kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD replicaof no one
fi

total_time=$(date +%s)
# Copy RDB into master's disk
echo "$(date): Downloading RDB"
download_time=$(date +%s)
kubectl -n $NAMESPACE exec node-sz-0 -c service -- curl -o /data/new.rdb $RDB_URL
echo "$(date): Downloaded RDB. Elapsed time: $(($(date +%s) - $download_time)) seconds"

kubectl -n $NAMESPACE exec sentinel-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD sentinel remove master

# Turn off persistence(RDB and AOF) in master
echo "$(date): Turning off persistence"
kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD config set save ""
kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD config set appendonly no

# Delete appendonly folder
echo "$(date): Deleting appendonly folder"
kubectl -n $NAMESPACE exec node-sz-0 -c service -- rm -rf /data/appendonlydir

# Replace old RDB with new RDB
echo "$(date): Replacing old RDB with new RDB"
kubectl -n $NAMESPACE exec node-sz-0 -c service -- cp /data/dump.rdb /data/dump.old.rdb
kubectl -n $NAMESPACE exec node-sz-0 -c service -- cp /data/new.rdb /data/dump.rdb

# Delete pod
echo "$(date): Deleting pod"
kubectl -n $NAMESPACE delete pod sentinel-sz-0
kubectl -n $NAMESPACE delete pod node-sz-1
kubectl -n $NAMESPACE delete pod node-sz-0

time_restart=$(date +%s)

# Wait for master to be recreated and complete loading the new RDB
echo "$(date): Waiting for master to be recreated"
sleep 5
kubectl -n $NAMESPACE wait --for=condition=Ready pod/node-sz-0 --timeout=10m
kubectl -n $NAMESPACE wait --for=condition=Ready pod/node-sz-1 --timeout=10m
kubectl -n $NAMESPACE wait --for=condition=Ready pod/sentinel-sz-0 --timeout=10m

is_loading=$(kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD info persistence | grep "loading:1")

while [ ! -z "$is_loading" ]; do
  sleep 5
  is_loading=$(kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD info persistence | grep "loading:1")
  echo "$(date): Master is still loading - $(kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD info keyspace | grep db)"
done
echo "$(date): Master has finished loading with size $(kubectl -n $NAMESPACE exec node-sz-0 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD info | grep 'used_memory_human')"

echo "$(date): Elapsed time: $(($(date +%s) - $time_restart)) seconds"
echo "$(date): Elapsed time since start: $(($(date +%s) - $total_time)) seconds"

replica_sync=$(kubectl -n $NAMESPACE exec node-sz-1 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD info replication | grep "master_sync_in_progress:1")

while [ ! -z "$replica_sync" ]; do
  sleep 5
  replica_sync=$(kubectl -n $NAMESPACE exec node-sz-1 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD info replication | grep "master_sync_in_progress:1")
  echo "$(date): Replica is still syncing - $(kubectl -n $NAMESPACE exec node-sz-1 -c service -- redis-cli --no-auth-warning -a $ADMIN_PASSWORD info keyspace | grep db)"
done

echo "$(date): Replica is synced"

echo "$(date): Restoration completed." 
echo "$(date): Total elapsed time: $(($(date +%s) - $total_time)) seconds"



# DB Size: 58GB
# RDB Size: 1.8GB
# Graphs: 64k
# Time to copy RDB: 30 seconds
# Time until master is ready: 377 seconds
# Time until master & replica is synced: 381 seconds