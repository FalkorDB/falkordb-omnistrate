"""Replication integration test configuration."""

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
def replication_integration_values():
    """Return default values for replication integration tests."""
    return {
        "mode": "replication",
        "replicas": 3,
        # The chart's default `nodeAffinity` is Omnistrate-specific and will not match Kind nodes.
        # Integration tests run on Kind, so override it to allow scheduling.
        # NOTE: must be null (not `{}`) so Helm merge replaces the chart default map.
        "nodeAffinity": None,
        "sentinel": {
            "enabled": True,
            "replicas": 3
        },
        "instanceType": "low",
        "storage": 20,
        "falkordbUser": {
            "username": "testuser",
            "password": "testpass123"
        },
    }


@pytest.fixture
def replication_test_timeout():
    """Return timeout for replication tests (longer due to sentinel setup)."""
    return 600  # 10 minutes


@pytest.fixture(scope="module")
def shared_replication_cluster(helm_render, namespace, skip_cleanup, replication_integration_values, worker_id):
    """
    Create a shared replication cluster for all tests in this module.
    Uses worker_id to create unique cluster names when running in parallel with pytest-xdist.
    """
    # Create unique cluster name per worker
    if worker_id == "master":
        cluster_name = "repl-cluster"
    else:
        worker_num = worker_id.replace("gw", "")
        cluster_name = f"repl-clstr-{worker_num}"
    
    logger.info(f"Setting up shared replication cluster: {cluster_name}")
    
    # Render manifests
    manifests = helm_render(
        values=replication_integration_values, 
        release_name=cluster_name, 
        namespace=namespace
    )
    
    try:
        # Apply manifests
        for manifest in manifests:
            logger.info(f"Applying {manifest['kind']}: {manifest['metadata']['name']}")
            assert kubectl_apply_manifest(manifest, namespace), f"Failed to apply {manifest['kind']}"
        
        # Wait for deployment
        logger.info("Waiting for replication cluster to be ready...")
        assert wait_for_deployment_ready(cluster_name, namespace, timeout=600)
        
        # Wait for pods
        logger.info("Waiting for all pods to be ready...")
        assert wait_for_pods_ready(
            f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=600
        )
        
        # Get cluster pods
        all_pods = get_cluster_pods(cluster_name, namespace)
        assert len(all_pods) >= 5, f"Expected at least 5 pods, got {len(all_pods)}"
        
        # Separate falkordb pods from sentinel pods
        falkordb_pods = [pod for pod in all_pods if 'falkordb-sent' not in pod and 'sentinel' not in pod]
        sentinel_pods = [pod for pod in all_pods if 'falkordb-sent' in pod or 'sentinel' in pod]
        
        logger.info(f"Found {len(falkordb_pods)} falkordb pods and {len(sentinel_pods)} sentinel pods")
        
        # Get credentials from secret
        import subprocess
        import base64
        
        # For replication mode, get the secret from the falkordb component
        secret_name = f"{cluster_name}-falkordb-account-default"
        
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
        
        cluster_info = {
            "name": cluster_name,
            "namespace": namespace,
            "all_pods": all_pods,
            "falkordb_pods": falkordb_pods,
            "sentinel_pods": sentinel_pods,
            "username": username,
            "password": password
        }
        
        yield cluster_info
        
    finally:
        if not skip_cleanup:
            logger.info(f"Cleaning up shared replication cluster: {cluster_name}")
            cleanup_test_resources(cluster_name, namespace)
        else:
            logger.info(f"Skipping cleanup of shared replication cluster: {cluster_name}")