import os
import time
import socket
import pytest
import logging
import secrets

from tests.classes.omnistrate_fleet_api import OmnistrateFleetAPI

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def pytest_addoption(parser):
    logging.info("Adding pytest options")
    add = parser.addoption
    # Cloud / env
    add("--cloud-provider", default=os.getenv("CLOUD_PROVIDER"))
    add("--region", default=os.getenv("CLOUD_REGION"))
    add("--service-id", default=os.getenv("SERVICE_ID"))
    add("--environment-id", default=os.getenv("ENVIRONMENT_ID"))
    add("--subscription-id", default=os.getenv("SUBSCRIPTION_ID"))

    # Tier / topology
    add("--tier-name", default=os.getenv("TIER_NAME"))
    add(
        "--resource-key", default=os.getenv("RESOURCE_KEY")
    )  # standalone | single-Zone | multi-Zone | cluster-Single-Zone | cluster-Multi-Zone
    add("--instance-name", default=os.getenv("INSTANCE_NAME", "e2e-grouped"))
    add("--instance-type", default=os.getenv("INSTANCE_TYPE"))
    add("--storage-size", default=os.getenv("STORAGE_SIZE", "30"))
    add(
        "--tls",
        action="store_true",
        default=os.getenv("TLS", "false").lower() in ("1", "true", "yes"),
    )
    add("--rdb-config", default=os.getenv("RDB_CONFIG", "medium"))
    add("--aof-config", default=os.getenv("AOF_CONFIG", "always"))
    add("--maxmemory", default=os.getenv("MAXMEMORY", "2GB"))
    add("--host-count", default=os.getenv("HOST_COUNT", "6"))
    add("--cluster-replicas", default=os.getenv("CLUSTER_REPLICAS", "1"))
    add(
        "--network-type", default=os.getenv("NETWORK_TYPE", "PUBLIC")
    )  # PUBLIC | INTERNAL
    add("--custom-network", default=os.getenv("CUSTOM_NETWORK"))

    # Timeouts
    add("--create-timeout", type=int, default=int(os.getenv("CREATE_TIMEOUT", "2600")))
    add("--delete-timeout", type=int, default=int(os.getenv("DELETE_TIMEOUT", "2600")))
    add(
        "--failover-timeout",
        type=int,
        default=int(os.getenv("FAILOVER_TIMEOUT", "2600")),
    )

    # Behavior
    add(
        "--persist-on-fail",
        action="store_true",
        default=os.getenv("PERSIST_ON_FAIL", "false").lower() in ("1", "true", "yes"),
    )

    # Which steps to run (comma-separated list). Default: all
    # Supported steps (packs use relevant subset):
    # failover, stopstart, sentinel-failover, second-failover, scale-replicas, scale-shards, resize, oom, upgrade
    add("--e2e-steps", default=os.getenv("E2E_STEPS", "all"))

    # Optional new instance type for resize step
    add("--new-instance-type", default=os.getenv("NEW_INSTANCE_TYPE"))


@pytest.fixture(scope="session")
def cfg(pytestconfig):
    logging.info("Creating configuration fixture")
    opt = pytestconfig.getoption
    steps_raw = opt("--e2e-steps") or "all"
    e2e_steps = set(s.strip().lower() for s in steps_raw.split(",") if s.strip()) or {
        "all"
    }

    cfg_dict = {
        # Cloud / env
        "cloud_provider": opt("--cloud-provider"),
        "region": opt("--region"),
        "service_id": opt("--service-id"),
        "environment_id": opt("--environment-id"),
        "subscription_id": opt("--subscription-id"),
        # Tier / topology
        "tier_name": opt("--tier-name"),
        "resource_key": opt("--resource-key"),
        "instance_name": opt("--instance-name"),
        "instance_type": opt("--instance-type"),
        "storage_size": opt("--storage-size"),
        "tls": opt("--tls"),
        "rdb_config": opt("--rdb-config"),
        "aof_config": opt("--aof-config"),
        "maxmemory": opt("--maxmemory"),
        "host_count": opt("--host-count"),
        "cluster_replicas": opt("--cluster-replicas"),
        "network_type": opt("--network-type"),
        "custom_network": opt("--custom-network"),
        # Timeouts
        "create_timeout": opt("--create-timeout"),
        "delete_timeout": opt("--delete-timeout"),
        "failover_timeout": opt("--failover-timeout"),
        # Behavior
        "persist_on_fail": opt("--persist-on-fail"),
        "e2e_steps": e2e_steps,
        "new_instance_type": opt("--new-instance-type"),
        # Originals for reverts
        "orig_host_count": int(opt("--host-count")),
        "orig_cluster_replicas": int(opt("--cluster-replicas")),
        "orig_instance_type": opt("--instance-type"),
        # Auth (env)
        "omnistrate_user": os.getenv("OMNISTRATE_USERNAME"),
        "omnistrate_password": os.getenv("OMNISTRATE_PASSWORD"),
    }
    logging.debug(f"Configuration: {cfg_dict}")
    return cfg_dict


@pytest.fixture(scope="session")
def omnistrate(cfg):
    logging.info("Creating OmnistrateFleetAPI instance")
    if not cfg["omnistrate_user"] or not cfg["omnistrate_password"]:
        logging.error(
            "Missing OMNISTRATE_USERNAME / OMNISTRATE_PASSWORD in environment"
        )
        raise RuntimeError(
            "Missing OMNISTRATE_USERNAME / OMNISTRATE_PASSWORD in environment."
        )
    return OmnistrateFleetAPI(
        email=cfg["omnistrate_user"], password=cfg["omnistrate_password"]
    )


@pytest.fixture(scope="session")
def service_model_parts(omnistrate: OmnistrateFleetAPI, cfg):
    logging.info("Fetching service model parts")
    service = omnistrate.get_service(cfg["service_id"])
    tier = omnistrate.get_product_tier(
        service_id=cfg["service_id"],
        environment_id=cfg["environment_id"],
        tier_name=cfg["tier_name"],
    )
    sm = omnistrate.get_service_model(cfg["service_id"], tier.service_model_id)
    network = (
        omnistrate.network(cfg["custom_network"]) if cfg["custom_network"] else None
    )
    logging.debug("Service model parts fetched successfully")
    return service, tier, sm, network


def _resolve_hostname(endpoint: str, timeout=300, interval=1):
    logging.info(f"Resolving hostname for endpoint: {endpoint}")
    start = time.time()
    while time.time() - start < timeout:
        try:
            resolved_ip = socket.gethostbyname(endpoint)
            logging.debug(f"Resolved IP: {resolved_ip}")
            return resolved_ip
        except Exception as e:
            logging.debug(f"Retrying DNS resolution for {endpoint}: {e}")
            time.sleep(interval)
    logging.error(f"DNS not resolved for {endpoint} within {timeout}s")
    raise TimeoutError(f"DNS not resolved for {endpoint} within {timeout}s")


@pytest.fixture(scope="session")
def instance(omnistrate: OmnistrateFleetAPI, service_model_parts, cfg, request):
    """
    Provision once and yield a ready instance.
    Teardown at the end unless --persist-on-fail is set and tests failed.
    Ensure deletion even if the pipeline is canceled.
    """
    logging.info("Creating instance fixture")
    service, tier, sm, network = service_model_parts

    inst = omnistrate.instance(
        service_id=cfg["service_id"],
        service_provider_id=service.service_provider_id,
        service_key=service.key,
        service_environment_id=cfg["environment_id"],
        service_environment_key=service.get_environment(cfg["environment_id"]).key,
        service_model_key=sm.key,
        service_api_version="v1",
        product_tier_key=tier.product_tier_key,
        resource_key=cfg["resource_key"],
        subscription_id=cfg["subscription_id"],
        deployment_create_timeout_seconds=cfg["create_timeout"],
        deployment_delete_timeout_seconds=cfg["delete_timeout"],
        deployment_failover_timeout_seconds=cfg["failover_timeout"],
    )

    password = secrets.token_hex(16)
    inst.create(
        wait_for_ready=True,
        deployment_cloud_provider=cfg["cloud_provider"],
        network_type=cfg["network_type"],
        deployment_region=cfg["region"],
        name=cfg["instance_name"],
        description=f"grouped-{cfg['resource_key']}",
        falkordb_user="falkordb",
        falkordb_password=password,
        nodeInstanceType=cfg["instance_type"],
        storageSize=cfg["storage_size"],
        enableTLS=cfg["tls"],
        RDBPersistenceConfig=cfg["rdb_config"],
        AOFPersistenceConfig=cfg["aof_config"],
        maxMemory=cfg["maxmemory"],
        hostCount=cfg["host_count"],
        clusterReplicas=cfg["cluster_replicas"],
        custom_network_id=network.network_id if network else None,
    )

    # Wait for DNS on main endpoint
    ep = (
        inst.get_cluster_endpoint(network_type=cfg["network_type"])
        if hasattr(inst, "get_cluster_endpoint")
        else inst.get_cluster_endpoint()
    )
    _resolve_hostname(ep["endpoint"])

    # attach handy stuff for tests
    inst._cfg = dict(cfg)  # shallow copy ok
    inst.falkordb_password = password
    inst._product_tier_id = tier.product_tier_id  # for upgrade calls

    try:
        yield inst
    finally:
        failed = request.session.testsfailed > 0
        if not (failed and cfg["persist_on_fail"]):
            logging.info("Deleting instance")
            inst.delete(network is not None)
        else:
            logging.warning(
                "Instance retained due to test failures and persist-on-fail flag"
            )
