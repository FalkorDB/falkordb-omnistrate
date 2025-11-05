"""
Common integration tests that apply to all deployment modes.
"""

import pytest
import logging
from ...utils.kubernetes import KubernetesHelper

logger = logging.getLogger(__name__)


class TestCommonIntegration:
    """Common integration tests for all deployment modes."""

    def test_kubeblocks_installed(self, k8s_client):
        """Verify KubeBlocks is installed and running."""
        pods = k8s_client.list_namespaced_pod(namespace="kb-system")
        kb_pods = [p for p in pods.items if "kubeblocks" in p.metadata.name]
        assert len(kb_pods) > 0, "KubeBlocks pods not found"
        
        for pod in kb_pods:
            assert pod.status.phase == "Running", f"Pod {pod.metadata.name} not running"

    def test_falkordb_addon_installed(self, k8s_custom_client):
        """Verify FalkorDB ClusterDefinition exists."""
        try:
            clusterdef = k8s_custom_client.get_cluster_custom_object(
                group="apps.kubeblocks.io",
                version="v1",
                plural="clusterdefinitions",
                name="falkordb",
            )
            assert clusterdef is not None, "FalkorDB ClusterDefinition not found"
            assert clusterdef["metadata"]["name"] == "falkordb"
        except Exception as e:
            pytest.fail(f"FalkorDB ClusterDefinition not found: {e}")

    def test_cluster_basic_connectivity(self, k8s_helper, cluster_name):
        """Test basic cluster connectivity and health."""
        # Wait for cluster to be ready
        assert k8s_helper.wait_for_cluster_ready(cluster_name, timeout=600), \
            f"Cluster {cluster_name} did not become ready"
        
        # Get cluster
        cluster = k8s_helper.get_cluster(cluster_name)
        assert cluster is not None, f"Cluster {cluster_name} not found"
        
        phase = cluster.get("status", {}).get("phase", "")
        assert phase == "Running", f"Expected cluster phase 'Running', got '{phase}'"

    def test_pod_and_service_health(self, k8s_helper, cluster_name):
        """Test pod and service health."""
        # Get pods
        pods = k8s_helper.get_pods_by_selector(f"app.kubernetes.io/instance={cluster_name}")
        assert len(pods) > 0, "No FalkorDB pods found"
        
        # Check pod health
        for pod in pods:
            assert pod.status.phase == "Running", f"Pod {pod.metadata.name} not running"
            
            # Check container readiness
            if pod.status.container_statuses:
                for container_status in pod.status.container_statuses:
                    assert container_status.ready, f"Container in pod {pod.metadata.name} not ready"
        
        # Get services
        services = k8s_helper.get_services_by_selector("apps.kubeblocks.io/component-name=falkordb")
        assert len(services) > 0, "No FalkorDB services found"
        
        # Check service has endpoints
        service = services[0]
        assert service.spec.ports[0].port == 6379, "FalkorDB port 6379 not found in service"

    def test_resource_allocation(self, k8s_helper, cluster_name):
        """Test that pods have correct resource allocation."""
        pods = k8s_helper.get_pods_by_selector(f"app.kubernetes.io/instance={cluster_name}")
        assert len(pods) > 0, "No pods found"
        
        pod = pods[0]
        container = pod.spec.containers[0]
        
        # Verify resources are set
        assert container.resources.limits is not None, "No resource limits set"
        assert container.resources.requests is not None, "No resource requests set"
        assert "cpu" in container.resources.limits, "CPU limit not set"
        assert "memory" in container.resources.limits, "Memory limit not set"

    def test_persistent_volume_claims(self, k8s_client, cluster_name, namespace):
        """Test that PVCs are created and bound."""
        # Look for PVCs related to the cluster
        pvcs = k8s_client.list_namespaced_persistent_volume_claim(namespace=namespace)
        
        cluster_pvcs = [
            pvc for pvc in pvcs.items 
            if cluster_name in pvc.metadata.name
        ]
        
        assert len(cluster_pvcs) > 0, f"No PVCs found for cluster {cluster_name}"
        
        for pvc in cluster_pvcs:
            assert pvc.status.phase == "Bound", f"PVC {pvc.metadata.name} is not bound (status: {pvc.status.phase})"
            
            # Check capacity
            if pvc.status.capacity:
                storage = pvc.status.capacity.get('storage', 'unknown')
                logger.info(f"PVC {pvc.metadata.name}: capacity={storage}")

    def test_environment_variables_set(self, k8s_helper, cluster_name):
        """Test that required environment variables are set in pods."""
        pods = k8s_helper.get_pods_by_selector(f"app.kubernetes.io/instance={cluster_name}")
        assert len(pods) > 0, "No pods found"
        
        pod = pods[0]
        container = pod.spec.containers[0]
        
        # Extract environment variables
        env_vars = {e.name: e.value for e in container.env or []}
        
        # Check for common environment variables (may vary based on configuration)
        # At minimum, there should be some environment configuration
        assert len(env_vars) > 0, "No environment variables found in container"
        
        # Log environment variables for debugging
        for name, value in env_vars.items():
            logger.debug(f"Environment variable: {name}={value}")

    def test_labels_and_annotations(self, k8s_helper, cluster_name):
        """Test that pods and services have correct labels and annotations."""
        pods = k8s_helper.get_pods_by_selector(f"app.kubernetes.io/instance={cluster_name}")
        assert len(pods) > 0, "No pods found"
        
        pod = pods[0]
        labels = pod.metadata.labels or {}
        
        # Check required labels
        assert "app.kubernetes.io/instance" in labels, "Instance label not found"
        assert labels["app.kubernetes.io/instance"] == cluster_name, "Instance label value incorrect"
        
        # Check services
        services = k8s_helper.get_services_by_selector("apps.kubeblocks.io/component-name=falkordb")
        assert len(services) > 0, "No services found"
        
        service = services[0]
        service_labels = service.metadata.labels or {}
        assert len(service_labels) > 0, "No labels found on service"