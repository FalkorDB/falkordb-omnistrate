import logging
import pytest
from .suite_utils import add_data, assert_data, stress_oom

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def _run_step(cfg, name):
    steps = cfg["e2e_steps"]
    return "all" in steps or name in steps


def test_standalone_pack(instance):
    """
    Standalone pack.
    IMPORTANT: No zero-downtime traffic here (standalone can have downtime).
    Steps controlled by --e2e-steps (comma separated), default 'all':
      failover, stopstart, resize, oom, upgrade
    """
    logging.info("Starting test_standalone_pack")
    ssl = instance._cfg["tls"]
    cfg = instance._cfg

    selected = cfg["e2e_steps"]
    valid = {"failover", "stopstart", "network_change", "resize", "oom", "upgrade"}
    if selected != {"all"} and not (selected & valid):
        logging.warning("No selected steps for standalone pack. Skipping test.")
        pytest.skip("No selected steps for standalone pack")

    # Prepare some data once if we run any data-affecting step
    add_first = any(
        _run_step(cfg, s) for s in ("failover", "stopstart", "resize", "oom", "upgrade")
    )
    if add_first:
        logging.debug("Adding initial data to the instance")
        add_data(instance, ssl, network_type=cfg["network_type"])

    # 1) Failover & persistence
    if _run_step(cfg, "failover"):
        logging.info("Triggering failover")
        ep_id = instance.get_resource_id("node-s")
        instance.trigger_failover(
            replica_id="node-f-0" if cfg["tier_name"].startswith("free") else "node-s-0",
            wait_for_ready=True,
            resource_id=ep_id,
        )
        logging.debug("Validating data after failover")
        assert_data(
            instance,
            ssl,
            msg="Data lost after failover",
            network_type=cfg["network_type"],
        )

    # 2) Stop/Start immediately after failover (or even if failover not selected)
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

    if _run_step(cfg, "oom"):
        logging.info("Simulating OOM")
        stress_oom(
            instance,
            ssl=ssl,
            query_size=(
                "small"
                if "free" in cfg["tier_name"]
                else "medium" if "startup" in cfg["tier_name"] else "big"
            ),
            network_type=cfg["network_type"],
        )
        logging.debug("Passed OOM stress test")

    if _run_step(cfg, "resize"):
        logging.info("Resizing instance memory")
        add_data(
            instance,
            ssl=ssl,
            network_type=cfg["network_type"],
        )
        new_type = cfg["new_instance_type"] or cfg["orig_instance_type"]
        instance.update_instance_type(new_type, wait_until_ready=True)
        logging.debug("Validating data after resize")
        assert_data(
            instance,
            ssl,
            msg="Data missing after resize",
            network_type=cfg["network_type"],
        )

    logging.info("Completed test_standalone_pack")
