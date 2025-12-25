"""
E2E tests for FalkorDB replication deployments via Omnistrate.

Tests replication topology deployments including:
- Basic replication connectivity

Note: The following operations are not yet supported by Omnistrate API:
- Failover operations (Omnistrate API, Sentinel-initiated)
- Start, stop, restart operations
- Replica scaling
- Vertical scaling (instance type resize)
- OOM resilience testing
- Multi-zone distribution testing

These will be added once Omnistrate API support is available.
"""

import time
import pytest
import logging
from redis import Sentinel
from redis.exceptions import TimeoutError, ConnectionError, ReadOnlyError, ResponseError

# Import local test utilities
from .test_utils import (
    add_data,
    assert_data,
    stress_oom,
    assert_multi_zone,
    run_zero_downtime,
    change_then_revert,
)

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def _run_step(cfg, name):
    """Check if a specific test step should be executed."""
    steps = cfg["e2e_steps"]
    return "all" in steps or name in steps


def _sentinel_client(instance, ssl):
    """Create a Sentinel client for the replication instance."""
    eps = instance.get_connection_endpoints()
    sent = next(e for e in eps if e["id"].startswith("sentinel"))
    return Sentinel(
        sentinels=[
            (sent["endpoint"], sent["ports"][0]),
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
        },
    )


@pytest.mark.omnistrate
@pytest.mark.replication
class TestOmnistrateReplication:
    """
    E2E tests for replication topology deployments via Omnistrate.
    """

    def test_replication_basic_connectivity(self, instance):
        """
        Test 1: Verify basic connectivity and data operations.
        
        - Connect to the replication cluster
        - Write data to master
        - Read data from replicas
        - Verify sentinel connectivity
        """
        logging.info("Testing basic replication connectivity")
        ssl = instance._cfg["tls"]
        network_type = instance._cfg["network_type"]
        
        # Add and verify data
        add_data(instance, ssl, key="test_connectivity", n=100, network_type=network_type)
        assert_data(
            instance,
            ssl,
            key="test_connectivity",
            min_rows=100,
            msg="Failed to verify connectivity data",
            network_type=network_type,
        )
        
        # Verify sentinel connectivity
        sent = _sentinel_client(instance, ssl)
        master_info = sent.discover_master("master")
        assert master_info is not None, "Failed to discover master via Sentinel"
        logging.info(f"Master discovered at: {master_info}")

    def test_replication_failover_persistence(self, instance):
        """
        Test 2: Verify data persistence across failover.
        
        SKIPPED: Omnistrate API does not yet support failover operations.
        This test will be added once API support is available.
        """
        pytest.skip("Failover not supported by Omnistrate API yet")
        
        # Add initial data
        add_data(instance, ssl, key="test_failover", n=500, network_type=network_type)
        
        # Get current master endpoint
        eps = instance.get_connection_endpoints()
        nodes = sorted(
            [e for e in eps if e["id"].startswith("node-")], key=lambda x: x["id"]
        )
        
        # Trigger failover on first node
        logging.info(f"Triggering failover on node: {nodes[0]['id']}")
        
        def do_failover():
            instance.trigger_failover(
                replica_id=nodes[0]["id"],
                wait_for_ready=True,
            )
        
        # Run with zero-downtime traffic
        run_zero_downtime(instance, ssl, do_failover, network_type)
        
        # Verify data persists
        assert_data(
            instance,
            ssl,
            key="test_failover",
            min_rows=500,
            msg="Data lost after failover",
            network_type=network_type,
        )
        logging.info("Failover completed successfully with data persistence")

    def test_replication_stop_start(self, instance):
        """
        Test 3: Verify instance can be stopped and started.
        
        SKIPPED: Omnistrate API does not yet support stop/start operations.
        This test will be added once API support is available.
        """
        pytest.skip("Stop/start not supported by Omnistrate API yet")
        
        # Add data before stop
        add_data(instance, ssl, key="test_stopstart", n=200, network_type=network_type)
        
        # Stop instance
        logging.info("Stopping instance")
        instance.stop(wait_for_ready=True)
        
        # Start instance
        logging.info("Starting instance")
        instance.start(wait_for_ready=True)
        
        # Verify data persists
        assert_data(
            instance,
            ssl,
            key="test_stopstart",
            min_rows=200,
            msg="Data lost after stop/start",
            network_type=network_type,
        )
        logging.info("Stop/start completed successfully")

    def test_replication_sentinel_failover(self, instance):
        """
        Test 4: Verify sentinel-initiated failover.
        
        - Connect via Sentinel
        - Trigger manual failover via Sentinel
        - Verify new master elected
        - Verify data persists
        """
        logging.info("Testing sentinel-initiated failover")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "sentinel-failover"):
            pytest.skip("Sentinel-failover step not selected")
        
        # Add data
        add_data(instance, ssl, key="test_sentinel", n=300, network_type=network_type)
        
        # Get sentinel client
        sent = _sentinel_client(instance, ssl)
        old_master = sent.discover_master("master")
        logging.info(f"Current master: {old_master}")
        
        # Trigger sentinel failover
        logging.info("Triggering sentinel failover")
        master = sent.master_for("master")
        master.execute_command("CLIENT", "PAUSE", "5000")
        time.sleep(10)  # Wait for sentinel to detect and failover
        
        # Verify new master
        new_master = sent.discover_master("master")
        logging.info(f"New master: {new_master}")
        assert new_master != old_master, "Failover did not result in new master"
        
        # Verify data
        assert_data(
            instance,
            ssl,
            key="test_sentinel",
            min_rows=300,
            msg="Data lost after sentinel failover",
            network_type=network_type,
        )
        logging.info("Sentinel failover completed successfully")

    def test_replication_multiple_failovers(self, instance):
        """
        Test 5: Verify multiple sequential failovers.
        
        - Perform multiple failovers in sequence
        - Verify data persists through all failovers
        - Test resilience
        """
        logging.info("Testing multiple sequential failovers")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "second-failover"):
            pytest.skip("Second-failover step not selected")
        
        # Add data
        add_data(instance, ssl, key="test_multiple", n=250, network_type=network_type)
        
        eps = instance.get_connection_endpoints()
        nodes = sorted(
            [e for e in eps if e["id"].startswith("node-")], key=lambda x: x["id"]
        )
        
        # Perform multiple failovers
        for i, node in enumerate(nodes[:2]):
            logging.info(f"Failover {i+1}: Triggering on node {node['id']}")
            
            def do_failover():
                instance.trigger_failover(
                    replica_id=node["id"],
                    wait_for_ready=True,
                )
            
            run_zero_downtime(instance, ssl, do_failover, network_type)
            time.sleep(10)  # Wait for stabilization
        
        # Verify data persists
        assert_data(
            instance,
            ssl,
            key="test_multiple",
            min_rows=250,
            msg="Data lost after multiple failovers",
            network_type=network_type,
        )
        logging.info("Multiple failovers completed successfully")

    def test_replication_replica_scaling(self, instance):
        """
        Test 6: Verify horizontal scaling of replicas.
        
        - Scale up replicas
        - Verify increased capacity
        - Scale back down
        - Verify data persists throughout
        """
        logging.info("Testing replica scaling")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "scale-replicas"):
            pytest.skip("Scale-replicas step not selected")
        
        # Add data
        add_data(instance, ssl, key="test_scaling", n=200, network_type=network_type)
        
        orig_count = cfg["orig_host_count"]
        new_count = int(orig_count) + 2
        
        def scale_up():
            logging.info(f"Scaling replicas from {orig_count} to {new_count}")
            instance.update_params(
                wait_until_ready=True,
                hostCount=str(new_count),
            )
        
        def scale_down():
            logging.info(f"Scaling replicas back from {new_count} to {orig_count}")
            instance.update_params(
                wait_until_ready=True,
                hostCount=str(orig_count),
            )
        
        # Scale with zero downtime
        change_then_revert(instance, ssl, scale_up, scale_down, network_type)
        
        # Verify data persists
        assert_data(
            instance,
            ssl,
            key="test_scaling",
            min_rows=200,
            msg="Data lost after scaling",
            network_type=network_type,
        )
        logging.info("Replica scaling completed successfully")

    def test_replication_vertical_scaling(self, instance):
        """
        Test 7: Verify vertical scaling (instance type change).
        
        - Change instance type to larger size
        - Verify operation completes
        - Change back to original
        - Verify data persists
        """
        logging.info("Testing vertical scaling (instance type resize)")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "resize"):
            pytest.skip("Resize step not selected")
        
        new_type = cfg.get("new_instance_type")
        if not new_type:
            pytest.skip("No new instance type specified for resize test")
        
        # Add data
        add_data(instance, ssl, key="test_resize", n=200, network_type=network_type)
        
        orig_type = cfg["orig_instance_type"]
        
        def resize_up():
            logging.info(f"Resizing from {orig_type} to {new_type}")
            instance.update_instance_type(new_type, wait_until_ready=True)
        
        def resize_down():
            logging.info(f"Resizing back from {new_type} to {orig_type}")
            instance.update_instance_type(orig_type, wait_until_ready=True)
        
        # Resize with zero downtime
        change_then_revert(instance, ssl, resize_up, resize_down, network_type)
        
        # Verify data persists
        assert_data(
            instance,
            ssl,
            key="test_resize",
            min_rows=200,
            msg="Data lost after resize",
            network_type=network_type,
        )
        logging.info("Vertical scaling completed successfully")

    def test_replication_oom_resilience(self, instance):
        """
        Test 8: Verify OOM (Out of Memory) handling and resilience.
        
        - Fill memory until OOM
        - Verify cluster recovers
        - Verify writes work after recovery
        """
        logging.info("Testing OOM resilience")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "oom"):
            pytest.skip("OOM step not selected")
        
        # Stress until OOM
        logging.info("Stressing instance until OOM")
        stress_oom(
            instance,
            ssl=ssl,
            query_size="small",
            network_type=network_type,
            stress_oomers=2,
            is_cluster=False,
            timeout_seconds=180,
        )
        
        # Verify recovery - should be able to write again
        logging.info("Verifying recovery after OOM")
        time.sleep(30)  # Wait for recovery
        add_data(instance, ssl, key="test_oom_recovery", n=100, network_type=network_type)
        assert_data(
            instance,
            ssl,
            key="test_oom_recovery",
            min_rows=100,
            msg="Failed to write after OOM",
            network_type=network_type,
        )
        logging.info("OOM resilience test completed successfully")

    def test_replication_multi_zone_distribution(self, instance):
        """
        Test 9: Verify multi-zone topology distribution (if applicable).
        
        - Check nodes are distributed across availability zones
        - Verify minimum 2 AZs for multi-zone deployments
        """
        logging.info("Testing multi-zone distribution")
        cfg = instance._cfg
        
        if "multi-zone" not in cfg["resource_key"].lower():
            pytest.skip("Not a multi-zone deployment")
        
        # Verify multi-zone distribution
        host_count = int(cfg["orig_host_count"])
        assert_multi_zone(instance, host_count=host_count)
        logging.info("Multi-zone distribution verified successfully")


@pytest.mark.omnistrate
@pytest.mark.replication
@pytest.mark.slow
def test_replication_full_suite(instance):
    """
    Comprehensive test running all replication scenarios in sequence.
    
    This test executes a full E2E suite including:
    - Failover and persistence
    - Stop/start
    - Sentinel failover
    - Multiple failovers
    - Scaling operations
    - Resize
    - OOM resilience
    
    Use --e2e-steps to control which steps to run.
    """
    logging.info("Starting comprehensive replication test suite")
    
    test_class = TestOmnistrateReplication()
    
    # Run all tests in sequence
    tests = [
        ("connectivity", test_class.test_replication_basic_connectivity),
        ("failover", test_class.test_replication_failover_persistence),
        ("stopstart", test_class.test_replication_stop_start),
        ("sentinel-failover", test_class.test_replication_sentinel_failover),
        ("second-failover", test_class.test_replication_multiple_failovers),
        ("scale-replicas", test_class.test_replication_replica_scaling),
        ("resize", test_class.test_replication_vertical_scaling),
        ("oom", test_class.test_replication_oom_resilience),
    ]
    
    cfg = instance._cfg
    for step_name, test_func in tests:
        if _run_step(cfg, step_name) or step_name == "connectivity":
            logging.info(f"Running step: {step_name}")
            try:
                test_func(instance)
            except pytest.skip.Exception as e:
                logging.info(f"Step {step_name} skipped: {e}")
            except Exception as e:
                logging.error(f"Step {step_name} failed: {e}")
                raise
    
    logging.info("Comprehensive replication test suite completed successfully")
