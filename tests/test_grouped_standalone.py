import logging
import pytest
from .suite_utils import add_data, assert_data, stress_oom

# Configure logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

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
    valid = {"failover", "stopstart", "resize", "oom", "upgrade"}
    if selected != {"all"} and not (selected & valid):
        logging.warning("No selected steps for standalone pack. Skipping test.")
        pytest.skip("No selected steps for standalone pack")

    # Prepare some data once if we run any data-affecting step
    add_first = any(_run_step(cfg, s) for s in ("failover", "stopstart", "resize", "oom", "upgrade"))
    if add_first:
        logging.debug("Adding initial data to the instance")
        add_data(instance, ssl)

    # 1) Failover & persistence
    if _run_step(cfg, "failover"):
        logging.info("Triggering failover")
        ep_id = instance.get_resource_id("node-s")
        instance.trigger_failover(replica_id="node-s-0", wait_for_ready=True, resource_id=ep_id)
        logging.debug("Validating data after failover")
        assert_data(instance, ssl, msg="Data lost after failover")

    # 2) Stop/Start immediately after failover (or even if failover not selected)
    if _run_step(cfg, "stopstart"):
        logging.info("Stopping and starting the instance")
        instance.stop(wait_for_ready=True)
        instance.start(wait_for_ready=True)
        logging.debug("Validating data after stop/start")
        assert_data(instance, ssl, msg="Data missing after stop/start")

    # 3) Update memory (resize). No revert required by your policy.
    if _run_step(cfg, "resize"):
        logging.info("Resizing instance memory")
        new_type = cfg["new_instance_type"] or cfg["orig_instance_type"]
        instance.update_instance_type(new_type, wait_until_ready=True)
        logging.debug("Validating data after resize")
        assert_data(instance, ssl, msg="Data missing after resize")

    # 4) Upgrade (run last)
    if _run_step(cfg, "upgrade"):
        logging.info("Upgrading instance")
        instance.upgrade(
            service_id=cfg["service_id"],
            product_tier_id=instance._product_tier_id,
            source_version=None,
            target_version=None,
            wait_until_ready=True,
        )
        logging.debug("Validating data after upgrade")
        assert_data(instance, ssl, msg="Data missing after upgrade")

    # 5) OOM
    if _run_step(cfg, "oom"):
        logging.info("Simulating OOM")
        stress_oom(instance, ssl=ssl, resource_key=cfg["resource_key"])

    logging.info("Completed test_standalone_pack")
