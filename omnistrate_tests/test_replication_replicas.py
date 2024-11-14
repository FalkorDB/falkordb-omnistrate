import sys
import signal
from pathlib import Path
from random import randbytes
from redis import Redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry
from redis.exceptions import (
   ConnectionError,
   TimeoutError,
   ReadOnlyError
)
import threading
import socket


file = Path(__file__).resolve()
parent, root = file.parent, file.parents[1]
sys.path.append(str(root))

# Additionally remove the current file's directory from sys.path
from contextlib import suppress

with suppress(ValueError):
    sys.path.remove(str(parent))

import logging
import time
import os
from omnistrate_tests.classes.omnistrate_fleet_instance import OmnistrateFleetInstance
from omnistrate_tests.classes.omnistrate_fleet_api import OmnistrateFleetAPI
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
parser.add_argument("--replica-count", required=False, default="2")
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
        deployment_create_timeout_seconds=2400,
        deployment_delete_timeout_seconds=2400,
        deployment_failover_timeout_seconds=2400
    )

    try:
        password = randbytes(16).hex()
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=args.cloud_provider,
            deployment_region=args.region,
            name=args.instance_name,
            description=args.instance_description,
            falkordb_user="falkordb",
            falkordb_password=password,
            nodeInstanceType=args.instance_type,
            storageSize=args.storage_size,
            enableTLS=args.tls,
            RDBPersistenceConfig=args.rdb_config,
            AOFPersistenceConfig=args.aof_config,
        )

        resolve_hostname(instance=instance,timeout=120)
        
        add_data(instance)

        thread_signal = threading.Event()
        error_signal = threading.Event()
        thread = threading.Thread(
            target=test_zero_downtime, args=(thread_signal, error_signal, instance, args.tls)
        )
        
        thread.start()

        check_data(instance)

        change_replica_count(instance, int(args.replica_count) + 1)

        test_fail_over(instance)

        change_replica_count(instance,int(args.replica_count))
    
        check_data(instance)

        thread_signal.set()
        thread.join()

    except Exception as e:
        logging.exception(e)
        if not args.persist_instance_on_fail:
            instance.delete(False)
        raise e

    # Delete instance
    instance.delete(False)

    if error_signal.is_set():
        raise ValueError("Test failed")
    else:
        logging.info("Test passed")


def change_replica_count(instance: OmnistrateFleetInstance, new_replica_count: int):

    logging.info(f"Changing replica count to {new_replica_count}")
    instance.update_params(
        numReplicas=new_replica_count,
        wait_for_ready=True,
    )

def test_fail_over(instance: OmnistrateFleetInstance):
    logging.info("Testing failover to the newly created replica")

    endpoint = instance.get_cluster_endpoint()
    password = instance.falkordb_password
    id_key = "sz" if args.resource_key == "single-Zone" else "mz"
    retry = Retry(ExponentialBackoff(base=5), retries=20,supported_errors=(TimeoutError,ConnectionError,ConnectionRefusedError,ReadOnlyError))
    try:
        client = Redis(
        host=f"{endpoint["endpoint"]}", port=endpoint['ports'][0],
        username="falkordb", 
        password=password,
        decode_responses=True,
        ssl=args.tls,
        retry=retry,
        retry_on_error=[TimeoutError,ConnectionError,ConnectionRefusedError,ReadOnlyError]
        )
    except Exception as e:
        logging.exception("Failed to connect to Sentinel!")
        logging.info(e)
    
    tout = time.time() + 600
    while True:
        if time.time() > tout:
            raise Exception(f"Failed to failover to node-{id_key}-2")
        try:
            time.sleep(5)
            print(client.execute_command('SENTINEL FAILOVER master'))
            time.sleep(10)
            master = client.execute_command('SENTINEL MASTER master')[3]
            if master.startswith(f"node-{id_key}-2"):
                break
        except Exception as e:
            logging.info(e)
            continue
    time.sleep(15)
    check_data(instance)
    
def add_data(instance: OmnistrateFleetInstance):
    """This function should retrieve the instance host and port for connection, write some data to the DB, then check that the data is there"""
    logging.info('Added data ....')
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
    logging.info('Retrieving data ....')
    # Get instance host and port
    db = instance.create_connection(
        ssl=args.tls,
        force_reconnect=True
    )

    graph = db.select_graph("test")

    result = graph.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data did not persist after host count change")

def test_zero_downtime(
    thread_signal: threading.Event,
    error_signal: threading.Event,
    instance: OmnistrateFleetInstance,
    ssl=False,
):
    """This function should test the ability to read and write while adding and removing a replica"""
    try:
        db = instance.create_connection(ssl=ssl, force_reconnect=True)

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
    
def resolve_hostname(instance: OmnistrateFleetInstance,timeout=30, interval=1):
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
            # Validate basic IP format
            if not all(0 <= int(part) <= 255 for part in ip.split('.')):
                raise socket.error("Invalid IP format")
                return ip
        except (socket.gaierror, socket.error) as e:
            logging.debug(f"DNS resolution attempt failed: {e}")
            time.sleep(interval)
     
    raise TimeoutError(f"Unable to resolve hostname '{hostname}' within {timeout} seconds.")

if __name__ == "__main__":
    test_add_remove_replica()
