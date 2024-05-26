import sys
import requests
import json
import time
from falkordb import FalkorDB
from redis import Sentinel
import base64
import os
from classes.omnistrate_instance import OmnistrateInstance
import random

if len(sys.argv) < 8:
    print(
        "Usage: python test_multi_zone.py <omnistrate_user> <omnistrate_password> <deployment_cloud_provider> <deployment_region> <deployment_instance_type> <deployment_storage_size> <replica_count> <tls=false>"
    )
    sys.exit(1)

OMNISTRATE_USER = sys.argv[1]
OMNISTRATE_PASSWORD = sys.argv[2]
DEPLOYMENT_CLOUD_PROVIDER = sys.argv[3]
DEPLOYMENT_REGION = sys.argv[4]
DEPLOYMENT_INSTANCE_TYPE = sys.argv[5]
DEPLOYMENT_STORAGE_SIZE = sys.argv[6]
DEPLOYMENT_REPLICA_COUNT = sys.argv[7]
DEPLOYMENT_TLS = sys.argv[8] if len(sys.argv) > 8 else "false"

API_VERSION = os.getenv("API_VERSION", "2022-09-01-00")
API_PATH = os.getenv(
    "API_PATH",
    f"{API_VERSION}/resource-instance/sp-JvkxkPhinN/falkordb-internal/v1/dev/falkordb-internal-customer-hosted/falkordb-internal-hosted-tier-falkordb-internal-customer-hosted-model-omnistrate-dedicated-tenancy/multi-Zone",
)
API_FAILOVER_PATH = os.getenv(
    "API_FAILOVER_PATH",
    f"{API_VERSION}/resource-instance/sp-JvkxkPhinN/falkordb-internal/v1/dev/falkordb-internal-customer-hosted/falkordb-internal-hosted-tier-falkordb-internal-customer-hosted-model-omnistrate-dedicated-tenancy",
)
API_SIGN_IN_PATH = os.getenv(
    "API_SIGN_IN_PATH", f"{API_VERSION}/resource-instance/user/signin"
)
SUBSCRIPTION_ID = os.getenv("SUBSCRIPTION_ID", "sub-bHEl5iUoPd")


def test_multi_zone():

    instance = OmnistrateInstance(
        api_path=API_PATH,
        api_failover_path=API_FAILOVER_PATH,
        api_sign_in_path=API_SIGN_IN_PATH,
        subscription_id=SUBSCRIPTION_ID,
        omnistrate_user=OMNISTRATE_USER,
        omnistrate_password=OMNISTRATE_PASSWORD,
    )

    try:
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=DEPLOYMENT_CLOUD_PROVIDER,
            deployment_region=DEPLOYMENT_REGION,
            name="github-pipeline-multi-zone",
            description="multi zone",
            falkordb_user="falkordb",
            falkordb_password="falkordb",
            nodeInstanceType=DEPLOYMENT_INSTANCE_TYPE,
            storageSize=DEPLOYMENT_STORAGE_SIZE,
            enableTLS=True if DEPLOYMENT_TLS == "true" else False,
        )
        # Test failover and data loss
        test_failover(instance)
    except Exception as e:
        instance.delete(True)
        raise e

    # Delete instance
    instance.delete(True)

    print("Test passed")


def test_failover(instance: OmnistrateInstance):
    """
    Multi Zone tests are the following:
    1. Create a multi zone instance
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
        filter(lambda resource: resource["id"].startswith("node-mz"), resources)
    )
    sentinel_resource = next(
        (resource for resource in resources if resource["id"].startswith("sentinel-mz")), None
    )
    db_0 = FalkorDB(
        host=db_resource[0]["endpoint"],
        port=db_resource[0]["ports"][0],
        username="falkordb",
        password="falkordb",
        ssl=True if DEPLOYMENT_TLS == "true" else False,
    )
    db_1 = FalkorDB(
        host=db_resource[1]["endpoint"],
        port=db_resource[1]["ports"][0],
        username="falkordb",
        password="falkordb",
        ssl=True if DEPLOYMENT_TLS == "true" else False,
    )
    sentinels = Sentinel(
        sentinels=[
            (sentinel_resource["endpoint"], sentinel_resource["ports"][0]),
            (db_resource[0]["endpoint"], db_resource[0]["ports"][1]),
            (db_resource[1]["endpoint"], db_resource[1]["ports"][1]),
        ],
        sentinel_kwargs={
            "username": "falkordb",
            "password": "falkordb",
            "ssl": True if DEPLOYMENT_TLS == "true" else False,
        },
        connection_kwargs={
            "username": "falkordb",
            "password": "falkordb",
            "ssl": True if DEPLOYMENT_TLS == "true" else False,
        },
    )

    sentinels_list = random.choice(sentinels.sentinels).execute_command("sentinel sentinels master")

    if len(sentinels_list) != 2:
        raise Exception(f"Sentinel list not correct. Expected 2, got {len(sentinels_list)}")

    graph_0 = db_0.select_graph("test")

    # Write some data to the DB
    graph_0.query("CREATE (n:Person {name: 'Alice'})")

    # Check if data was replicated
    graph_1 = db_1.select_graph("test")

    result = graph_1.ro_query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data was not replicated to the slave")

    print("Triggering failover for node-mz-0")
    # Trigger failover
    instance.trigger_failover(
        replica_id="node-mz-0",
        wait_for_ready=False,
        resource_id="node-mz"
    )

    promotion_completed = False
    while not promotion_completed:
        try:
            graph = db_1.execute_command("info replication")
            if "role:master" in graph:
                promotion_completed = True
            time.sleep(5)
        except Exception as e:
            print("Promotion not completed yet")
            time.sleep(5)

    print("Promotion completed")

    # Check if data is still there
    graph_1 = db_1.select_graph("test")

    result = graph_1.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data lost after first failover")

    print("Data persisted after first failover")

    graph_1.query("CREATE (n:Person {name: 'Bob'})")

    result = graph_1.query("MATCH (n:Person) RETURN n")

    print("result after bob", result.result_set)

    # wait until the node-mz-0 is ready
    instance.wait_for_ready(timeout_seconds=600)
    
    print("Triggering failover for sentinel-mz-0")
    # Trigger sentinel failover
    instance.trigger_failover(
        replica_id="sentinel-mz-0",
        wait_for_ready=False,
        resource_id="sentinel-mz"
    )

    graph_1 = db_1.select_graph("test")

    result = graph_1.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) < 2:
        raise Exception("Data lost after second failover")

    print("Data persisted after second failover")

    # wait until the node-mz-0 is ready
    instance.wait_for_ready(timeout_seconds=600)

    print("Triggering failover for node-mz-1")
    # Trigger failover
    instance.trigger_failover(
        replica_id="node-mz-1",
        wait_for_ready=False,
        resource_id="node-mz"
    )

    promotion_completed = False
    while not promotion_completed:
        try:
            graph = db_0.execute_command("info replication")
            if "role:master" in graph:
                promotion_completed = True
            time.sleep(5)
        except Exception as e:
            print("Promotion not completed yet")
            time.sleep(5)

    print("Promotion completed")

    # Check if data is still there
    graph_0 = db_0.select_graph("test")

    result = graph_0.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) < 2:
        print(result.result_set)
        raise Exception("Data lost after third failover")

    print("Data persisted after third failover")


if __name__ == "__main__":
    test_multi_zone()
