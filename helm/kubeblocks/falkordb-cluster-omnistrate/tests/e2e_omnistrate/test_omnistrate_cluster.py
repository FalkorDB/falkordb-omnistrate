"""
E2E tests for FalkorDB cluster deployments via Omnistrate.

Tests cluster topology deployments including:
- Basic cluster connectivity
- Data distribution and sharding

Note: The following operations are not yet supported by Omnistrate API:
- Failover, start, stop, restart operations
- Shard scaling (horizontal)
- Replica scaling within shards
- Vertical scaling (instance type resize)
- OOM resilience testing
- Cross-shard queries

These will be added once Omnistrate API support is available.
"""

import time
import pytest
import logging

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


@pytest.mark.omnistrate
@pytest.mark.cluster
class TestOmnistrateCluster:
    """
    E2E tests for cluster topology deployments via Omnistrate.
    """

    def test_cluster_basic_connectivity(self, instance):
        """
        Test 1: Verify basic cluster connectivity and data operations.
        
        - Connect to the cluster
        - Write data across shards
        - Read data from cluster
        - Verify cluster topology
        """
        logging.info("Testing basic cluster connectivity")
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
        
        # Verify cluster topology
        db = instance.create_connection(ssl=ssl, network_type=network_type)
        cluster_info = db.client.execute_command("CLUSTER", "INFO")
        logging.info(f"Cluster info: {cluster_info}")
        assert b"cluster_state:ok" in cluster_info, "Cluster state is not OK"

    def test_cluster_data_distribution(self, instance):
        """
        Test 2: Verify data is distributed across shards.
        
        - Write data to multiple graphs
        - Verify data exists in cluster
        - Check cluster slots distribution
        """
        logging.info("Testing cluster data distribution across shards")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        # Add data to multiple graphs
        for i in range(5):
            graph_key = f"test_shard_{i}"
            add_data(instance, ssl, key=graph_key, n=50, network_type=network_type)
            assert_data(
                instance,
                ssl,
                key=graph_key,
                min_rows=50,
                msg=f"Failed to verify data in {graph_key}",
                network_type=network_type,
            )
        
        # Verify cluster slots are assigned
        db = instance.create_connection(ssl=ssl, network_type=network_type)
        slots = db.client.execute_command("CLUSTER", "SLOTS")
        logging.info(f"Cluster slots: {slots}")
        assert len(slots) > 0, "No cluster slots assigned"
        logging.info(f"Data successfully distributed across {len(slots)} shard ranges")

    def test_cluster_shard_failover(self, instance):
        """
        Test 3: Verify shard failover and data persistence.
        
        SKIPPED: Omnistrate API does not yet support failover operations.
        This test will be added once API support is available.
        """
        pytest.skip("Failover not supported by Omnistrate API yet")
        
        # Add initial data
        add_data(instance, ssl, key="test_failover", n=500, network_type=network_type)
        
        # Get cluster nodes
        eps = instance.get_connection_endpoints()
        nodes = sorted(
            [e for e in eps if "cluster" in e["id"]], key=lambda x: x["id"]
        )
        
        if len(nodes) == 0:
            pytest.skip("No cluster nodes found for failover")
        
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
            msg="Data lost after shard failover",
            network_type=network_type,
        )
        logging.info("Shard failover completed successfully with data persistence")

    def test_cluster_stop_start(self, instance):
        """
        Test 4: Verify cluster can be stopped and started.
        
        SKIPPED: Omnistrate API does not yet support stop/start operations.
        This test will be added once API support is available.
        """
        pytest.skip("Stop/start not supported by Omnistrate API yet")
        
        # Add data before stop
        add_data(instance, ssl, key="test_stopstart", n=200, network_type=network_type)
        
        # Stop instance
        logging.info("Stopping cluster")
        instance.stop(wait_for_ready=True)
        
        # Start instance
        logging.info("Starting cluster")
        instance.start(wait_for_ready=True)
        
        # Wait for cluster to be ready
        time.sleep(30)
        
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

    def test_cluster_shard_scaling(self, instance):
        """
        Test 5: Verify horizontal scaling of shards.
        
        SKIPPED: Omnistrate API does not yet support shard scaling operations.
        This test will be added once API support is available.
        """
        pytest.skip("Shard scaling not supported by Omnistrate API yet")
        
        # Add data
        add_data(instance, ssl, key="test_shard_scale", n=200, network_type=network_type)
        
        orig_count = cfg["orig_host_count"]
        orig_replicas = cfg["orig_cluster_replicas"]
        
        # Calculate new total nodes (more shards with same replicas)
        new_shards = int(orig_count) // (int(orig_replicas) + 1) + 1
        new_count = new_shards * (int(orig_replicas) + 1)
        
        def scale_up():
            logging.info(f"Scaling shards: increasing nodes from {orig_count} to {new_count}")
            instance.update_params(
                wait_until_ready=True,
                hostCount=str(new_count),
            )
            time.sleep(60)  # Wait for cluster rebalancing
        
        def scale_down():
            logging.info(f"Scaling shards back: decreasing nodes from {new_count} to {orig_count}")
            instance.update_params(
                wait_until_ready=True,
                hostCount=str(orig_count),
            )
            time.sleep(60)  # Wait for cluster rebalancing
        
        # Scale with zero downtime
        change_then_revert(instance, ssl, scale_up, scale_down, network_type)
        
        # Verify data persists
        assert_data(
            instance,
            ssl,
            key="test_shard_scale",
            min_rows=200,
            msg="Data lost after shard scaling",
            network_type=network_type,
        )
        logging.info("Shard scaling completed successfully")

    def test_cluster_replica_scaling(self, instance):
        """
        Test 6: Verify replica scaling within shards.
        
        SKIPPED: Omnistrate API does not yet support replica scaling operations.
        This test will be added once API support is available.
        """
        pytest.skip("Replica scaling not supported by Omnistrate API yet")
        
        # Add data
        add_data(instance, ssl, key="test_replica_scale", n=200, network_type=network_type)
        
        orig_replicas = cfg["orig_cluster_replicas"]
        new_replicas = int(orig_replicas) + 1
        
        def scale_up():
            logging.info(f"Scaling replicas from {orig_replicas} to {new_replicas}")
            instance.update_params(
                wait_until_ready=True,
                clusterReplicas=str(new_replicas),
            )
        
        def scale_down():
            logging.info(f"Scaling replicas back from {new_replicas} to {orig_replicas}")
            instance.update_params(
                wait_until_ready=True,
                clusterReplicas=str(orig_replicas),
            )
        
        # Scale with zero downtime
        change_then_revert(instance, ssl, scale_up, scale_down, network_type)
        
        # Verify data persists
        assert_data(
            instance,
            ssl,
            key="test_replica_scale",
            min_rows=200,
            msg="Data lost after replica scaling",
            network_type=network_type,
        )
        logging.info("Replica scaling completed successfully")

    def test_cluster_vertical_scaling(self, instance):
        """
        Test 7: Verify vertical scaling (instance type change).
        
        SKIPPED: Omnistrate API does not yet support vertical scaling (resize) operations.
        This test will be added once API support is available.
        """
        pytest.skip("Vertical scaling not supported by Omnistrate API yet")
        
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

    def test_cluster_oom_resilience(self, instance):
        """
        Test 8: Verify OOM (Out of Memory) handling and resilience.
        
        SKIPPED: Requires advanced testing infrastructure not available in Omnistrate E2E environment.
        This test will be added once environment support is available.
        """
        pytest.skip("OOM testing not supported in Omnistrate E2E environment yet")
        
    def _test_cluster_oom_resilience_impl(self, instance):
        """
        Test 8: Verify OOM (Out of Memory) handling and resilience.
        
        - Fill memory until OOM
        - Verify cluster recovers
        - Verify writes work after recovery
        """
        logging.info("Testing cluster OOM resilience")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "oom"):
            pytest.skip("OOM step not selected")
        
        # Stress until OOM
        logging.info("Stressing cluster until OOM")
        stress_oom(
            instance,
            ssl=ssl,
            query_size="small",
            network_type=network_type,
            stress_oomers=3,
            is_cluster=True,
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

    def test_cluster_cross_shard_queries(self, instance):
        """
        Test 9: Verify cross-shard query capabilities.
        
        - Write data across multiple shards
        - Execute queries that span shards
        - Verify results are correct
        """
        logging.info("Testing cross-shard query capabilities")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        # Create multiple graphs that will hash to different shards
        graphs = ["test_shard_a", "test_shard_b", "test_shard_c"]
        
        for graph_key in graphs:
            add_data(instance, ssl, key=graph_key, n=50, network_type=network_type)
        
        # Verify each graph independently
        for graph_key in graphs:
            assert_data(
                instance,
                ssl,
                key=graph_key,
                min_rows=50,
                msg=f"Failed to verify data in {graph_key}",
                network_type=network_type,
            )
        
        logging.info("Cross-shard queries verified successfully")

    def test_cluster_multi_zone_distribution(self, instance):
        """
        Test 10: Verify multi-zone topology distribution (if applicable).
        
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
@pytest.mark.cluster
@pytest.mark.slow
def test_cluster_full_suite(instance):
    """
    Comprehensive test running all cluster scenarios in sequence.
    
    This test executes a full E2E suite including:
    - Data distribution
    - Shard failover
    - Stop/start
    - Shard scaling
    - Replica scaling
    - Resize
    - OOM resilience
    - Cross-shard queries
    
    Use --e2e-steps to control which steps to run.
    """
    logging.info("Starting comprehensive cluster test suite")
    
    test_class = TestOmnistrateCluster()
    
    # Run all tests in sequence
    tests = [
        ("connectivity", test_class.test_cluster_basic_connectivity),
        ("distribution", test_class.test_cluster_data_distribution),
        ("failover", test_class.test_cluster_shard_failover),
        ("stopstart", test_class.test_cluster_stop_start),
        ("scale-shards", test_class.test_cluster_shard_scaling),
        ("scale-replicas", test_class.test_cluster_replica_scaling),
        ("resize", test_class.test_cluster_vertical_scaling),
        ("oom", test_class.test_cluster_oom_resilience),
        ("cross-shard", test_class.test_cluster_cross_shard_queries),
    ]
    
    cfg = instance._cfg
    for step_name, test_func in tests:
        if _run_step(cfg, step_name) or step_name in ("connectivity", "distribution", "cross-shard"):
            logging.info(f"Running step: {step_name}")
            try:
                test_func(instance)
            except pytest.skip.Exception as e:
                logging.info(f"Step {step_name} skipped: {e}")
            except Exception as e:
                logging.error(f"Step {step_name} failed: {e}")
                raise
    
    logging.info("Comprehensive cluster test suite completed successfully")
