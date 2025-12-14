"""Cluster integration test configuration."""

import pytest
import logging
from ...utils.kubernetes import (
    cleanup_test_resources,
    wait_for_deployment_ready,
    wait_for_pods_ready,
    kubectl_apply_manifest,
    get_cluster_pods,
    wait_for_ops_request_completion,
)

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def cluster_integration_values():
    """Return default values for cluster integration tests."""
    return {
        "mode": "cluster",
        "replicas": 2,  # Start smaller for faster setup
        # The chart's default `nodeAffinity` is Omnistrate-specific and will not match Kind nodes.
        # Integration tests run on Kind, so override it to allow scheduling.
        # NOTE: must be null (not `{}`) so Helm merge replaces the chart default map.
        "nodeAffinity": None,
        "instanceType": "low",
        "storage": 30,
        "podAntiAffinityEnabled": True,
        "falkordbUser": {
            "username": "testuser",
            "password": "testpass123"
        },
    }


@pytest.fixture
def cluster_test_timeout():
    """Return timeout for cluster tests (longer due to complexity)."""
    return 900  # 15 minutes


@pytest.fixture(scope="module")
def shared_cluster(helm_render, namespace, skip_cleanup, cluster_integration_values, worker_id):
    """
    Create a shared cluster for all tests in this module.
    This avoids the overhead of creating/destroying clusters for each test.
    
    Uses worker_id to create unique cluster names when running in parallel with pytest-xdist.
    """
    # Create unique cluster name per worker to avoid conflicts in parallel execution
    # Max length is 15 chars due to Kubernetes naming constraints
    if worker_id == "master":
        # Single process execution
        cluster_name = "shared-clstr"
    else:
        # Parallel execution - extract worker number from "gw0", "gw1", etc
        worker_num = worker_id.replace("gw", "")
        cluster_name = f"shared-clstr-{worker_num}"
    
    logger.info(f"Setting up shared cluster: {cluster_name}")
    
    # Render manifests for the shared cluster
    manifests = helm_render(
        values=cluster_integration_values, 
        release_name=cluster_name, 
        namespace=namespace
    )
    
    try:
        # Apply manifests
        for manifest in manifests:
            logger.info(f"Applying {manifest['kind']}: {manifest['metadata']['name']}")
            assert kubectl_apply_manifest(manifest, namespace), f"Failed to apply {manifest['kind']}"
        
        # Wait for deployment to be ready
        logger.info("Waiting for shared cluster to be ready...")
        assert wait_for_deployment_ready(cluster_name, namespace, timeout=900)
        
        # Wait for all pods to be ready
        logger.info("Waiting for all pods to be ready...")
        assert wait_for_pods_ready(
            f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=600
        )
        
        # Wait for user creation job (optional - might not be needed if manual user creation works)
        logger.info(f"Checking for user creation job completion {cluster_name}-create-falkordb-user on {namespace}...")
        try:
            wait_for_ops_request_completion(
                f"{cluster_name}-create-falkordb-user", namespace, timeout=180
            )
            logger.info("User creation OpsRequest completed successfully")
        except Exception as e:
            logger.warning(f"User creation OpsRequest not found or failed: {e}")
            logger.info("Proceeding without OpsRequest - manual user credentials will be used")
        
        # Get cluster pods for tests to use
        cluster_pods = get_cluster_pods(cluster_name, namespace)
        assert len(cluster_pods) >= 6, f"Expected at least 6 pods for cluster, got {len(cluster_pods)}"
        
        logger.info(f"Shared cluster {cluster_name} is ready with {len(cluster_pods)} pods")
        
        # Get the actual credentials from the first shard's secret
        # KubeBlocks creates a "default" system account for each shard
        import subprocess
        import base64
        
        # Extract shard name from first pod (e.g., "shared-cluster-shard-7xq-0" -> "shared-cluster-shard-7xq")
        first_pod = cluster_pods[0]
        shard_name = "-".join(first_pod.rsplit("-", 1)[0].split("-"))  # Remove pod ordinal
        secret_name = f"{shard_name}-account-default"
        
        logger.info(f"Retrieving credentials from secret: {secret_name}")
        
        # Get username
        username_cmd = f"kubectl get secret {secret_name} -n {namespace} -o jsonpath='{{.data.username}}'"
        username_b64 = subprocess.check_output(username_cmd, shell=True).decode().strip()
        username = base64.b64decode(username_b64).decode()
        
        # Get password
        password_cmd = f"kubectl get secret {secret_name} -n {namespace} -o jsonpath='{{.data.password}}'"
        password_b64 = subprocess.check_output(password_cmd, shell=True).decode().strip()
        password = base64.b64decode(password_b64).decode()
        
        logger.info(f"Using credentials - username: {username}")
        
        # Return cluster info for tests to use
        cluster_info = {
            "name": cluster_name,
            "namespace": namespace,
            "pods": cluster_pods,
            "username": username,
            "password": password
        }
        
        yield cluster_info
        
    finally:
        # Cleanup the shared cluster only if not skipping cleanup
        if not skip_cleanup:
            logger.info(f"Cleaning up shared cluster: {cluster_name}")
            cleanup_test_resources(cluster_name, namespace)
        else:
            logger.info(f"Skipping cleanup of shared cluster: {cluster_name}")


@pytest.fixture
def clean_graphs(shared_cluster):
    """
    Clean up any test graphs before and after each test.
    This ensures tests don't interfere with each other.
    """
    import subprocess
    
    cluster_info = shared_cluster
    
    def _clean_all_graphs():
        """Clean all test graphs from all pods using FLUSHALL for complete cleanup."""
        graph_names = ["clustergraph", "resilience_test", "scaling_test", "performance_test"]
        
        # In cluster mode, we need to use FLUSHALL to ensure complete cleanup across all shards
        # GRAPH.DELETE may not propagate correctly across all shards
        for pod in cluster_info["pods"][:3]:  # Clean from first 3 shard primaries
            try:
                clean_script = f"""#!/bin/bash
# Delete each graph explicitly first
redis-cli -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" GRAPH.DELETE clustergraph >/dev/null 2>&1 || true
redis-cli -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" GRAPH.DELETE resilience_test >/dev/null 2>&1 || true
redis-cli -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" GRAPH.DELETE scaling_test >/dev/null 2>&1 || true
redis-cli -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" GRAPH.DELETE performance_test >/dev/null 2>&1 || true

# Then do FLUSHALL to ensure complete cleanup
redis-cli -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" FLUSHALL >/dev/null 2>&1 || true
"""
                exec_cmd = [
                    "kubectl", "exec", pod, "-n", cluster_info["namespace"], 
                    "-c", "falkordb-cluster", "--", "sh", "-c", clean_script
                ]
                
                result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    logger.debug(f"Successfully cleaned graphs from {pod}")
                else:
                    logger.warning(f"Graph cleanup from {pod} had non-zero exit: {result.stderr}")
                
            except Exception as e:
                logger.warning(f"Error cleaning graphs from {pod}: {e}")
    
    # Clean before test
    _clean_all_graphs()
    
    yield cluster_info
    
    # Clean after test
    _clean_all_graphs()