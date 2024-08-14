import sys
import signal
from pathlib import Path
from redis import Redis
file = Path(__file__).resolve()
parent, root = file.parent, file.parents[1]
sys.path.append(str(root))

# Additionally remove the current file's directory from sys.path
from contextlib import suppress

with suppress(ValueError):
    sys.path.remove(str(parent))

import time
import os
from omnistrate_tests.classes.omnistrate_fleet_instance import OmnistrateFleetInstance
from omnistrate_tests.classes.omnistrate_fleet_api import OmnistrateFleetAPI
from omnistrate_tests.classes.falkordb_cluster import FalkorDBCluster
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("omnistrate_user")
parser.add_argument("omnistrate_password")
parser.add_argument("cloud_provider", choices=["aws", "gcp"])
parser.add_argument("region")

parser.add_argument(
    "--subscription-id", required=False, default=os.getenv("SUBSCRIPTION_ID")
)
parser.add_argument("--ref-name", required=False, default=os.getenv("REF_NAME"))
parser.add_argument("--service-id", required=True)
parser.add_argument("--environment-id", required=True)
parser.add_argument("--resource-key", required=True)
parser.add_argument("--replica-id", required=True)


parser.add_argument("--instance-name", required=True)
parser.add_argument("--instance-description", required=False, default="test-standalone")
parser.add_argument("--instance-type", required=True)
parser.add_argument("--storage-size", required=False, default="30")
parser.add_argument("--tls", action="store_true")
parser.add_argument("--rdb-config", required=False, default="medium")
parser.add_argument("--aof-config", required=False, default="always")
parser.add_argument("--host-count", required=False, default="6")
parser.add_argument("--cluster-replicas", required=False, default="1")

parser.add_argument("--ensure-mz-distribution", action="store_true")

parser.set_defaults(tls=False)
args = parser.parse_args()

instance: OmnistrateFleetInstance = None

# Intercept exit signals so we can delete the instance before exiting
def signal_handler(sig, frame):
    if instance:
        instance.delete(False)
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def test_add_remove_replica():
    global instance

    omnistrate = OmnistrateFleetAPI(
        email=args.omnistrate_user,
        password=args.omnistrate_password,
    )

    service = omnistrate.get_service(args.service_id)
    product_tier = omnistrate.get_product_tier(
        service_id=args.service_id,
        environment_id=args.environment_id,
        tier_name=args.ref_name,
    )
    service_model = omnistrate.get_service_model(
        args.service_id, product_tier.service_model_id
    )

    print(f"Product tier id: {product_tier.product_tier_id} for {args.ref_name}")

    instance = omnistrate.instance(
        service_id=args.service_id,
        service_provider_id=service.service_provider_id,
        service_key=service.key,
        service_environment_id=args.environment_id,
        service_environment_key=service.get_environment(args.environment_id).key,
        service_model_key=service_model.key,
        service_api_version="v1",
        product_tier_key=product_tier.product_tier_key,
        resource_key=args.resource_key,
        subscription_id=args.subscription_id,
    )

    try:
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=args.cloud_provider,
            deployment_region=args.region,
            name=args.instance_name,
            description=args.instance_description,
            falkordb_user="falkordb",
            falkordb_password="falkordb",
            nodeInstanceType=args.instance_type,
            storageSize=args.storage_size,
            enableTLS=args.tls,
            RDBPersistenceConfig=args.rdb_config,
            AOFPersistenceConfig=args.aof_config,
            hostCount=args.host_count,
            clusterReplicas=args.cluster_replicas,
        )

        add_data(instance)

        check_data(instance)

        change_replica_count(instance, int(args.cluster_replicas) + 2)

        test_fail_over(instance)

    except Exception as e:
        instance.delete(True)
        raise e

    # Delete instance
    instance.delete(True)

    print("Test passed")


def change_replica_count(instance: OmnistrateFleetInstance, new_replica_count: int):

    print(f"Changing host count to {new_replica_count}")
    instance.update_params(
        numReplicas=f"{new_replica_count}",
        wait_for_ready=True,
    )

def test_fail_over(instance: OmnistrateFleetInstance):
    print("Testing failover to the newly created replica")
    endpoints = instance.get_connection_endpoints()
    # fail master
    print(f"failing the master instance {args.replica_id}")

    id_key = "sz" if args.resource_key == "single-Zone" else "mz"

    instance.trigger_failover(
        replica_id=f"node-{id_key}-0",
        wait_for_ready=False,
        resource_id=instance.get_resource_id(f"node-{id_key}"),
    )

    #fail replica at index 2
    instance.trigger_failover(
        replica_id=f"node-{id_key}-1",
        wait_for_ready=False,
        resource_id=instance.get_resource_id(f"node-{id_key}"),
    )

    endpoint = [endpoint['id'] for endpoint in endpoints if id in endpoint.values()][0]

    client = Redis(
    host=f"{endpoint}", port=6379,
    username="falkordb", # use your Redis user. More info https://redis.io/docs/latest/operate/oss_and_stack/management/security/acl/
    password="falkordb", # use your Redis password
    decode_responses=True,
    ssl=True,
    )

    print(client.acl_whoami())

    role = client.info(section='replication').get('role')

    if role == "master":
        check_data(instance)
    else:
        raise Exception('New replica was not promoted as master')
    
    # remove replica
    change_replica_count(instance,2)
    # check if data is still there
    check_data(instance)
    
def add_data(instance: OmnistrateFleetInstance):
    """This function should retrieve the instance host and port for connection, write some data to the DB, then check that the data is there"""

    # Get instance host and port
    db = instance.create_connection(
        ssl=args.tls,
    )

    graph = db.select_graph("test")

    # Write some data to the DB
    graph.query("CREATE (n:Person {name: 'Alice'})")

    result = graph.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data not found after write")


def check_data(instance: OmnistrateFleetInstance):

    # Get instance host and port
    db = instance.create_connection(
        ssl=args.tls,
    )

    graph = db.select_graph("test")

    result = graph.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data did not persist after host count change")


if __name__ == "__main__":
    test_add_remove_replica()
