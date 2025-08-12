import pytest
from .suite_utils import (
    add_data,
    assert_data,
    change_then_revert,
    stress_oom,
    assert_multi_zone,
)


def _run_step(cfg, name):
    steps = cfg["e2e_steps"]
    return "all" in steps or name in steps


def test_cluster_pack(instance):
    """
    Cluster pack (cluster-Single-Zone / cluster-Multi-Zone).
    Steps: failover, stopstart, scale-shards, scale-replicas, resize, oom, upgrade
    """
    ssl = instance._cfg["tls"]
    cfg = instance._cfg

    valid = {
        "failover",
        "stopstart",
        "scale-shards",
        "scale-replicas",
        "resize",
        "oom",
        "upgrade",
    }
    if cfg["e2e_steps"] != {"all"} and not (cfg["e2e_steps"] & valid):
        pytest.skip("No selected steps for cluster pack")

    add_data(instance, ssl)

    # 0) Ensure MZ distribution
    if "multi-zone" in cfg["resource_key"].lower():
        assert_multi_zone(instance, host_count=cfg["host_count"])

    # 1) Failover & persistence
    if _run_step(cfg, "failover"):
        rep_id = (
            "cluster-sz-1" if "Single-Zone" in cfg["resource_key"] else "cluster-mz-4"
        )
        instance.trigger_failover(replica_id=rep_id, wait_for_ready=True)
        assert_data(instance, ssl, msg="Data lost after cluster failover")

    # 2) Stop/Start immediately after failover
    if _run_step(cfg, "stopstart"):
        instance.stop(wait_for_ready=True)
        instance.start(wait_for_ready=True)
        assert_data(instance, ssl, msg="Data missing after stop/start")

    # 3) Change shards (hostCount) — MUST revert before next test
    if _run_step(cfg, "scale-shards"):
        orig_hosts = int(cfg["orig_host_count"] or cfg["host_count"] or "6")

        def do_shards():
            instance.update_params(hostCount=orig_hosts + 2, wait_for_ready=True)

        def revert_shards():
            instance.update_params(hostCount=orig_hosts, wait_for_ready=True)

        change_then_revert(instance, ssl, do_shards, revert_shards)
        assert_data(instance, ssl, msg="Data missing after hostCount change")

    # 4) Change replicas (clusterReplicas) — MUST revert
    if _run_step(cfg, "scale-replicas"):
        orig_replicas = int(cfg["orig_cluster_replicas"] or "1")

        def do_replicas():
            instance.update_params(
                clusterReplicas=str(orig_replicas + 1), wait_for_ready=True
            )

        def revert_replicas():
            instance.update_params(
                clusterReplicas=str(orig_replicas), wait_for_ready=True
            )

        change_then_revert(instance, ssl, do_replicas, revert_replicas)
        assert_data(instance, ssl, msg="Data missing after clusterReplicas change")

    # 5) Update memory (resize)
    if _run_step(cfg, "resize"):
        new_type = cfg["new_instance_type"] or cfg["orig_instance_type"]
        instance.update_instance_type(new_type, wait_until_ready=True)
        assert_data(instance, ssl, msg="Data missing after resize")

    # 6) Upgrade (last)
    if _run_step(cfg, "upgrade"):
        instance.upgrade(
            service_id=cfg["service_id"],
            product_tier_id=instance._product_tier_id,
            source_version=None,
            target_version=None,
            wait_until_ready=True,
        )
        assert_data(instance, ssl, msg="Data missing after upgrade")

    # 7) OOM
    if _run_step(cfg, "oom"):
        stress_oom(instance, ssl=ssl, resource_key=cfg["resource_key"])
