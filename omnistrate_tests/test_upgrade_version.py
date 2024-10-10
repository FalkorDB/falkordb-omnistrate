import sys
import signal
from random import randbytes
from pathlib import Path  # if you haven't already done so
import threading
from redis.exceptions import (
   ConnectionError,
   TimeoutError,
   ReadOnlyError
)
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
from omnistrate_tests.classes.omnistrate_types import TierVersionStatus
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
parser.add_argument(
    "--instance-description", required=False, default="test-upgrade-version"
)
parser.add_argument("--instance-type", required=True)
parser.add_argument("--storage-size", required=False, default="30")
parser.add_argument("--tls", action="store_true")
parser.add_argument("--rdb-config", required=False, default="medium")
parser.add_argument("--aof-config", required=False, default="always")
parser.add_argument("--host-count", required=False, default="6")
parser.add_argument("--cluster-replicas", required=False, default="1")
parser.add_argument("--debug",required=False,default=False)

parser.set_defaults(tls=False)
args = parser.parse_args()

instance: OmnistrateFleetInstance = None


# Intercept exit signals so we can delete the instance before exiting
def signal_handler(sig, frame):
    if instance:
        instance.delete(False)
    sys.exit(0)


if args.debug is False:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def test_upgrade_version():
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

    # 1. List product tier versions
    tiers = omnistrate.list_tier_versions(
        service_id=args.service_id, tier_id=product_tier.product_tier_id
    )

    preferred_tier = next(
        (tier for tier in tiers if tier.status == TierVersionStatus.PREFERRED), None
    )
    if preferred_tier is None:
        raise ValueError("No preferred tier found")

    last_tier = next(
        (tier for tier in tiers if tier.status == TierVersionStatus.ACTIVE), None
    )

    if last_tier is None:
        raise ValueError("No last tier found")

    logging.info(f"Preferred tier: {preferred_tier.version}")
    logging.info(f"Last tier: {last_tier.version}")

    # 2. Create omnistrate instance with previous version
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
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=args.cloud_provider,
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
            hostCount=args.host_count,
            clusterReplicas=args.cluster_replicas,
            product_tier_version=last_tier.version,
        )

        # 3. Add data to the instance
        add_data(instance)

        thread_signal = None
        error_signal = None
        thread = None
        if "standalone" not in args.instance_name:
            thread_signal = threading.Event()
            error_signal = threading.Event()
            thread = threading.Thread(
                target=test_zero_downtime,
                args=(thread_signal, error_signal, instance, args.tls),
            )
            thread.start()

        # 4. Upgrade version for the omnistrate instance
        upgrade_timer = time.time()
        instance.upgrade(
            service_id=args.service_id,
            product_tier_id=product_tier.product_tier_id,
            source_version=last_tier.version,
            target_version=preferred_tier.version,
            wait_until_ready=True,
        )

        if "standalone" not in args.instance_name:
            thread_signal.set()
            thread.join()

        logging.info(f"Upgrade time: {(time.time() - upgrade_timer):.2f}s")

        # 6. Verify the upgrade was successful
        query_data(instance)
    except Exception as e:
        logging.exception(e)
        if args.debug is False:
            instance.delete(False)
        raise e

    # 7. Delete the instance
    instance.delete(False)

    if "standalone" not in args.instance_name and error_signal.is_set():
        raise ValueError("Test failed")
    else:
        logging.info("Test passed")


def add_data(instance: OmnistrateFleetInstance):

    # Get instance host and port
    db = instance.create_connection(ssl=args.tls)

    graph = db.select_graph("test")

    # Write some data to the DB
    graph.query("CREATE (n:Person {name: 'Alice'})")


def query_data(instance: OmnistrateFleetInstance):

    # Get instance host and port
    db = instance.create_connection(ssl=args.tls)

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
    """This function should test the ability to read and write while an upgrade version happens"""
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


if __name__ == "__main__":
    test_upgrade_version()
