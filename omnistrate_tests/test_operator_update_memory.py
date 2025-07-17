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
parser.add_argument("--replica-id", required=False, default=None)


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
parser.add_argument("--new-instance-type", required=True)
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



def test_update_memory():
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
            custom_network_id=network.network_id if network else None,

        )
        
        try:
            resolved = resolve_hostname(instance=instance)
            logging.info(f"Instance endpoint {resolved['main_endpoint']['hostname']} resolved to {resolved['main_endpoint']['ip']}")
            for node in resolved['nodes']:
                logging.info(f"Node {node['hostname']} ({node['role']}-{node['index']}) resolved to {node['ip']}")
        except TimeoutError as e:
            logging.error(f"DNS resolution failed: {e}")
            raise Exception("Instance endpoint not ready: DNS resolution failed") from e
        
        add_data(instance)

        thread_signal = None
        error_signal = None
        thread = None
        if "standalone" not in args.instance_name:
            # Start a new thread and signal for zero_downtime test
            thread_signal = threading.Event()
            error_signal = threading.Event()
            thread = threading.Thread(
                target=test_zero_downtime,
                args=(thread_signal, error_signal, instance, args.tls),
            )
            thread.start()

        instance.update_instance_type(args.new_instance_type, wait_until_ready=True)
        # Wait for the zero_downtime
        if "standalone" not in args.instance_name:
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
        db = instance.create_connection(ssl=ssl, force_reconnect=True ,network_type=args.network_type, operator=True)

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

def resolve_hostname(instance: OmnistrateFleetInstance, timeout=300, interval=1):
    """Check if the instance's main endpoint and all individual nodes are resolvable.
    Args:
        instance: The OmnistrateFleetInstance to check
        timeout: Maximum time in seconds to wait for resolution (default: 300)
        interval: Time in seconds between retry attempts (default: 1)
    
    Returns:
        dict: Dictionary containing resolved information:
            {
                'main_endpoint': {'hostname': str, 'ip': str},
                'nodes': [{'hostname': str, 'ip': str, 'role': str, 'index': int}, ...]
            }

    Raises:
        ValueError: If interval or timeout are invalid
        KeyError: If endpoint information is missing
        TimeoutError: If hostname cannot be resolved within timeout
    """
    if interval <= 0 or timeout <= 0:
        raise ValueError("Interval and timeout must be positive")
    
    cluster_endpoint = instance.get_operator_endpoint()
    if not cluster_endpoint or not cluster_endpoint[0].get('endpoint'):
        raise KeyError("Missing endpoint information in cluster configuration")
    
    hostname = cluster_endpoint[0]['endpoint']
    domain_suffix = hostname.split('.', 1)[1]
    
    # Correct node count: 3 leaders + 3 followers = 6 total nodes
    leader_nodes = [
        f"{args.instance_name}-leader-{i}.{domain_suffix}"
        for i in range(0, 3)
    ]
    follower_nodes = [
        f"{args.instance_name}-follower-{i}.{domain_suffix}"
        for i in range(0, 3)
    ]
    
    all_nodes = leader_nodes + follower_nodes
    start_time = time.time()

    if not hostname or not all_nodes:
        raise ValueError("Hostname or nodes list is invalid or empty")

    while time.time() - start_time < timeout:
        try:
            # Try to resolve main endpoint
            main_ip = socket.gethostbyname(hostname)
            
            # Try to resolve all individual nodes
            resolved_nodes = []
            for i, node_hostname in enumerate(all_nodes):
                node_ip = socket.gethostbyname(node_hostname)
                role = "leader" if i < 3 else "follower"
                index = i if i < 3 else i - 3
                resolved_nodes.append({
                    'hostname': node_hostname,
                    'ip': node_ip,
                    'role': role,
                    'index': index
                })
            
            return {
                'main_endpoint': {
                    'hostname': hostname,
                    'ip': main_ip
                },
                'nodes': resolved_nodes
            }
            
        except (socket.gaierror, socket.error) as e:
            logging.debug(f"DNS resolution attempt failed: {e}")
            time.sleep(interval)
     
    raise TimeoutError(f"Unable to resolve hostname '{hostname}' and all nodes within {timeout} seconds.")

if __name__ == "__main__":
    test_update_memory()
