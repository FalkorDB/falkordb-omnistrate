import sys
import time
import os
from classes.omnistrate_instance import OmnistrateInstance
from classes.omnistrate_api import OmnistrateApi, TierVersionStatus
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("omnistrate_user")
parser.add_argument("omnistrate_password")
parser.add_argument("cloud_provider")
parser.add_argument("region")

parser.add_argument(
    "--api-version", required=False, default=os.getenv("API_VERSION", "2022-09-01-00")
)
parser.add_argument(
    "--api-path",
    required=False,
    default=os.getenv(
        "API_PATH",
        "2022-09-01-00/resource-instance/sp-JvkxkPhinN/falkordb-internal/v1/dev/falkordb-internal-customer-hosted/falkordb-internal-hosted-tier-falkordb-internal-customer-hosted-model-omnistrate-dedicated-tenancy/standalone",
    ),
)
parser.add_argument(
    "--api-sign-in-path",
    required=False,
    default=os.getenv(
        "API_SIGN_IN_PATH", "2022-09-01-00/resource-instance/user/signin"
    ),
)
parser.add_argument(
    "--subscription-id", required=False, default=os.getenv("SUBSCRIPTION_ID")
)
parser.add_argument("--ref-name", required=False, default=os.getenv("REF_NAME"))
parser.add_argument("--service-id", required=True)
parser.add_argument("--environment-id", required=True)
parser.add_argument("--product-tier-id", required=False)
parser.add_argument("--instance-name", required=True)
parser.add_argument(
    "--instance-description", required=False, default="test-upgrade-version"
)
parser.add_argument("--check-failover", required=False, default=False, type=bool)

parser.add_argument("--instance-type", required=True)
parser.add_argument("--storage-size", required=False, default="30")
parser.add_argument("--tls", action="store_true")
parser.add_argument("--rdb-config", required=False, default="medium")
parser.add_argument("--aof-config", required=False, default="always")

parser.set_defaults(tls=False)
args = parser.parse_args()

API_VERSION = args.api_version
API_PATH = args.api_path
API_SIGN_IN_PATH = args.api_sign_in_path
SUBSCRIPTION_ID = args.subscription_id

REF_NAME = args.ref_name
if REF_NAME is not None:
    if len(REF_NAME) > 50:
        # Replace the second occurrence of REF_NAME with the first 50 characters of REF_NAME
        API_PATH = f"customer-hosted/{REF_NAME[:50]}".join(
            API_PATH.split(f"customer-hosted/{REF_NAME}")
        )


def test_upgrade_version():

    omnistrate = OmnistrateApi(
        api_sign_in_path=API_SIGN_IN_PATH,
        omnistrate_user=args.omnistrate_user,
        omnistrate_password=args.omnistrate_password,
    )

    product_tier_id = args.product_tier_id or omnistrate.get_product_tier_id(
        service_id=args.service_id,
        environment_id=args.environment_id,
        tier_name=REF_NAME,
    )

    print(f"Product tier id: {product_tier_id} for {REF_NAME}")

    # 1. List product tier versions
    tiers = omnistrate.list_tier_versions(
        service_id=args.service_id, tier_id=product_tier_id
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

    print(f"Preferred tier: {preferred_tier.version}")
    print(f"Last tier: {last_tier.version}")

    # 2. Create omnistrate instance with previous version
    instance = OmnistrateInstance(
        api_path=API_PATH,
        api_sign_in_path=API_SIGN_IN_PATH,
        subscription_id=SUBSCRIPTION_ID,
        omnistrate_user=args.omnistrate_user,
        omnistrate_password=args.omnistrate_password,
    )
    try:
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=args.cloud_provider,
            deployment_region=args.region,
            name=args.instance_name,
            description=args.instance_description,
            falkordb_user="falkordb",
            falkordb_password="falkordb",
            nodeInstanceType=args.instance_type,
            storageSize=args.storage_size,
            enableTLS=args.tls,
            RDBPersistenceConfig=args.rdb_config,
            AOFPersistenceConfig=args.aof_config,
            product_tier_version=last_tier.version,
        )

        # 3. Add data to the instance
        add_data(instance)

        # 4. Upgrade version for the omnistrate instance
        upgrade_timer = time.time()
        omnistrate.upgrade_instance(
            service_id=args.service_id,
            product_tier_id=product_tier_id,
            instance_id=instance.instance_id,
            source_version=last_tier.version,
            target_version=preferred_tier.version,
            wait_until_ready=True,
            check_failover=args.check_failover,
        )

        print(f"Upgrade time: {(time.time() - upgrade_timer):.2f}s")

        # 6. Verify the upgrade was successful
        query_data(instance)
    except Exception as e:
        print("Error")
        instance.delete(True)
        raise e

    # 7. Delete the instance
    instance.delete(True)

    print("Upgrade version test passed")


def add_data(instance: OmnistrateInstance):

    # Get instance host and port
    db = instance.create_connection(ssl=args.tls)

    graph = db.select_graph("test")

    # Write some data to the DB
    graph.query("CREATE (n:Person {name: 'Alice'})")


def query_data(instance: OmnistrateInstance):

    # Get instance host and port
    db = instance.create_connection(ssl=args.tls)

    graph = db.select_graph("test")

    # Get info
    result = graph.query("MATCH (n:Person) RETURN n.name")

    if len(result.result_set) == 0:
        raise ValueError("No data found in the graph after upgrade")


if __name__ == "__main__":
    test_upgrade_version()
