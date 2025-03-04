import sys
import signal
from random import randbytes
from pathlib import Path  # if you haven't already done so
import socket
import threading
from redis.exceptions import AuthenticationError
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
parser.add_argument("--custom-network", required=False)
parser.add_argument("--network-type", required=False, default="PUBLIC")
parser.add_argument("--is-standalone", action="store_true")

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
parser.set_defaults(is_standalone=False)
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


def test_change_password():
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
        old_password = instance.falkordb_password
        new_password = instance.falkordb_password + "abc"
        try:
            ip = resolve_hostname(instance=instance)
            logging.info(f"Instance endpoint {instance.get_cluster_endpoint()['endpoint']} resolved to {ip}")
        except TimeoutError as e:
            logging.error(f"DNS resolution failed: {e}")
            raise Exception("Instance endpoint not ready: DNS resolution failed") from e
        # Change password
        
        if not args.is_standalone:
            thread_signal = threading.Event()
            error_signal = threading.Event()
            thread = threading.Thread(
            target=test_zero_downtime, args=(thread_signal, error_signal, instance, args.tls, new_password)
            )
            thread.start()

        change_password(instance=instance, password=new_password)
        if not args.is_standalone:
            thread_signal.set()
            thread.join()
        # Test connectivity after password change
        test_connectivity_after_password_change(instance=instance, old_password=old_password, ssl=args.tls)
        
    except Exception as e:
        logging.exception(e)
        if not args.persist_instance_on_fail:
            instance.delete(network is not None)
        raise e

    # Delete instance
    instance.delete(network is not None)

    logging.info("Test passed")


def change_password(instance: OmnistrateFleetInstance, password: str):
    """Change the password of the instance's main user.
    Args:
        instance: The OmnistrateFleetInstance to change the password for
        password: The new password to set
    """
    instance.update_params(
        falkordbPassword=password,
        wait_for_ready=True,
    )
    instance.falkordb_password = password
    logging.info("Password changed successfully")

def test_connectivity_after_password_change(instance: OmnistrateFleetInstance,old_password: str,ssl=False):
    """Test Connectivity between nodes after password change by creating different keys."""
    logging.info("Testing connectivity after password change")
    client = instance.create_connection(ssl=ssl)
    db = client.select_graph('test')
    try:
        db.query("CREATE (n:Person {name: 'Bob'})")
    except Exception as e:
        logging.error(e)

    logging.info("New password works successfully")
    client.connection.close()
    instance.falkordb_password = old_password

    try:
        client = instance.create_connection(ssl=ssl)
    except Exception as e:
        if isinstance(e, AuthenticationError):
            logging.info("Old password failed as expected")
            return
        else:
            logging.error(e)
            raise e
    raise Exception("Old password should not work after password change")

def test_zero_downtime(
    thread_signal: threading.Event,
    error_signal: threading.Event,
    instance: OmnistrateFleetInstance,
    password: str,
    ssl=False,
    max_retries=2,
    retry_delay=5,
):
    """This function should test the ability to read and write while a memory update happens"""
    retries = 0
    db = None
    while retries < max_retries:
        try:
            db = instance.create_connection(ssl=ssl, force_reconnect=False)
            graph = db.select_graph("test")

            while not thread_signal.is_set():
                # Write some data to the DB
                graph.query("CREATE (n:Person {name: 'Alice'})")
                graph.ro_query("MATCH (n:Person {name: 'Alice'}) RETURN n")
                time.sleep(3)
            break  # Exit the retry loop if successful
        except Exception as e:
            logging.exception(e)
            if isinstance(e, AuthenticationError):
                backoff_time = min(retry_delay * (2 ** retries), 30)
                logging.info(f"Authentication error, retrying in {backoff_time} seconds")
                time.sleep(backoff_time)
                if instance.falkordb_password != password:
                    instance.falkordb_password = password
                retries += 1
                if db is not None:
                    db.connection.close()
                logging.info(f"Retrying test_zero_downtime (attempt {retries}/{max_retries})")
            else:
                logging.error(f"Non-authentication error occurred: {str(e)}")
                error_signal.set()
                raise e
    else:
        logging.error(f"Failed after {max_retries} authentication retry attempts")
        error_signal.set()
        raise Exception("Max retries reached")

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
    test_change_password()
