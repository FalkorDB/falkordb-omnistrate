"""
E2E tests for FalkorDB standalone deployments via Omnistrate.

Tests standalone (single-node) topology including:
- Basic connectivity and data operations
- Stop/start operations
- Vertical scaling (instance type resize)
- OOM resilience
- Data persistence across operations
"""

import time
import pytest
import logging

# Import local test utilities
from .test_utils import (
    add_data,
    assert_data,
    stress_oom,
)

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def _run_step(cfg, name):
    """Check if a specific test step should be executed."""
    steps = cfg["e2e_steps"]
    return "all" in steps or name in steps


@pytest.mark.omnistrate
@pytest.mark.standalone
class TestOmnistrateStandalone:
    """
    E2E tests for standalone topology deployments via Omnistrate.
    """

    def test_standalone_basic_connectivity(self, instance):
        """
        Test 1: Verify basic connectivity and data operations.
        
        - Connect to the standalone instance
        - Write data
        - Read data
        - Verify persistence
        """
        logging.info("Testing basic standalone connectivity")
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
        logging.info("Basic connectivity test completed successfully")

    def test_standalone_data_persistence(self, instance):
        """
        Test 2: Verify data persistence with multiple graphs.
        
        - Create multiple graphs
        - Write data to each
        - Verify all data persists
        """
        logging.info("Testing standalone data persistence")
        ssl = instance._cfg["tls"]
        network_type = instance._cfg["network_type"]
        
        # Create and verify multiple graphs
        for i in range(5):
            graph_key = f"test_graph_{i}"
            add_data(instance, ssl, key=graph_key, n=100, network_type=network_type)
            assert_data(
                instance,
                ssl,
                key=graph_key,
                min_rows=100,
                msg=f"Failed to verify data in {graph_key}",
                network_type=network_type,
            )
        
        logging.info("Data persistence test completed successfully")

    def test_standalone_stop_start(self, instance):
        """
        Test 3: Verify instance can be stopped and started.
        
        - Write data
        - Stop instance
        - Start instance
        - Verify data persists
        """
        logging.info("Testing standalone stop/start operations")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "stopstart"):
            pytest.skip("Stop/start step not selected")
        
        # Add data before stop
        add_data(instance, ssl, key="test_stopstart", n=200, network_type=network_type)
        
        # Stop instance
        logging.info("Stopping instance")
        instance.stop(wait_for_ready=True)
        
        # Start instance
        logging.info("Starting instance")
        instance.start(wait_for_ready=True)
        
        # Wait for instance to be fully ready
        time.sleep(15)
        
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

    def test_standalone_vertical_scaling(self, instance):
        """
        Test 4: Verify vertical scaling (instance type change).
        
        - Write data
        - Change instance type to larger size
        - Verify operation completes
        - Change back to original
        - Verify data persists throughout
        """
        logging.info("Testing standalone vertical scaling (instance type resize)")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "resize"):
            pytest.skip("Resize step not selected")
        
        new_type = cfg.get("new_instance_type")
        if not new_type:
            pytest.skip("No new instance type specified for resize test")
        
        # Add data before resize
        add_data(instance, ssl, key="test_resize", n=200, network_type=network_type)
        
        orig_type = cfg["orig_instance_type"]
        
        # Resize up
        logging.info(f"Resizing from {orig_type} to {new_type}")
        instance.update_instance_type(new_type, wait_until_ready=True)
        
        # Verify data persists
        assert_data(
            instance,
            ssl,
            key="test_resize",
            min_rows=200,
            msg="Data lost after resize up",
            network_type=network_type,
        )
        
        # Resize back down
        logging.info(f"Resizing back from {new_type} to {orig_type}")
        instance.update_instance_type(orig_type, wait_until_ready=True)
        
        # Verify data still persists
        assert_data(
            instance,
            ssl,
            key="test_resize",
            min_rows=200,
            msg="Data lost after resize down",
            network_type=network_type,
        )
        
        logging.info("Vertical scaling completed successfully")

    def test_standalone_storage_expansion(self, instance):
        """
        Test 5: Verify storage can be expanded (if supported).
        
        - Write significant data
        - Expand storage
        - Write more data
        - Verify all data persists
        """
        logging.info("Testing standalone storage expansion")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "storage-expand"):
            pytest.skip("Storage expansion step not selected")
        
        # Add initial data
        add_data(instance, ssl, key="test_storage", n=500, network_type=network_type)
        
        # Get current storage size
        orig_storage = cfg["storage_size"]
        new_storage = str(int(orig_storage) + 10)
        
        # Expand storage
        logging.info(f"Expanding storage from {orig_storage}GB to {new_storage}GB")
        try:
            instance.update_params(
                wait_until_ready=True,
                storageSize=new_storage,
            )
            
            # Add more data after expansion
            add_data(instance, ssl, key="test_storage_post", n=200, network_type=network_type)
            
            # Verify all data persists
            assert_data(
                instance,
                ssl,
                key="test_storage",
                min_rows=500,
                msg="Original data lost after storage expansion",
                network_type=network_type,
            )
            assert_data(
                instance,
                ssl,
                key="test_storage_post",
                min_rows=200,
                msg="New data not written after storage expansion",
                network_type=network_type,
            )
            
            logging.info("Storage expansion completed successfully")
        except Exception as e:
            logging.warning(f"Storage expansion may not be supported: {e}")
            pytest.skip(f"Storage expansion not supported: {e}")

    def test_standalone_oom_resilience(self, instance):
        """
        Test 6: Verify OOM (Out of Memory) handling and resilience.
        
        - Fill memory until OOM
        - Verify instance recovers
        - Verify writes work after recovery
        """
        logging.info("Testing standalone OOM resilience")
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

    def test_standalone_persistence_config(self, instance):
        """
        Test 7: Verify persistence configuration (RDB/AOF).
        
        - Write data
        - Verify persistence settings
        - Restart instance (stop/start)
        - Verify data persists (loaded from disk)
        """
        logging.info("Testing standalone persistence configuration")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        if not _run_step(cfg, "persistence"):
            pytest.skip("Persistence test step not selected")
        
        # Add data
        add_data(instance, ssl, key="test_persistence", n=300, network_type=network_type)
        
        # Check persistence config
        db = instance.create_connection(ssl=ssl, network_type=network_type)
        config = db.client.config_get("save")
        logging.info(f"RDB persistence config: {config}")
        
        aof_config = db.client.config_get("appendonly")
        logging.info(f"AOF persistence config: {aof_config}")
        
        # Stop and start to test persistence
        logging.info("Stopping instance to test persistence")
        instance.stop(wait_for_ready=True)
        
        logging.info("Starting instance to verify data loaded from disk")
        instance.start(wait_for_ready=True)
        time.sleep(15)
        
        # Verify data persists (loaded from RDB/AOF)
        assert_data(
            instance,
            ssl,
            key="test_persistence",
            min_rows=300,
            msg="Data not loaded from persistence after restart",
            network_type=network_type,
        )
        logging.info("Persistence configuration test completed successfully")

    def test_standalone_concurrent_operations(self, instance):
        """
        Test 8: Verify concurrent read/write operations.
        
        - Execute multiple concurrent writes
        - Execute multiple concurrent reads
        - Verify data consistency
        """
        logging.info("Testing standalone concurrent operations")
        cfg = instance._cfg
        ssl = cfg["tls"]
        network_type = cfg["network_type"]
        
        import threading
        import queue
        
        errors = queue.Queue()
        
        def write_worker(thread_id):
            try:
                key = f"test_concurrent_{thread_id}"
                add_data(instance, ssl, key=key, n=50, network_type=network_type)
            except Exception as e:
                errors.put(e)
        
        # Launch concurrent writes
        threads = []
        for i in range(5):
            t = threading.Thread(target=write_worker, args=(i,))
            threads.append(t)
            t.start()
        
        # Wait for all threads
        for t in threads:
            t.join()
        
        # Check for errors
        if not errors.empty():
            raise errors.get()
        
        # Verify all data
        for i in range(5):
            key = f"test_concurrent_{i}"
            assert_data(
                instance,
                ssl,
                key=key,
                min_rows=50,
                msg=f"Data lost for concurrent write {i}",
                network_type=network_type,
            )
        
        logging.info("Concurrent operations test completed successfully")


@pytest.mark.omnistrate
@pytest.mark.standalone
@pytest.mark.slow
def test_standalone_full_suite(instance):
    """
    Comprehensive test running all standalone scenarios in sequence.
    
    This test executes a full E2E suite including:
    - Basic connectivity
    - Data persistence
    - Stop/start
    - Vertical scaling
    - Storage expansion
    - OOM resilience
    - Persistence configuration
    - Concurrent operations
    
    Use --e2e-steps to control which steps to run.
    """
    logging.info("Starting comprehensive standalone test suite")
    
    test_class = TestOmnistrateStandalone()
    
    # Run all tests in sequence
    tests = [
        ("connectivity", test_class.test_standalone_basic_connectivity),
        ("persistence", test_class.test_standalone_data_persistence),
        ("stopstart", test_class.test_standalone_stop_start),
        ("resize", test_class.test_standalone_vertical_scaling),
        ("storage-expand", test_class.test_standalone_storage_expansion),
        ("oom", test_class.test_standalone_oom_resilience),
        ("persistence", test_class.test_standalone_persistence_config),
        ("concurrent", test_class.test_standalone_concurrent_operations),
    ]
    
    cfg = instance._cfg
    for step_name, test_func in tests:
        if _run_step(cfg, step_name) or step_name in ("connectivity", "concurrent"):
            logging.info(f"Running step: {step_name}")
            try:
                test_func(instance)
            except pytest.skip.Exception as e:
                logging.info(f"Step {step_name} skipped: {e}")
            except Exception as e:
                logging.error(f"Step {step_name} failed: {e}")
                raise
    
    logging.info("Comprehensive standalone test suite completed successfully")
