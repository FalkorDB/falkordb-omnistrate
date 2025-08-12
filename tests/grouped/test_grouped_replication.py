import os
import time
import pytest
from suite_utils import (
    add_data,
    assert_data,
    change_then_revert,
    stress_oom,
    assert_multi_zone,
)
from redis import Sentinel
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import TimeoutError, ConnectionError, ReadOnlyError, ResponseError


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
        pytest.skip("No selected steps for replication pack")

    id_key = "sz" if rk == "single-Zone" else "mz"

    # 0) Ensure MZ distribution
    if "multi-zone" in cfg["resource_key"].lower():
        assert_multi_zone(instance, host_count=cfg["orig_cluster_replicas"])

    # Seed data if any step runs
    if True:
        add_data(instance, ssl)

    # 1) Failover & persistence
    if _run_step(cfg, "failover"):
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
                time.sleep(5)
        assert_data(instance, ssl, msg="Data lost after first failover")

    # 2) Stop/Start immediately after failover
    if _run_step(cfg, "stopstart"):
        instance.stop(wait_for_ready=True)
        instance.start(wait_for_ready=True)
        assert_data(instance, ssl, msg="Data missing after stop/start")

    # 3) Sentinel failover
    if _run_step(cfg, "sentinel-failover"):
        instance.wait_for_instance_status(timeout_seconds=600)
        instance.trigger_failover(
            replica_id=f"sentinel-{id_key}-0",
            wait_for_ready=False,
            resource_id=instance.get_resource_id(f"sentinel-{id_key}"),
        )
        assert_data(instance, ssl, msg="Data lost after sentinel failover")

    # 4) Second master failover
    if _run_step(cfg, "second-failover"):
        instance.wait_for_instance_status(timeout_seconds=600)
        instance.trigger_failover(
            replica_id=f"node-{id_key}-1",
            wait_for_ready=False,
            resource_id=instance.get_resource_id(f"node-{id_key}"),
        )
        assert_data(instance, ssl, msg="Data lost after second failover")

    # 5) Add/remove replica — MUST revert
    if _run_step(cfg, "scale-replicas"):
        orig_replicas = int(cfg["orig_cluster_replicas"] or "1")

        def do_scale():
            instance.update_params(numReplicas=orig_replicas + 1, wait_for_ready=True)

        def revert_scale():
            instance.update_params(numReplicas=orig_replicas, wait_for_ready=True)

        # change under traffic, then revert under traffic
        from suite_utils import (
            change_then_revert,
        )  # local import to avoid unused if step not selected

        change_then_revert(instance, ssl, do_scale, revert_scale)
        assert_data(instance, ssl, msg="Data missing after replica scaling")

    # 6) Update memory (resize) — no revert required
    if _run_step(cfg, "resize"):
        new_type = cfg["new_instance_type"] or cfg["orig_instance_type"]
        instance.update_instance_type(new_type, wait_until_ready=True)
        assert_data(instance, ssl, msg="Data missing after resize")

    # 7) Upgrade (last)
    if _run_step(cfg, "upgrade"):
        instance.upgrade(
            service_id=cfg["service_id"],
            product_tier_id=instance._product_tier_id,
            source_version=None,
            target_version=None,
            wait_until_ready=True,
        )
        assert_data(instance, ssl, msg="Data missing after upgrade")

    # 8) OOM
    if _run_step(cfg, "oom"):
        stress_oom(instance, ssl=ssl, resource_key=rk)
