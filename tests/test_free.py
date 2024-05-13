import sys
from falkordb import FalkorDB
import os
from classes.omnistrate_instance import OmnistrateInstance

if len(sys.argv) < 5:
    print(
        "Usage: python create_free.py <omnistrate_user> <omnistrate_password> <deployment_cloud_provider> <deployment_region>"
    )
    sys.exit(1)

OMNISTRATE_USER = sys.argv[1]
OMNISTRATE_PASSWORD = sys.argv[2]
DEPLOYMENT_CLOUD_PROVIDER = sys.argv[3]
DEPLOYMENT_REGION = sys.argv[4]


API_VERSION = os.getenv("API_VERSION", "2022-09-01-00")
API_PATH = os.getenv(
    "API_PATH",
    f"{API_VERSION}/resource-instance/sp-JvkxkPhinN/falkordb-internal/v1/dev/falkordb-internal-customer-hosted/falkordb-internal-hosted-tier-falkordb-internal-customer-hosted-model-omnistrate-dedicated-tenancy/free",
)
API_FAILOVER_PATH = os.getenv(
    "API_FAILOVER_PATH",
    f"{API_VERSION}/resource-instance/sp-JvkxkPhinN/falkordb-internal/v1/dev/falkordb-internal-customer-hosted/falkordb-internal-hosted-tier-falkordb-internal-customer-hosted-model-omnistrate-dedicated-tenancy/node-f",
)
API_SIGN_IN_PATH = os.getenv(
    "API_SIGN_IN_PATH", f"{API_VERSION}/resource-instance/user/signin"
)
SUBSCRIPTION_ID = os.getenv("SUBSCRIPTION_ID", "sub-bHEl5iUoPd")


def test_free():

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
            name="github-pipeline-free",
            description="free",
            falkordb_user="falkordb",
            falkordb_password="falkordb",
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
    """This function should retrieve the instance host and port for connection, write some data to the DB, then trigger a failover. After X seconds, the instance should be back online and data should have persisted"""

    # Get instance host and port
    endpoints = instance.get_connection_endpoints()

    nodeId = endpoints[0]["id"]
    host = endpoints[0]["endpoint"]
    port = endpoints[0]["ports"][0]

    print("Connection data: {}:{}".format(host, port))
    db = FalkorDB(host=host, port=port, username="falkordb", password="falkordb")

    graph = db.select_graph("test")

    # Write some data to the DB
    graph.query("CREATE (n:Person {name: 'Alice'})")

    # Trigger failover
    instance.trigger_failover(
        replica_id=nodeId,
        wait_for_ready=True,
    )

    # Check if data is still there

    graph = db.select_graph("test")

    result = graph.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data lost after failover")

    print("Data persisted after failover")


if __name__ == "__main__":
    test_free()
