import sys
import time
import os
from classes.omnistrate_instance import OmnistrateInstance

if len(sys.argv) < 8:
    print(
        "Usage: python test_update_memory.py <omnistrate_user> <omnistrate_password> <deployment_cloud_provider> <deployment_region> <deployment_instance_type> <deployment_storage_size> <instance_type_new> <tls=false> <rdb_config=medium> <aof_config=always>"
    )
    sys.exit(1)

OMNISTRATE_USER = sys.argv[1]
OMNISTRATE_PASSWORD = sys.argv[2]
DEPLOYMENT_CLOUD_PROVIDER = sys.argv[3]
DEPLOYMENT_REGION = sys.argv[4]
DEPLOYMENT_INSTANCE_TYPE = sys.argv[5]
DEPLOYMENT_STORAGE_SIZE = sys.argv[6]
DEPLOYMENT_INSTANCE_TYPE_NEW = sys.argv[7]
DEPLOYMENT_TLS = sys.argv[8] if len(sys.argv) > 8 else "false"
DEPLOYMENT_RDB_CONFIG = sys.argv[9] if len(sys.argv) > 9 else "medium"
DEPLOYMENT_AOF_CONFIG = sys.argv[10] if len(sys.argv) > 10 else "always"

API_VERSION = os.getenv("API_VERSION", "2022-09-01-00")
API_PATH = os.getenv(
    "API_PATH",
    f"{API_VERSION}/resource-instance/sp-JvkxkPhinN/falkordb-internal/v1/dev/falkordb-internal-customer-hosted/falkordb-internal-hosted-tier-falkordb-internal-customer-hosted-model-omnistrate-dedicated-tenancy/single-Zone",
)
API_FAILOVER_PATH = os.getenv(
    "API_FAILOVER_PATH",
    f"{API_VERSION}/resource-instance/sp-JvkxkPhinN/falkordb-internal/v1/dev/falkordb-internal-customer-hosted/falkordb-internal-hosted-tier-falkordb-internal-customer-hosted-model-omnistrate-dedicated-tenancy",
)
API_SIGN_IN_PATH = os.getenv(
    "API_SIGN_IN_PATH", f"{API_VERSION}/resource-instance/user/signin"
)
SUBSCRIPTION_ID = os.getenv("SUBSCRIPTION_ID", "sub-bHEl5iUoPd")


def test_update_memory():

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
            name="github-pipeline-test-update-memory",
            description="test-update-memory",
            falkordb_user="falkordb",
            falkordb_password="falkordb",
            nodeInstanceType=DEPLOYMENT_INSTANCE_TYPE,
            storageSize=DEPLOYMENT_STORAGE_SIZE,
            enableTLS=True if DEPLOYMENT_TLS == "true" else False,
            RDBPersistenceConfig=DEPLOYMENT_RDB_CONFIG,
            AOFPersistenceConfig=DEPLOYMENT_AOF_CONFIG,
        )

        instance.generate_data(graph_count=1000)

        # Update memory
        instance.update_instance_type(DEPLOYMENT_INSTANCE_TYPE_NEW, wait_until_ready=True)

        check_data_loss(instance, keys=4000)

    except Exception as e:
        instance.delete(True)
        raise e

    # Delete instance
    instance.delete(True)

    print("Test passed")


def check_data_loss(instance: OmnistrateInstance, keys: int):

    connection = instance.get_connection()

    # Get info
    info = connection.execute_command("INFO")

    print(info)

    # Check the number of keys
    assert int(info["db0"]["keys"]) == keys
