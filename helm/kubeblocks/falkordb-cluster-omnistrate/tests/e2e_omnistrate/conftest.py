"""
Pytest configuration and fixtures for E2E Omnistrate tests.

This conftest provides fixtures for:
- Loading configuration from environment variables
- Creating Omnistrate Fleet API client
- Managing instance lifecycle (create/teardown)
- Service model and tier information
"""

import os
import sys
import time
import socket
import pytest
import logging
import secrets
from pathlib import Path

# Add the tests directory to the path so we can import test utilities
tests_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
if tests_root not in sys.path:
    sys.path.insert(0, tests_root)

# Import refactored Omnistrate client classes
from .omnistrate_client import OmnistrateFleetAPI, OmnistrateFleetInstance, OmnistrateFleetNetwork

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def pytest_addoption(parser):
    """Add command-line options for E2E Omnistrate tests."""
    logging.info("Adding pytest options for E2E Omnistrate tests")
    add = parser.addoption
    
    # Cloud / environment
    add("--cloud-provider", default=os.getenv("CLOUD_PROVIDER", "aws"))
    add("--region", default=os.getenv("CLOUD_REGION", "us-east-1"))
    add("--service-id", default=os.getenv("SERVICE_ID"))
    add("--environment-id", default=os.getenv("ENVIRONMENT_ID"))
    add("--subscription-id", default=os.getenv("SUBSCRIPTION_ID"))

    # Tier / topology
    add("--tier-name", default=os.getenv("TIER_NAME", "Free"))
    add("--resource-key", default=os.getenv("RESOURCE_KEY", "standalone"))
    add("--instance-name", default=os.getenv("INSTANCE_NAME", "e2e-test"))
    add("--instance-type", default=os.getenv("INSTANCE_TYPE", "t3.medium"))
    add("--storage-size", default=os.getenv("STORAGE_SIZE", "30"))
    add(
        "--tls",
        action="store_true",
        default=os.getenv("TLS", "false").lower() in ("1", "true", "yes"),
    )
    add("--rdb-config", default=os.getenv("RDB_CONFIG", "medium"))
    add("--aof-config", default=os.getenv("AOF_CONFIG", "always"))
    add("--maxmemory", default=os.getenv("MAXMEMORY", "1GB"))
    add("--host-count", default=os.getenv("HOST_COUNT", "3"))
    add("--cluster-replicas", default=os.getenv("CLUSTER_REPLICAS", "1"))
    add("--network-type", default=os.getenv("NETWORK_TYPE", "PUBLIC"))
    add("--custom-network", default=os.getenv("CUSTOM_NETWORK"))
    add(
        "--multi-zone",
        action="store_true",
        default=os.getenv("MULTI_ZONE", "false").lower() in ("1", "true", "yes"),
    )

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
    add(
        "--skip-teardown",
        action="store_true",
        default=os.getenv("SKIP_TEARDOWN", "false").lower() in ("1", "true", "yes"),
    )

    # Test selection
    add("--e2e-steps", default=os.getenv("E2E_STEPS", "all"))
    add("--new-instance-type", default=os.getenv("NEW_INSTANCE_TYPE"))


@pytest.fixture(scope="session")
def cfg(pytestconfig):
    """
    Configuration fixture loaded from command-line options and environment variables.
    """
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
        "multi_zone": opt("--multi-zone"),
        # Timeouts
        "create_timeout": opt("--create-timeout"),
        "delete_timeout": opt("--delete-timeout"),
        "failover_timeout": opt("--failover-timeout"),
        # Behavior
        "persist_on_fail": opt("--persist-on-fail"),
        "skip_teardown": opt("--skip-teardown"),
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
    
    # Validate required fields
    required_fields = [
        "omnistrate_user",
        "omnistrate_password",
        "service_id",
        "environment_id",
        "subscription_id",
    ]
    
    missing = [f for f in required_fields if not cfg_dict.get(f)]
    if missing:
        raise RuntimeError(
            f"Missing required configuration: {', '.join(missing)}. "
            f"Please set via environment variables or command-line options."
        )
    
    logging.debug(f"Configuration: {cfg_dict}")
    return cfg_dict


@pytest.fixture(scope="session")
def omnistrate(cfg):
    """
    Omnistrate Fleet API client fixture.
    """
    logging.info("Creating OmnistrateFleetAPI instance")
    return OmnistrateFleetAPI(
        email=cfg["omnistrate_user"], password=cfg["omnistrate_password"]
    )


@pytest.fixture(scope="session")
def service_model_parts(omnistrate: OmnistrateFleetAPI, cfg):
    """
    Fetch service, tier, service model, and optionally custom network.
    """
    logging.info("Fetching service model parts")
    service = omnistrate.get_service(cfg["service_id"])
    tier = omnistrate.get_product_tier(
        service_id=cfg["service_id"],
        environment_id=cfg["environment_id"],
        tier_name=cfg["tier_name"],
    )
    sm = omnistrate.get_service_model(cfg["service_id"], tier.service_model_id)
    network = (
        OmnistrateFleetNetwork(omnistrate, cfg["custom_network"]) 
        if cfg["custom_network"] 
        else None
    )
    logging.debug("Service model parts fetched successfully")
    return service, tier, sm, network


def _resolve_hostname(endpoint: str, timeout=300, interval=1):
    """
    Resolve hostname to ensure DNS is propagated.
    """
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


def _reset_instance_data(inst: OmnistrateFleetInstance) -> None:
    """Best-effort reset of FalkorDB state between tests."""
    ssl = bool(getattr(inst, "_cfg", {}).get("tls", False))
    network_type = getattr(inst, "_cfg", {}).get("network_type", "PUBLIC")

    # Ensure instance is running before we try to connect/flush.
    try:
        inst.wait_for_instance_status(
            timeout_seconds=int(getattr(inst, "_cfg", {}).get("ready_timeout", 600))
        )
    except Exception as e:
        logging.warning(f"Instance not ready during reset: {e}")

    # Clear cached topology/connection in case previous test changed topology.
    inst._network_topology = None
    inst._connection = None

    # 1) Prefer flushing via cluster endpoint.
    try:
        db = inst.create_connection(
            ssl=ssl,
            force_reconnect=True,
            retries=3,
            network_type=network_type,
        )
        db.connection.flushall()
        return
    except Exception as e:
        logging.debug(f"Cluster-endpoint flushall failed; falling back: {e}")

    # 2) Fallback: flush each node endpoint (best-effort, handles cluster mode).
    try:
        endpoints = inst.get_connection_endpoints()
    except Exception as e:
        logging.warning(f"Failed to fetch endpoints for reset: {e}")
        return

    for ep in endpoints:
        ep_id = (ep.get("id") or "").lower()
        if ep_id.startswith("sentinel-") or "sentinel" in ep_id:
            continue

        host = ep.get("endpoint")
        ports = ep.get("ports") or []
        if not host or not ports:
            continue

        flushed = False
        for port in ports:
            try:
                conn = FalkorDB(
                    host=host,
                    port=int(port),
                    username="falkordb",
                    password=inst.falkordb_password,
                    ssl=ssl,
                )
                conn.client.ping()
                conn.client.flushall()
                flushed = True
                break
            except Exception:
                continue

        if not flushed:
            logging.debug(f"Reset: could not flush {ep.get('id')} at {host}:{ports}")


@pytest.fixture(scope="module")
def instance(omnistrate: OmnistrateFleetAPI, service_model_parts, cfg, request):
    """
    Provision a FalkorDB instance via Omnistrate once per test module (file).
    
    The instance is created before the test runs and torn down afterward,
    unless --persist-on-fail is set and the test fails, or --skip-teardown is set.
    """
    logging.info("Creating instance fixture")
    service, tier, sm, network = service_model_parts

    # Prepare instance configuration
    inst_cfg = {
        "service_id": cfg["service_id"],
        "service_provider_id": service.service_provider_id,
        "service_key": service.key,
        "service_environment_id": cfg["environment_id"],
        "service_environment_key": service.get_environment(cfg["environment_id"]).key,
        "service_model_key": sm.key,
        "service_api_version": "v1",
        "product_tier_key": tier.product_tier_key,
        "resource_key": cfg["resource_key"],
        "subscription_id": cfg["subscription_id"],
        "ready_timeout": cfg["create_timeout"],
        "stop_timeout": cfg["delete_timeout"],
        "update_timeout": cfg["failover_timeout"],
        "network_type": cfg["network_type"],
        "tls": cfg["tls"],
        "e2e_steps": cfg["e2e_steps"],
        "cloud_provider": cfg["cloud_provider"],
        "region": cfg["region"],
        "instance_type": cfg["instance_type"],
        "orig_instance_type": cfg["instance_type"],
        "new_instance_type": cfg.get("new_instance_type"),
        "storage_size": cfg["storage_size"],
    }
    
    inst = OmnistrateFleetInstance(omnistrate, inst_cfg)

    password = secrets.token_hex(16)
    
    # Generate unique instance name for this test module
    module_name = Path(str(request.fspath)).stem
    instance_name = f"{cfg['instance_name']}-{module_name}"[:50]  # Limit length
    
    logging.info(f"Creating instance: {instance_name}")
    
    inst.create(
        wait_for_ready=True,
        deployment_cloud_provider=cfg["cloud_provider"],
        network_type=cfg["network_type"],
        deployment_region=cfg["region"],
        name=instance_name,
        description=f"E2E test module: {module_name}",
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
        multiZoneEnabled=cfg["multi_zone"],
        custom_network_id=network.network_id if network else None,
    )

    # Wait for DNS propagation on main endpoint
    ep = inst.get_cluster_endpoint(network_type=cfg["network_type"])
    if ep:
        _resolve_hostname(ep["endpoint"])

    # Attach configuration and credentials for test access
    # Keep Omnistrate API config keys while also exposing test config keys.
    inst._cfg = {**inst_cfg, **dict(cfg)}
    inst.falkordb_password = password
    inst._product_tier_id = tier.product_tier_id

    logging.info(f"Instance {instance_name} ready for testing")
    
    try:
        yield inst
    finally:
        # Determine if we should skip teardown
        failed = bool(getattr(request.module, "_had_failure", False))
        skip_teardown = cfg["skip_teardown"] or (failed and cfg["persist_on_fail"])
        
        if not skip_teardown:
            logging.info(f"Deleting instance {instance_name}")
            try:
                inst.delete(network is not None)
            except Exception as e:
                logging.error(f"Failed to delete instance: {e}")
        else:
            logging.warning(
                f"Instance {instance_name} retained "
                f"(failed={failed}, persist_on_fail={cfg['persist_on_fail']}, "
                f"skip_teardown={cfg['skip_teardown']})"
            )


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """
    Hook to capture test outcome and make it available to fixtures.
    """
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)

    if rep.when == "call" and rep.failed:
        try:
            setattr(item.module, "_had_failure", True)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def reset_instance_between_tests(instance, request):
    """Reset FalkorDB data between test functions while reusing the same instance."""
    # Only reset for actual test calls (not collection) and only for this E2E suite.
    _reset_instance_data(instance)
    yield
