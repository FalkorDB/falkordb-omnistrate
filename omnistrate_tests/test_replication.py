import sys
import signal
from random import randbytes
from pathlib import Path
import threading
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import (
    TimeoutError,
    ConnectionError,
    BusyLoadingError,
    ReadOnlyError
)

file = Path(__file__).resolve()
parent, root = file.parent, file.parents[1]
sys.path.append(str(root))

# Additionally remove the current file's directory from sys.path
try:
    sys.path.remove(str(parent))
except ValueError:  # Already removed
    pass

import logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(message)s")

import time
import os
from omnistrate_tests.classes.omnistrate_fleet_instance import OmnistrateFleetInstance
from omnistrate_tests.classes.omnistrate_fleet_api import OmnistrateFleetAPI
import argparse
from falkordb import FalkorDB
from redis import Sentinel
import random


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
parser.add_argument(
    "--resource-key", required=True, choices=["single-Zone", "multi-Zone"]
)


parser.add_argument("--instance-name", required=True)
parser.add_argument(
    "--instance-description", required=False, default="test-replication"
)
parser.add_argument("--instance-type", required=True)
parser.add_argument("--storage-size", required=False, default="30")
parser.add_argument("--tls", action="store_true")
parser.add_argument("--rdb-config", required=False, default="medium")
parser.add_argument("--aof-config", required=False, default="always")

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


def test_replication():
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

        thread_signal = threading.Event()
        error_signal = threading.Event()
        thread = threading.Thread(
            target=test_zero_downtime, args=(thread_signal, error_signal, instance, args.tls)
        )
        thread.start()

        # Test failover and data loss
        test_failover(instance, password)

        # Wait for the zero_downtime
        thread_signal.set()
        thread.join()

        # Test stop and start instance
        test_stop_start(instance, password)
    except Exception as e:
        logging.exception(e)
        instance.delete(False)
        raise e

    # Delete instance
    instance.delete(False)

    if error_signal.is_set():
        raise ValueError("Test failed")
    else:
        logging.info("Test passed")


def test_failover(instance: OmnistrateFleetInstance, password: str):
    """
    Single Zone tests are the following:
    1. Create a single zone instance
    2. Write some data to the master node
    3. Trigger a failover for the master node
    4. Wait until the sentinels promote a new master
    5. Check if the data is still there
    6. Write more data to the new master
    7. Trigger a failover for one of the sentinels
    8. Make sure we can still connect and read the data
    9. Trigger a failover for the new master
    10. Wait until the sentinels promote a new master
    11. Make sure we still have the both writes in the new master and slave
    12. Delete the instance
    """

    resources = instance.get_connection_endpoints()
    db_resource = list(
        filter(lambda resource: resource["id"].startswith("node-"), resources)
    )
    db_resource.sort(key=lambda resource: resource["id"])
    sentinel_resource = next(
        (resource for resource in resources if resource["id"].startswith("sentinel-")),
        None,
    )
    db_0 = FalkorDB(
        host=db_resource[0]["endpoint"],
        port=db_resource[0]["ports"][0],
        username="falkordb",
        password=password,
        ssl=args.tls,
    )
    db_1 = FalkorDB(
        host=db_resource[1]["endpoint"],
        port=db_resource[1]["ports"][0],
        username="falkordb",
        password=password,
        ssl=args.tls,
    )

    retry = Retry(ExponentialBackoff(base=3,cap=30),30,supported_errors=(
        TimeoutError,
        ConnectionError,
        ConnectionRefusedError
    ))
    
    sentinels = Sentinel(
        sentinels=[
            (sentinel_resource["endpoint"], sentinel_resource["ports"][0]),
            (db_resource[0]["endpoint"], db_resource[0]["ports"][1]),
            (db_resource[1]["endpoint"], db_resource[1]["ports"][1]),
        ],
        sentinel_kwargs={
            "username": "falkordb",
            "password": password,
            "ssl": args.tls,
        },
        connection_kwargs={
            "username": "falkordb",
            "password": password,
            "ssl": args.tls,
            "retry": retry
        },
    )

    sentinels_list = random.choice(sentinels.sentinels).execute_command(
        "sentinel sentinels master"
    )

    if len(sentinels_list) != 2:
        raise Exception(
            f"Sentinel list not correct. Expected 2, got {len(sentinels_list)}"
        )

    graph_0 = db_0.select_graph("test")

    # Write some data to the DB
    graph_0.query("CREATE (n:Person {name: 'Alice'})")

    # Check if data was replicated
    graph_1 = db_1.select_graph("test")

    result = graph_1.ro_query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data was not replicated to the slave")

    id_key = "sz" if args.resource_key == "single-Zone" else "mz"

    logging.info(f"Triggering failover for node-{id_key}-0")
    # Trigger failover
    instance.trigger_failover(
        replica_id=f"node-{id_key}-0",
        wait_for_ready=False,
        resource_id=instance.get_resource_id(f"node-{id_key}"),
    )

    promotion_completed = False
    while not promotion_completed:
        try:
            graph = db_1.execute_command("info replication")
            if "role:master" in graph:
                promotion_completed = True
            time.sleep(5)
        except Exception as e:
            logging.info("Promotion not completed yet")
            time.sleep(5)

    logging.info("Promotion completed")

    # Check if data is still there
    graph_1 = db_1.select_graph("test")

    result = graph_1.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data lost after first failover")

    logging.info("Data persisted after first failover")

    graph_1.query("CREATE (n:Person {name: 'Bob'})")

    result = graph_1.query("MATCH (n:Person) RETURN n")

    logging.info(f"result after bob: {result.result_set}")

    # wait until the node 0 is ready
    instance.wait_for_instance_status(timeout_seconds=600)

    logging.info(f"Triggering failover for sentinel-{id_key}-0")
    # Trigger sentinel failover
    instance.trigger_failover(
        replica_id=f"sentinel-{id_key}-0",
        wait_for_ready=False,
        resource_id=instance.get_resource_id(f"sentinel-{id_key}"),
    )
    print("sleeping to see if it is a latency issue..")
    time.sleep(120)
    graph_1 = db_1.select_graph("test")

    result = graph_1.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) < 2:
        raise Exception("Data lost after second failover")

    logging.info("Data persisted after second failover")

    # wait until the node 0 is ready
    instance.wait_for_instance_status(timeout_seconds=600)

    logging.info(f"Triggering failover for node-{id_key}-1")
    # Trigger failover
    instance.trigger_failover(
        replica_id=f"node-{id_key}-1",
        wait_for_ready=False,
        resource_id=instance.get_resource_id(f"node-{id_key}"),
    )

    promotion_completed = False
    while not promotion_completed:
        try:
            graph = db_0.execute_command("info replication")
            if "role:master" in graph:
                promotion_completed = True
            time.sleep(5)
        except Exception as e:
            logging.info("Promotion not completed yet")
            time.sleep(5)

    logging.info("Promotion completed")

    # Check if data is still there
    graph_0 = db_0.select_graph("test")

    result = graph_0.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) < 2:
        logging.info(result.result_set)
        raise Exception("Data lost after third failover")

    logging.info("Data persisted after third failover")


def test_stop_start(instance: OmnistrateFleetInstance, password: str):
    """
    Single Zone tests are the following:
    1. Create a single zone instance
    2. Write some data to the master node
    3. Stop the master node
    4. Make sure we can still connect and read the data
    5. Start the master node
    6. Make sure we can still connect and read the data
    7. Delete the instance
    """

    resources = instance.get_connection_endpoints()
    sentinel_resource = next(
        (resource for resource in resources if resource["id"].startswith("sentinel-")),
        None,
    )
    db = FalkorDB(
        host=sentinel_resource["endpoint"],
        port=sentinel_resource["ports"][0],
        username="falkordb",
        password=password,
        ssl=args.tls,
    )

    graph = db.select_graph("test")

    # Write some data to the DB
    graph.query("CREATE (n:Person {name: 'Alice'})")

    logging.info("Stopping node")

    instance.stop(wait_for_ready=True)

    logging.info("Instance stopped")

    instance.start(wait_for_ready=True)
    
    graph = db.select_graph("test")

    result = graph.query("MATCH (n:Person) RETURN n")

    print("PASSED QUERY")
    if len(result.result_set) == 0:
        raise Exception("Data lost after stop/start")

    logging.info("Instance started")


def test_zero_downtime(
    thread_signal: threading.Event,
    error_signal: threading.Event,
    instance: OmnistrateFleetInstance,
    ssl=False,
):
    """This function should test the ability to read and write while a memory update happens"""
    try:
        db = instance.create_connection(ssl=ssl, force_reconnect=True)

        graph = db.select_graph("test")

        while not thread_signal.is_set():
            # Write some data to the DB
            try:
                graph.query("CREATE (n:Person {name: 'Alice'})")
                graph.ro_query("MATCH (n:Person {name: 'Alice'}) RETURN n")
            except (ConnectionError) as e:
                logging.info("THE CREATE COMMAND FAILED")
                print("THE CREATE COMMAND FAILED")
                db.connection.close()
                db = instance.create_connection(ssl=ssl, force_reconnect=True)
                graph = db.select_graph("test")
                continue

            time.sleep(3)
    except Exception as e:
        logging.exception(e)
        error_signal.set()
        raise e
    

if __name__ == "__main__":
    test_replication()

