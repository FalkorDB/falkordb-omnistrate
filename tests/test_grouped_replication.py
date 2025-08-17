import time
import pytest
import logging
from .suite_utils import (
    add_data,
    assert_data,
    stress_oom,
    assert_multi_zone,
)
from redis import Sentinel
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import TimeoutError, ConnectionError, ReadOnlyError, ResponseError

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def _run_step(cfg, name):
    steps = cfg["e2e_steps"]
    return "all" in steps or name in steps


def _sentinel_client(instance, ssl):
    eps = instance.get_connection_endpoints()
    nodes = sorted(
        [e for e in eps if e["id"].startswith("node-")], key=lambda x: x["id"]
    )
    sent = next(e for e in eps if e["id"].startswith("sentinel-"))
    retry = Retry(
        ExponentialBackoff(base=1, cap=10),
        retries=40,
        supported_errors=(TimeoutError, ConnectionError, ReadOnlyError, ResponseError),
    )
    return Sentinel(
        sentinels=[
            (sent["endpoint"], sent["ports"][0]),
            (nodes[0]["endpoint"], nodes[0]["ports"][1]),
            (nodes[1]["endpoint"], nodes[1]["ports"][1]),
        ],
        sentinel_kwargs={
            "username": "falkordb",
            "password": instance.falkordb_password,
            "ssl": ssl,
        },
        connection_kwargs={
            "username": "falkordb",
            "password": instance.falkordb_password,
            "ssl": ssl,
            "retry": retry,
            "retry_on_error": [
                TimeoutError,
                ConnectionError,
                ReadOnlyError,
                ResponseError,
            ],
        },
    )


def test_replication_pack(instance):
    """
    Replication pack (single-Zone / multi-Zone).
    Steps: failover, stopstart, sentinel-failover, second-failover, scale-replicas, resize, oom, upgrade
    """
    logging.info("Starting test_replication_pack")
    ssl = instance._cfg["tls"]
    cfg = instance._cfg
    rk = cfg["resource_key"]
    valid = {
        "failover",
        "stopstart",
        "sentinel-failover",
        "second-failover",
        "scale-replicas",
        "resize",
        "oom",
        "upgrade",
    }
    if cfg["e2e_steps"] != {"all"} and not (cfg["e2e_steps"] & valid):
        logging.warning("No selected steps for replication pack. Skipping test.")
        pytest.skip("No selected steps for replication pack")

    id_key = "sz" if rk == "single-Zone" else "mz"

    # 0) Ensure MZ distribution
    if "multi-zone" in cfg["resource_key"].lower():
        logging.info("Ensuring multi-zone distribution")
        assert_multi_zone(instance, host_count=cfg["orig_cluster_replicas"] + 1)

    # Seed data if any step runs
    logging.debug("Adding initial data to the instance")
    add_data(instance, ssl, network_type=cfg["network_type"])

    # 1) Failover & persistence
    if _run_step(cfg, "failover"):
        logging.info("Triggering failover")
        instance.trigger_failover(
            replica_id=f"node-{id_key}-0",
            wait_for_ready=False,
            resource_id=instance.get_resource_id(f"node-{id_key}"),
        )
        tout = time.time() + 600
        while time.time() < tout:
            try:
                info = instance.create_connection(
                    ssl=ssl, force_reconnect=True
                ).execute_command("info replication")
                if "role:master" in info:
                    break
            except Exception:
                logging.debug("Retrying connection during failover")
                time.sleep(5)
        logging.debug("Validating data after failover")
        assert_data(
            instance,
            ssl,
            msg="Data lost after first failover",
            network_type=cfg["network_type"],
        )

    # 2) Stop/Start immediately after failover
    if _run_step(cfg, "stopstart"):
        logging.info("Stopping and starting the instance")
        instance.stop(wait_for_ready=True)
        instance.start(wait_for_ready=True)
        logging.debug("Validating data after stop/start")
        assert_data(
            instance,
            ssl,
            msg="Data missing after stop/start",
            network_type=cfg["network_type"],
        )

    # 3) Sentinel failover
    if _run_step(cfg, "sentinel-failover"):
        logging.info("Triggering sentinel failover")
        instance.wait_for_instance_status(timeout_seconds=600)
        instance.trigger_failover(
            replica_id=f"sentinel-{id_key}-0",
            wait_for_ready=False,
            resource_id=instance.get_resource_id(f"sentinel-{id_key}"),
        )
        logging.debug("Validating data after sentinel failover")
        assert_data(
            instance,
            ssl,
            msg="Data lost after sentinel failover",
            network_type=cfg["network_type"],
        )

    # 4) Second master failover
    if _run_step(cfg, "second-failover"):
        logging.info("Triggering second master failover")
        instance.wait_for_instance_status(timeout_seconds=600)
        instance.trigger_failover(
            replica_id=f"node-{id_key}-1",
            wait_for_ready=False,
            resource_id=instance.get_resource_id(f"node-{id_key}"),
        )
        logging.debug("Validating data after second failover")
        assert_data(
            instance,
            ssl,
            msg="Data lost after second failover",
            network_type=cfg["network_type"],
        )

    # 5) Add/remove replica — MUST revert
    if _run_step(cfg, "scale-replicas"):
        logging.info("Scaling replicas")
        orig_replicas = int(cfg["orig_cluster_replicas"] or "1")

        def do_scale():
            logging.debug("Increasing number of replicas")
            instance.update_params(numReplicas=orig_replicas + 1, wait_for_ready=True)

        def revert_scale():
            logging.debug("Reverting number of replicas")
            instance.update_params(numReplicas=orig_replicas, wait_for_ready=True)

        from .suite_utils import change_then_revert

        change_then_revert(instance, ssl, do_scale, revert_scale)
        logging.debug("Validating data after replica scaling")
        assert_data(
            instance,
            ssl,
            msg="Data missing after replica scaling",
            network_type=cfg["network_type"],
        )

    # 6) Update memory (resize) — no revert required
    if _run_step(cfg, "resize"):
        logging.info("Resizing instance memory")
        new_type = cfg["new_instance_type"] or cfg["orig_instance_type"]
        instance.update_instance_type(new_type, wait_until_ready=True)
        logging.debug("Validating data after resize")
        assert_data(
            instance,
            ssl,
            msg="Data missing after resize",
            network_type=cfg["network_type"],
        )

    # 7) OOM
    if _run_step(cfg, "oom"):
        logging.info("Simulating OOM")
        stress_oom(instance, ssl=ssl, network_type=cfg["network_type"], query_size="big")
        logging.debug("Passed OOM stress test")

    logging.info("Completed test_replication_pack")
