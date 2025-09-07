import logging
import pytest
from .suite_utils import (
    add_data,
    assert_data,
    change_then_revert,
    stress_oom,
    assert_multi_zone,
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def _run_step(cfg, name):
    steps = cfg["e2e_steps"]
    return "all" in steps or name in steps


def test_cluster_pack(instance):
    """
    Cluster pack (cluster-Single-Zone / cluster-Multi-Zone).
    Steps: failover, stopstart, scale-shards, scale-replicas, resize, oom, upgrade
    """
    logging.info("Starting test_cluster_pack")
    ssl = instance._cfg["tls"]
    cfg = instance._cfg

    valid = {
        "failover",
        "stopstart",
        "network_change",
        "scale-shards",
        "scale-replicas",
        "resize",
        "oom",
        "upgrade",
    }
    if cfg["e2e_steps"] != {"all"} and not (cfg["e2e_steps"] & valid):
        logging.warning("No selected steps for cluster pack. Skipping test.")
        pytest.skip("No selected steps for cluster pack")

    logging.debug("Adding initial data to the instance")
    add_data(instance, ssl, network_type=cfg["network_type"])

    # 0) Ensure MZ distribution
    if "multi-zone" in cfg["resource_key"].lower():
        logging.info("Ensuring multi-zone distribution")
        assert_multi_zone(instance, host_count=cfg["host_count"])

    # 1) Failover & persistence
    if _run_step(cfg, "failover"):
        logging.info("Triggering failover")
        rep_id = (
            "cluster-mz-4" if "Multi-Zone" in cfg["resource_key"] else "cluster-sz-4"
        )
        instance.trigger_failover(replica_id=rep_id, wait_for_ready=True)
        logging.debug("Validating data after failover")
        assert_data(
            instance,
            ssl,
            msg="Data lost after cluster failover",
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

    if _run_step(cfg, "network_change"):
        old_network = cfg["network_type"]
        new_network = "PRIVATE" if old_network == "PUBLIC" else "PUBLIC"
        logging.info(f"Changing network type from {old_network} to {new_network}")
        instance.update_params(network_type=new_network, wait_until_ready=True)
        cfg["network_type"] = new_network
        if old_network == "PRIVATE":
            assert_data(
                instance,
                ssl,
                msg=f"Data missing after network change to {new_network}",
                network_type=cfg["network_type"],
            )
        logging.info(f"Changing back network type from {new_network} to {old_network}")
        instance.update_params(network_type=old_network, wait_until_ready=True)
        cfg["network_type"] = old_network
        assert_data(
            instance,
            ssl,
            msg=f"Data missing after network change back to {old_network}",
            network_type=cfg["network_type"],
        )

    # 3) Change shards (hostCount) — MUST revert before next test
    if _run_step(cfg, "scale-shards"):
        logging.info("Scaling shards")
        orig_hosts = int(cfg["orig_host_count"] or cfg["host_count"] or "6")

        def do_shards():
            logging.debug("Increasing host count")
            instance.update_params(hostCount=orig_hosts + 2, wait_for_ready=True)

        def revert_shards():
            logging.debug("Reverting host count")
            instance.update_params(hostCount=orig_hosts, wait_for_ready=True)

        change_then_revert(
            instance, ssl, do_shards, revert_shards, network_type=cfg["network_type"]
        )
        logging.debug("Validating data after hostCount change")
        assert_data(
            instance,
            ssl,
            msg="Data missing after hostCount change",
            network_type=cfg["network_type"],
        )

    # 4) Change replicas (clusterReplicas) — MUST revert
    if _run_step(cfg, "scale-replicas"):
        logging.info("Scaling replicas")
        orig_replicas = int(cfg["orig_cluster_replicas"] or "1")

        def do_replicas():
            logging.debug("Increasing cluster replicas")
            instance.update_params(
                clusterReplicas=str(orig_replicas + 1), wait_for_ready=True
            )

        def revert_replicas():
            logging.debug("Reverting cluster replicas")
            instance.update_params(
                clusterReplicas=str(orig_replicas), wait_for_ready=True
            )

        change_then_revert(
            instance,
            ssl,
            do_replicas,
            revert_replicas,
            network_type=cfg["network_type"],
        )
        logging.debug("Validating data after clusterReplicas change")
        assert_data(
            instance,
            ssl,
            msg="Data missing after clusterReplicas change",
            network_type=cfg["network_type"],
        )

    if _run_step(cfg, "oom"):
        logging.info("Simulating OOM")
        stress_oom(
            instance,
            ssl=ssl,
            network_type=cfg["network_type"],
            query_size="big",
            stress_oomers=10,
        )
        logging.debug("Passed OOM stress test")

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

    logging.info("Completed test_cluster_pack")
