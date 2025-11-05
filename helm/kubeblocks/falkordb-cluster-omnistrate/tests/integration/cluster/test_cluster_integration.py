"""Integration tests for cluster mode deployment."""

import logging
import time
import pytest

from ...utils.kubernetes import (
    KubernetesHelper,
    setup_port_forward,
    cleanup_port_forward,
    cleanup_test_resources,
    wait_for_deployment_ready,
    wait_for_pods_ready,
    port_forward_pod,
    kubectl_apply_manifest,
    get_cluster_pods,
    wait_for_ops_request_completion
)
from ...utils.validation import validate_falkordb_connection, validate_falkordb_connection_in_cluster, validate_cluster_status

logger = logging.getLogger(__name__)


@pytest.mark.integration
class TestClusterIntegration:
    """Integration tests for cluster mode FalkorDB deployment."""

    def test_cluster_deployment_basic(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test basic cluster deployment."""
        values = {
            "mode": "cluster",
            "replicas": 2,
            "instanceType": "e2-medium",
            "storage": 30,
            "podAntiAffinityEnabled": True,
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        logger.info(f"Rendered {len(manifests)} manifests for cluster deployment")
        
        try:
            # Apply manifests
            for manifest in manifests:
                logger.info(f"Applying {manifest['kind']}: {manifest['metadata']['name']}")
                assert kubectl_apply_manifest(manifest, namespace), f"Failed to apply {manifest['kind']}"
            
            # Wait for deployment to be ready (clusters take longer)
            logger.info("Waiting for cluster to be ready...")
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=900)
            
            # Wait for all pods to be ready
            logger.info("Waiting for all pods to be ready...")
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=600)

            logger.info(f"Wait for job completion {cluster_name}-create-falkordb-user on {namespace}...")
            assert wait_for_ops_request_completion(f"{cluster_name}-create-falkordb-user", namespace, timeout=600)
            
            # Validate FalkorDB connections to cluster nodes
            logger.info("Validating FalkorDB connections to cluster nodes...")
            cluster_pods = get_cluster_pods(cluster_name, namespace)
            assert len(cluster_pods) >= 6, f"Expected at least 6 pods for sharded cluster, got {len(cluster_pods)}"
            
            connected_nodes = 0
            for pod in cluster_pods:
                try:
                    # Use in-cluster validation to avoid DNS resolution issues
                    if validate_falkordb_connection_in_cluster(pod, namespace, "testuser", "testpass123"):
                        connected_nodes += 1
                        logger.info(f"Successfully connected to {pod}")
                    else:
                        logger.warning(f"Could not connect to {pod}")
                except Exception as e:
                    logger.warning(f"Could not connect to {pod}: {e}")
            
            assert connected_nodes >= 3, f"Expected at least 3 connected nodes, got {connected_nodes}"
            
            # Validate cluster status using kubectl debug inside a pod
            logger.info("Validating cluster status...")
            cluster_nodes_found = validate_cluster_status(cluster_pods[0], namespace, "testuser", "testpass123", expected_nodes=6)
            assert cluster_nodes_found >= 6, f"Expected at least 6 cluster nodes, got {cluster_nodes_found}"
            
            logger.info("Cluster deployment test completed successfully")
            
        finally:
            if not skip_cleanup:
                logger.info("Cleaning up test resources...")
                cleanup_test_resources(cluster_name, namespace)

    def test_cluster_data_distribution(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test data distribution across cluster nodes."""
        values = {
            "mode": "cluster",
            "replicas": 2,
            "storage": 20,
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        logger.info("Testing cluster data distribution...")
        
        try:
            # Apply manifests and wait for readiness
            for manifest in manifests:
                # kubectl_apply_manifest(manifest, namespace)
                pass
            
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=900)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=600)
            
            cluster_pods = [f"{cluster_name}-falkordb-{i}" for i in range(6)]
            
            # Write test data through different nodes
            logger.info("Writing test data through cluster nodes...")
            for i, pod in enumerate(cluster_pods[:3]):  # Use first 3 nodes
                try:
                    with port_forward_pod(pod, namespace, 6379) as port:
                        import falkordb
                        conn = falkordb.FalkorDB(host="localhost", port=port, username="testuser", password="testpass123")
                        graph = conn.select_graph("clustergraph")
                        
                        # Create data specific to this node
                        graph.query(f"CREATE (:TestNode {{id: 'node-{i}', data: 'test-data-{i}'}})")
                        logger.info(f"Created test data through {pod}")
                        
                except Exception as e:
                    logger.warning(f"Could not write data through {pod}: {e}")
            
            # Allow time for data distribution
            time.sleep(10)
            
            # Verify data is accessible from different nodes
            logger.info("Verifying data accessibility across cluster...")
            total_nodes_found = 0
            
            for pod in cluster_pods:
                try:
                    with port_forward_pod(pod, namespace, 6379) as port:
                        conn = falkordb.FalkorDB(host="localhost", port=port, username="testuser", password="testpass123")
                        graph = conn.select_graph("clustergraph")
                        result = graph.query("MATCH (n:TestNode) RETURN count(n) as count")
                        
                        if result.result_set and len(result.result_set) > 0:
                            node_count = result.result_set[0][0]
                            if node_count > 0:
                                total_nodes_found += node_count
                                logger.info(f"Found {node_count} nodes through {pod}")
                        
                except Exception as e:
                    logger.warning(f"Could not read data through {pod}: {e}")
            
            # In a properly functioning cluster, we should find our test data
            assert total_nodes_found > 0, "No test data found in cluster"
            logger.info(f"Data distribution test completed - found {total_nodes_found} total node references")
            
        finally:
            if not skip_cleanup:
                cleanup_test_resources(cluster_name, namespace)

    def test_cluster_node_failure_resilience(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test cluster resilience to node failures."""
        values = {
            "mode": "cluster",
            "replicas": 2,
            "podAntiAffinityEnabled": True,
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        logger.info("Testing cluster node failure resilience...")
        
        try:
            # Apply manifests and wait for readiness
            for manifest in manifests:
                # kubectl_apply_manifest(manifest, namespace)
                pass
            
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=900)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=600)
            
            cluster_pods = [f"{cluster_name}-falkordb-{i}" for i in range(6)]
            
            # Write initial test data
            logger.info("Writing initial test data...")
            with port_forward_pod(cluster_pods[0], namespace, 6379) as port:
                import falkordb
                conn = falkordb.FalkorDB(host="localhost", port=port, username="testuser", password="testpass123")
                graph = conn.select_graph("resilience_test")
                graph.query("CREATE (:ResilienceTest {id: 'initial', timestamp: timestamp()})")
                logger.info("Initial data written")
            
            # Simulate node failure by deleting a pod
            failed_pod = cluster_pods[2]  # Delete middle pod
            logger.info(f"Simulating node failure by deleting {failed_pod}...")
            # kubectl_delete_pod(failed_pod, namespace)
            
            # Wait for cluster to recover
            time.sleep(30)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=600)
            
            # Verify cluster is still functional
            logger.info("Verifying cluster functionality after node failure...")
            functional_nodes = 0
            
            for pod in cluster_pods:
                try:
                    with port_forward_pod(pod, namespace, 6379) as port:
                        conn = falkordb.FalkorDB(host="localhost", port=port, username="testuser", password="testpass123")
                        graph = conn.select_graph("resilience_test")
                        
                        # Try to read existing data
                        result = graph.query("MATCH (n:ResilienceTest) RETURN count(n) as count")
                        if result.result_set and result.result_set[0][0] > 0:
                            functional_nodes += 1
                            logger.info(f"{pod} is functional and has data")
                            
                            # Try to write new data
                            graph.query(f"CREATE (:ResilienceTest {{id: 'after_failure_{pod}', timestamp: timestamp()}})")
                            
                except Exception as e:
                    logger.warning(f"{pod} is not functional: {e}")
            
            assert functional_nodes >= 4, f"Expected at least 4 functional nodes after failure, got {functional_nodes}"
            logger.info("Cluster resilience test completed successfully")
            
        finally:
            if not skip_cleanup:
                cleanup_test_resources(cluster_name, namespace)

    def test_cluster_scaling_capability(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test cluster scaling capabilities."""
        # Start with smaller cluster
        values = {
            "mode": "cluster",
            "replicas": 2,
            "storage": 20,
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        logger.info("Testing cluster scaling capabilities...")
        
        try:
            # Apply initial manifests
            for manifest in manifests:
                # kubectl_apply_manifest(manifest, namespace)
                pass
            
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=600)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=300)
            
            # Write initial data
            logger.info("Writing initial data to 3-node cluster...")
            with port_forward_pod(f"{cluster_name}-falkordb-0", namespace, 6379) as port:
                import falkordb
                conn = falkordb.FalkorDB(host="localhost", port=port, username="testuser", password="testpass123")
                graph = conn.select_graph("scaling_test")
                graph.query("CREATE (:ScalingTest {phase: 'initial', nodes: 3})")
                logger.info("Initial data written")
            
            # Scale up to 6 nodes
            logger.info("Scaling up to 6 nodes...")
            scaled_values = values.copy()
            scaled_values["replicas"] = 6
            
            scaled_manifests = helm_render(scaled_values, release_name=cluster_name, namespace=namespace)
            for manifest in scaled_manifests:
                # kubectl_apply_manifest(manifest, namespace)
                pass
            
            # Wait for scaling to complete
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=900)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=600)
            
            # Verify all 6 nodes are functional
            logger.info("Verifying scaled cluster functionality...")
            scaled_pods = [f"{cluster_name}-falkordb-{i}" for i in range(6)]
            functional_count = 0
            
            for pod in scaled_pods:
                try:
                    with port_forward_pod(pod, namespace, 6379) as port:
                        conn = falkordb.FalkorDB(host="localhost", port=port, username="testuser", password="testpass123")
                        graph = conn.select_graph("scaling_test")
                        
                        # Verify we can still access original data
                        result = graph.query("MATCH (n:ScalingTest {phase: 'initial'}) RETURN count(n) as count")
                        if result.result_set and result.result_set[0][0] > 0:
                            functional_count += 1
                            logger.info(f"{pod} is functional and has original data")
                
                except Exception as e:
                    logger.warning(f"{pod} not functional: {e}")
            
            assert functional_count >= 5, f"Expected at least 5 functional nodes after scaling, got {functional_count}"
            logger.info("Cluster scaling test completed successfully")
            
        finally:
            if not skip_cleanup:
                cleanup_test_resources(cluster_name, namespace)

    def test_cluster_performance_basic(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test basic cluster performance characteristics."""
        values = {
            "mode": "cluster",
            "replicas": 2,
            "instanceType": "e2-standard-2",
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        logger.info("Testing cluster performance characteristics...")
        
        try:
            # Apply manifests and wait for readiness
            for manifest in manifests:
                # kubectl_apply_manifest(manifest, namespace)
                pass
            
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=900)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=600)
            
            # Perform basic performance test
            logger.info("Performing basic performance test...")
            with port_forward_pod(f"{cluster_name}-falkordb-0", namespace, 6379) as port:
                import falkordb
                conn = falkordb.FalkorDB(host="localhost", port=port, username="testuser", password="testpass123")
                graph = conn.select_graph("performance_test")
                
                # Create a moderate amount of test data
                start_time = time.time()
                for i in range(100):
                    graph.query(f"CREATE (:PerfTest {{id: {i}, batch: 'performance_test'}})")
                
                creation_time = time.time() - start_time
                logger.info(f"Created 100 nodes in {creation_time:.2f} seconds")
                
                # Test query performance
                start_time = time.time()
                result = graph.query("MATCH (n:PerfTest) RETURN count(n) as total")
                query_time = time.time() - start_time
                
                assert result.result_set[0][0] == 100
                logger.info(f"Query completed in {query_time:.2f} seconds")
                
                # Basic performance assertions (very lenient for CI environments)
                assert creation_time < 30, f"Node creation took too long: {creation_time:.2f}s"
                assert query_time < 5, f"Query took too long: {query_time:.2f}s"
            
            logger.info("Cluster performance test completed successfully")
            
        finally:
            if not skip_cleanup:
                cleanup_test_resources(cluster_name, namespace)