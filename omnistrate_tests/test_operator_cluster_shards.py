import sys
import signal
from random import randbytes
from pathlib import Path
import threading
import socket
import falkordb
from redis import Redis
from redis.exceptions import ConnectionError


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
parser.add_argument("--debug-command", required=False, default="disabled")

parser.add_argument("--ensure-mz-distribution", action="store_true")
parser.add_argument("--custom-network", required=False)
parser.add_argument("--network-type", required=False, default="PUBLIC")


parser.add_argument(
    "--deployment-create-timeout-seconds", required=False, default=2600, type=int
)
parser.add_argument(
    "--deployment-delete-timeout-seconds", required=False, default=2600, type=int
)
parser.add_argument(
    "--deployment-failover-timeout-seconds", required=False, default=2600, type=int
)

parser.add_argument("--persist-instance-on-fail",action="store_true")

parser.set_defaults(tls=False)
args = parser.parse_args()

instance: OmnistrateFleetInstance = None


# Intercept exit signals so we can delete the instance before exiting
def signal_handler(sig, frame):
    if instance:
        instance.delete(False)
    sys.exit(0)


if not args.persist_instance_on_fail:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

current_host_count = int(args.host_count)
current_cluster_replicas = int(args.cluster_replicas)

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

    network = None
    if args.custom_network:
        network = omnistrate.network(args.custom_network)

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
        deployment_create_timeout_seconds=args.deployment_create_timeout_seconds,
        deployment_delete_timeout_seconds=args.deployment_delete_timeout_seconds,
        deployment_failover_timeout_seconds=args.deployment_failover_timeout_seconds,
    )

    try:
        password = randbytes(16).hex()
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=args.cloud_provider,
            network_type=args.network_type,
            deployment_region=args.region,
            name=args.instance_name,
            description=args.instance_description,
            falkordb_user="falkordb",
            falkordb_password=password,
            nodeInstanceType=args.instance_type,
            enableTLS=args.tls,
            RDBPersistenceConfig=args.rdb_config,
            AOFPersistenceConfig=args.aof_config,
            hostCount=args.host_count,
            clusterReplicas=args.cluster_replicas,
            enableDebugCommand=args.debug_command,
            adminPassword=password,
            custom_network_id=network.network_id if network else None,

        )
        
        try:
            ip = resolve_hostname(instance=instance)
            logging.info(f"Instance endpoint {instance.get_operator_endpoint()[0]['endpoint']} resolved to {ip}")
        except TimeoutError as e:
            logging.error(f"DNS resolution failed: {e}")
            raise Exception("Instance endpoint not ready: DNS resolution failed") from e
        
        thread_signal = threading.Event()
        error_signal = threading.Event()
        thread = threading.Thread(
            target=test_zero_downtime,
            args=(thread_signal, error_signal, instance, args.tls),
        )
        thread.start()

        add_data(instance)

        change_replica_count(instance, int(current_host_count) + 1 ,int(current_cluster_replicas) + 1)

        if args.ensure_mz_distribution:
            logging.info("Testing MZ distribution")
            test_ensure_mz_distribution(instance, password)
        
        query_data(instance)
        change_replica_count(instance, int(current_host_count) - 1 ,int(current_cluster_replicas) - 1)
        
        # Wait for the zero_downtime
        thread_signal.set()
        thread.join()

        query_data(instance)
    except Exception as e:
        logging.exception(e)
        if not args.persist_instance_on_fail:
            instance.delete(network is not None)
        raise e

    # Delete instance
    instance.delete(network is not None)

    if error_signal.is_set():
        raise ValueError("Test failed")
    else:
        logging.info("Test passed")

def add_data(instance: OmnistrateFleetInstance):

    # Get instance host and port
    db = instance.create_connection(ssl=args.tls,operator=True)

    graph = db.select_graph("test")

    # Write some data to the DB
    graph.query("CREATE (n:Person {name: 'Alice'})")

def query_data(instance: OmnistrateFleetInstance):
    logging.info("Retrieving data ....")
    # Get instance host and port
    db = instance.create_connection(ssl=args.tls, force_reconnect=True, operator=True)

    graph = db.select_graph("test")

    # Get info
    result = graph.query("MATCH (n:Person) RETURN n.name")

    if len(result.result_set) == 0:
        raise ValueError("No data found in the graph after upgrade")
    
def test_zero_downtime(
    thread_signal: threading.Event,
    error_signal: threading.Event,
    instance: OmnistrateFleetInstance,
    ssl=False,
):
    """This function should test the ability to read and write while a failover happens"""
    try:
        db = instance.create_connection(ssl=ssl, force_reconnect=True, network_type=args.network_type, operator=True)

        graph = db.select_graph("test")
        
        while not thread_signal.is_set():
            # Write some data to the DB
            graph.query("CREATE (n:Person {name: 'Alice'})")
            graph.ro_query("MATCH (n:Person {name: 'Alice'}) RETURN n")
            time.sleep(3)
    except Exception as e:
        logging.exception(e)
        error_signal.set()
        raise e

def change_replica_count(instance: OmnistrateFleetInstance,host_count: int,new_replicas_count: int):
    """This function should change the number of replicas in the cluster"""
    global current_host_count, current_cluster_replicas
    
    instance_details = instance.get_instance_details()

    resources = instance.get_network_topology(force_refresh=True)


    node_count = [len(resource["nodes"]) for resource in resources.values() 
       if resource.get("resourceName") == args.resource_key ][0]

    if (current_cluster_replicas + current_host_count) != node_count:
        raise Exception("Host count does not match current host count")

    instance.update_params(
        replicaCount=new_replicas_count,
        masterCount=host_count,
        wait_for_ready=True,
    )

    current_host_count = host_count
    current_cluster_replicas = new_replicas_count

    params = (
        instance_details["result_params"]
        if "result_params" in instance_details
        else None
    )

    if not params:
        raise Exception("No result_params found in instance details")

    node_count = [len(resource["nodes"]) for resource in instance.get_network_topology(force_refresh=True).values() 
       if resource.get("resourceName") == args.resource_key ][0]
    
    if (current_cluster_replicas + current_host_count) != node_count:
        raise Exception(
            f"Replica count not updated. Expected {new_replicas_count}, got {node_count}"
        )
    
    logging.info(f"node count updated to {node_count}")
    
def test_ensure_mz_distribution(instance: OmnistrateFleetInstance, password: str):
    """This function should ensure that each shard is distributed across multiple availability zones"""
    instance_details = instance.get_instance_details()
    network_topology: dict = instance.get_network_topology(force_refresh=True)

    params = (
        instance_details["result_params"]
        if "result_params" in instance_details
        else {}
    )

    # Get operator endpoint
    operator_endpoints = instance.get_operator_endpoint()
    if not operator_endpoints:
        raise Exception("No operator endpoints found")

    port = operator_endpoints[0]["ports"][0]

    # Create cluster connection using the operator endpoint
    cluster = FalkorDBCluster(
        host=operator_endpoints[0]["endpoint"],
        port=port,
        username="falkordb",
        password=password,
        ssl=params.get("enableTLS") == "true"
    )

    # Get node groups (each group contains a master and its replicas)
    groups = cluster.groups(1)

    for group in groups:
        group_azs = set()
        for node in group:
            # For operator nodes, hostname pattern is: instance-name-{role}-{index}
            node_name = node.hostname.split('.')[0]  # Get just the node name part
            
            # Find the matching node in network topology using the full node name
            matching_node = None
            for topology_node in network_topology.values():
                if isinstance(topology_node, dict) and "nodes" in topology_node:
                    for n in topology_node["nodes"]:
                        if node_name in n["id"]:
                            matching_node = n
                            break
                if matching_node:
                    break

            if not matching_node:
                logging.warning(f"Node {node.hostname} not found in network topology")
                continue

            group_azs.add(matching_node["availabilityZone"])

        if len(group_azs) == 1:
            raise Exception(f"Group containing nodes {[n.hostname for n in group]} is not distributed across multiple availability zones")

        logging.info(f"Group containing nodes {[n.hostname for n in group]} is distributed across availability zones {group_azs}")

    logging.info("Shards are distributed across multiple availability zones")

def resolve_hostname(instance: OmnistrateFleetInstance,timeout=300, interval=1):
    """Check if the instance's main endpoint is resolvable.
    Args:
        instance: The OmnistrateFleetInstance to check
        timeout: Maximum time in seconds to wait for resolution (default: 30)
        interval: Time in seconds between retry attempts (default: 1)
    
    Returns:
        str: The resolved IP address

    Raises:
        ValueError: If interval or timeout are invalid
        KeyError: If endpoint information is missing
        TimeoutError: If hostname cannot be resolved within timeout
    """
    if interval <= 0 or timeout <= 0:
        raise ValueError("Interval and timeout must be positive")
    
    cluster_endpoint = instance.get_operator_endpoint()
    # if not cluster_endpoint or 'endpoint' not in cluster_endpoint:
    #     raise KeyError("Missing endpoint information in cluster configuration")

    hostname = cluster_endpoint[0]['endpoint']
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            ip = socket.gethostbyname(hostname)
            return ip
        except (socket.gaierror, socket.error) as e:
            logging.debug(f"DNS resolution attempt failed: {e}")
            time.sleep(interval)
     
    raise TimeoutError(f"Unable to resolve hostname '{hostname}' within {timeout} seconds.")

if __name__ == "__main__":
    test_cluster_shards()
