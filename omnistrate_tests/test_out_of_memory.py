import sys
import signal
from random import randbytes
from pathlib import Path  # if you haven't already done so
import socket

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
import argparse
from redis.exceptions import ResponseError, OutOfMemoryError
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
parser.add_argument("--persist-instance-on-fail",action="store_true")
parser.add_argument("--custom-network", required=False)
parser.add_argument("--network-type", required=False, default="PUBLIC")
parser.add_argument("--host-count", required=False, default="6")
parser.add_argument("--cluster-replicas", required=False, default="1")

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


def test_out_of_memory():
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
        service_provider_id='sp-JvkxkPhinN',
        service_key=service.key,
        service_environment_id=args.environment_id,
        service_environment_key=service.get_environment(args.environment_id).key,
        service_model_key=service_model.key,
        service_api_version="v1",
        product_tier_key=product_tier.product_tier_key,
        resource_key=args.resource_key,
        subscription_id=args.subscription_id,
        deployment_create_timeout_seconds=2400,
        deployment_delete_timeout_seconds=2400,
        deployment_failover_timeout_seconds=2400,
    )

    try:
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=args.cloud_provider,
            network_type=args.network_type,
            deployment_region=args.region,
            name=args.instance_name,
            description=args.instance_description,
            falkordb_user="falkordb",
            falkordb_password=randbytes(16).hex(),
            nodeInstanceType=args.instance_type,
            storageSize=args.storage_size,
            enableTLS=args.tls,
            RDBPersistenceConfig=args.rdb_config,
            AOFPersistenceConfig=args.aof_config,
            custom_network_id=network.network_id if network else None,
        )
        
        try:
            ip = resolve_hostname(instance=instance)
            logging.info(f"Instance endpoint {instance.get_cluster_endpoint()['endpoint']} resolved to {ip}")
        except TimeoutError as e:
            logging.error(f"DNS resolution failed: {e}")
            raise Exception("Instance endpoint not ready: DNS resolution failed") from e
        
        # Stress test the instance
        stress_test_out_of_memory(instance,resource_key=args.resource_key)
    except Exception as e:
        logging.exception(e)
        if not args.persist_instance_on_fail:
            instance.delete(network is not None)
        raise e

    # Delete instance
    instance.delete(network is not None)

    logging.info("Test passed")



def stress_test_out_of_memory(instance: OmnistrateFleetInstance,resource_key: str = None):
    """Stress test the instance by allocating memory until it runs out of memory.
    Should return this: '(error) OOM command not allowed when used memory > 'maxmemory''
    Args:
        instance: The OmnistrateFleetInstance to stress test
    """
    medQuery = "UNWIND RANGE(1, 10000) AS id CREATE (n:Person {name: 'Alice'})"

    logging.info("Starting stress test")
    db = instance.create_connection(
        ssl=args.tls,
    )
    graph = db.select_graph("test")
    
    tout = time.time() + 1200
    while True:
        if time.time() > tout:
            raise Exception(f"Took too long to reach out of memory error")
        try:
            response = graph.query(medQuery)
            logging.info(f"Response: {response}")
        except Exception as e:
            if isinstance(e, OutOfMemoryError) and 'memory' in str(e):
                logging.info("Out of memory error detected")
                break
            logging.error(f"{e}")
            logging.error("Failed to detect out of memory error")
            raise e
        

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
    
    cluster_endpoint = instance.get_cluster_endpoint()

    if not cluster_endpoint or 'endpoint' not in cluster_endpoint:
        raise KeyError("Missing endpoint information in cluster configuration")

    hostname = cluster_endpoint['endpoint']
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
    test_out_of_memory()
