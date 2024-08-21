import sys
import signal
from pathlib import Path

file = Path(__file__).resolve()
parent, root = file.parent, file.parents[1]
sys.path.append(str(root))

# Additionally remove the current file's directory from sys.path
from contextlib import suppress

with suppress(ValueError):
    sys.path.remove(str(parent))

import logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(message)s")

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

current_host_count = int(args.host_count)


# Intercept exit signals so we can delete the instance before exiting
def signal_handler(sig, frame):
    if instance:
        instance.delete(False)
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def test_cluster_shards():
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

    logging.info(f"Product tier id: {product_tier.product_tier_id} for {args.ref_name}")

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
        deployment_create_timeout_seconds=60*60*30
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

        change_host_count(instance, int(args.host_count) + 2)

        if args.ensure_mz_distribution:
            test_ensure_mz_distribution(instance)

        check_data(instance)

        change_host_count(instance, int(args.host_count))

        if args.ensure_mz_distribution:
            test_ensure_mz_distribution(instance)

        check_data(instance)
    except Exception as e:
        logging.exception(e)
        instance.delete(True)
        raise e

    # Delete instance
    instance.delete(True)

    logging.info("Test passed")


def change_host_count(instance: OmnistrateFleetInstance, new_host_count: int):
    global current_host_count

    logging.info(f"Changing host count to {new_host_count}")
    instance.update_params(
        hostCount=f"{new_host_count}",
        wait_for_ready=True,
    )
    current_host_count = new_host_count

    instance_details = instance.get_instance_details()

    params = (
        instance_details["result_params"]
        if "result_params" in instance_details
        else None
    )

    if not params:
        raise Exception("No result_params found in instance details")

    host_count = int(params["hostCount"]) if "hostCount" in params else None

    if not host_count:
        raise Exception("No hostCount found in instance details")

    if host_count != current_host_count:
        raise Exception("Host count does not match new host count")


def test_ensure_mz_distribution(instance: OmnistrateFleetInstance):
    """This function should ensure that each shard is distributed across multiple availability zones"""

    instance_details = instance.get_instance_details()
    network_topology: dict = instance.get_network_topology()

    params = (
        instance_details["result_params"]
        if "result_params" in instance_details
        else None
    )

    if not params:
        raise Exception("No result_params found in instance details")

    cluster_replicas = (
        int(params["clusterReplicas"]) if "clusterReplicas" in params else None
    )

    if not cluster_replicas:
        raise Exception("No clusterReplicas found in instance details")

    resource_key = next(
        (k for [k, v] in network_topology.items() if v["resourceName"] == "cluster-mz"),
        None,
    )

    resource = network_topology[resource_key]

    nodes = resource["nodes"]

    if len(nodes) == 0:
        raise Exception("No nodes found in network topology")

    if len(nodes) != current_host_count:
        raise Exception("Host count does not match number of nodes")

    cluster = FalkorDBCluster(
        host=resource["clusterEndpoint"],
        port=resource["clusterPorts"][0],
        username="falkordb",
        password="falkordb",
        ssl=params["enableTLS"] == "true" if "enableTLS" in params else False,
    )

    groups = cluster.groups(cluster_replicas)

    for group in groups:
        group_azs = set()
        for node in group:
            omnistrateNode = next(
                (n for n in nodes if n["endpoint"] == node.hostname), None
            )
            if not omnistrateNode:
                raise Exception(f"Node {node.hostname} not found in network topology")

            group_azs.add(omnistrateNode["availabilityZone"])

        if len(group_azs) == 1:
            raise Exception(
                "Group is not distributed across multiple availability zones"
            )

        logging.info(
            f"Group {group} is distributed across availability zones {group_azs}"
        )

    logging.info("Shards are distributed across multiple availability zones")


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
    test_cluster_shards()
