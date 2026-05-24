import sys
import signal
import secrets
import socket
import logging
import time
import os
import argparse

# Add the parent directory to sys.path to fix import errors
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

#pylint: disable=import-error
from classes.omnistrate_fleet_instance import OmnistrateFleetInstance
from classes.omnistrate_fleet_api import OmnistrateFleetAPI

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(message)s")

parser = argparse.ArgumentParser()
parser.add_argument("omnistrate_user")
parser.add_argument("omnistrate_password")
parser.add_argument("cloud_provider", choices=["aws", "gcp", "azure"])
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
    "--instance-description", required=False, default="test-deploy-instance"
)
parser.add_argument("--instance-type", required=True)
parser.add_argument("--storage-size", required=False, default="30")
parser.add_argument("--tls", action="store_true", default=False)
parser.add_argument("--rdb-config", required=False, default="medium")
parser.add_argument("--aof-config", required=False, default="always")
parser.add_argument("--host-count", required=False, default="6")
parser.add_argument("--cluster-replicas", required=False, default="1")
parser.add_argument("--persist-instance-on-fail", action="store_true")
parser.add_argument("--network-type", required=False, default="PUBLIC")
parser.add_argument("--custom-network", required=False)

parser.add_argument(
    "--deployment-create-timeout-seconds", required=False, default=2600, type=int
)
parser.add_argument(
    "--deployment-delete-timeout-seconds", required=False, default=2600, type=int
)
parser.add_argument(
    "--deployment-failover-timeout-seconds", required=False, default=2600, type=int
)

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


def test_deploy_instance():
    global instance

    omnistrate = OmnistrateFleetAPI(
        email=args.omnistrate_user,
        password=args.omnistrate_password,
    )

    if not args.service_id:
        raise ValueError("Missing service ID")
    if not args.environment_id:
        raise ValueError("Missing environment ID")
    if not args.ref_name:
        raise ValueError("Missing ref name")

    service = omnistrate.get_service(args.service_id)

    product_tier = omnistrate.get_product_tier(
        service_id=args.service_id,
        environment_id=args.environment_id,
        tier_name=args.ref_name,
    )
    if not product_tier:
        raise ValueError(f"Missing product tier: {args.ref_name}")

    service_model = omnistrate.get_service_model(
        args.service_id, product_tier.service_model_id
    )

    logging.info(f"Product tier id: {product_tier.product_tier_id} for {args.ref_name}")

    # Resolve latest version
    latest_version = product_tier.latest_major_version
    logging.info(f"Latest version: {latest_version}")

    # Create omnistrate instance
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
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=args.cloud_provider,
            network_type=args.network_type,
            deployment_region=args.region,
            name=args.instance_name,
            description=args.instance_description,
            falkordb_user="falkordb",
            falkordb_password=secrets.token_hex(16),
            product_tier_version=latest_version,
            custom_tags=[{"key": "falkordb-internal", "value": "testing-pipeline"}],
            nodeInstanceType=args.instance_type,
            storageSize=args.storage_size,
            enableTLS=args.tls,
            RDBPersistenceConfig=args.rdb_config,
            AOFPersistenceConfig=args.aof_config,
            hostCount=args.host_count,
            clusterReplicas=args.cluster_replicas,
            custom_network_id=network.network_id if network else None,
        )

        try:
            ip = resolve_hostname(instance=instance, network_type=args.network_type)
            logging.info(
                f"Instance endpoint {instance.get_cluster_endpoint(network_type=args.network_type)['endpoint']} resolved to {ip}"
            )
        except TimeoutError as e:
            logging.error(f"DNS resolution failed: {e}")
            raise Exception("Instance endpoint not ready: DNS resolution failed") from e

        # Write data to verify the instance is functional
        add_data(instance)

        # Read data back to verify
        query_data(instance)

        logging.info("Test passed — instance created and verified successfully")
    except Exception as e:
        logging.exception(e)
        if not args.persist_instance_on_fail:
            instance.delete(False)
        raise

    # Delete the instance
    instance.delete(False)


def add_data(instance: OmnistrateFleetInstance):
    db = instance.create_connection(
        ssl=args.tls,
        force_reconnect=True,
        network_type=args.network_type,
    )
    graph = db.select_graph("test")
    graph.query("CREATE (n:Person {name: 'Alice'})")


def query_data(instance: OmnistrateFleetInstance):
    db = instance.create_connection(
        ssl=args.tls,
        force_reconnect=True,
        network_type=args.network_type,
    )
    graph = db.select_graph("test")
    result = graph.ro_query("MATCH (n:Person) RETURN n.name")

    if len(result.result_set) == 0:
        raise ValueError("No data found in the graph after creation")


def resolve_hostname(instance: OmnistrateFleetInstance, timeout=300, interval=1, network_type="PUBLIC"):
    if interval <= 0 or timeout <= 0:
        raise ValueError("Interval and timeout must be positive")

    cluster_endpoint = instance.get_cluster_endpoint(network_type=network_type)

    if not cluster_endpoint or "endpoint" not in cluster_endpoint:
        raise KeyError("Missing endpoint information in cluster configuration")

    hostname = cluster_endpoint["endpoint"]
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            ip = socket.gethostbyname(hostname)
            return ip
        except (socket.gaierror, socket.error) as e:
            logging.debug(f"DNS resolution attempt failed: {e}")
            time.sleep(interval)

    raise TimeoutError(
        f"Unable to resolve hostname '{hostname}' within {timeout} seconds."
    )


if __name__ == "__main__":
    test_deploy_instance()
