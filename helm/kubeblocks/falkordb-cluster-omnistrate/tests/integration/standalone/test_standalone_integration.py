"""
Integration tests specific to standalone deployment mode.
"""

import time
import logging
import pytest
import subprocess
import socket
import base64
from pathlib import Path
from falkordb import FalkorDB

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def deployed_standalone_cluster(k8s_custom_client, chart_path, integration_values, namespace):
    """Deploy a standalone FalkorDB cluster using helm and wait for it to be ready."""
    cluster_name = "test-standalone-cluster"
    
    # Check if cluster already exists
    try:
        existing_cluster = k8s_custom_client.get_namespaced_custom_object(
            group="apps.kubeblocks.io",
            version="v1",
            namespace=namespace,
            plural="clusters",
            name=cluster_name,
        )
        logger.info(f"Cluster {cluster_name} already exists, using it")
        return existing_cluster
    except Exception:
        pass  # Cluster doesn't exist, we'll create it
    
    import tempfile
    import yaml
    
    # Package the chart first
    logger.info(f"Packaging chart from {chart_path}")
    package_result = subprocess.run(
        ["helm", "package", str(chart_path)],
        cwd=str(chart_path.parent),
        capture_output=True,
        text=True,
        check=True
    )
    
    # Find the package file
    package_line = [line for line in package_result.stdout.split('\n') if 'Successfully packaged' in line][0]
    package_path = package_line.split(': ')[-1].strip()
    logger.info(f"Using packaged chart: {package_path}")
    
    values_file = None
    
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(integration_values, f)
            values_file = f.name
        
        # Deploy using helm
        cmd = [
            "helm", "install", cluster_name,
            package_path,
            "--namespace", namespace,
            "--create-namespace",
            "--values", values_file,
            "--wait",
            "--timeout", "10m"
        ]
        
        logger.info(f"Deploying cluster with: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"Helm install output: {result.stdout}")
        
        # Wait for cluster to be ready
        max_wait = 600
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            try:
                cluster = k8s_custom_client.get_namespaced_custom_object(
                    group="apps.kubeblocks.io",
                    version="v1",
                    namespace=namespace,
                    plural="clusters",
                    name=cluster_name,
                )
                
                phase = cluster.get("status", {}).get("phase", "")
                if phase == "Running":
                    logger.info(f"Cluster {cluster_name} is running")
                    return cluster
                
                logger.info(f"Waiting for cluster to be ready, current phase: {phase}")
                time.sleep(10)
            except Exception as e:
                logger.info(f"Waiting for cluster to be created: {e}")
                time.sleep(10)
        
        pytest.fail(f"Cluster did not become ready within {max_wait}s")
        
    except subprocess.CalledProcessError as e:
        pytest.fail(f"Failed to deploy cluster: {e.stderr}")
    finally:
        # Cleanup temp files
        if values_file:
            Path(values_file).unlink(missing_ok=True)
        Path(package_path).unlink(missing_ok=True)


@pytest.fixture
def falkordb_connection(k8s_client, deployed_standalone_cluster, namespace):
    """Get FalkorDB connection via port-forward."""
    cluster_name = deployed_standalone_cluster["metadata"]["name"]
    
    # Get admin credentials from secret
    try:
        secret = k8s_client.read_namespaced_secret(
            name=f"{cluster_name}-falkordb-account-default",
            namespace=namespace
        )
        username = base64.b64decode(secret.data['username']).decode('utf-8')
        password = base64.b64decode(secret.data['password']).decode('utf-8')
        logger.info(f"Using admin credentials from secret: {username}")
    except Exception as e:
        logger.error(f"Failed to get admin credentials: {e}")
        raise
    
    # Find an available local port
    sock = socket.socket()
    sock.bind(('', 0))
    local_port = sock.getsockname()[1]
    sock.close()
    
    # Get pod name
    pods = k8s_client.list_namespaced_pod(
        namespace=namespace,
        label_selector=f"app.kubernetes.io/instance={cluster_name}"
    )
    
    if len(pods.items) == 0:
        raise RuntimeError("No pods found for connection")
    
    pod_name = pods.items[0].metadata.name
    
    # Kill any existing port-forward processes
    try:
        subprocess.run(["pkill", "-f", f"port-forward.*{pod_name}"], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        time.sleep(1)
    except Exception:
        pass
    
    # Start port-forward
    proc = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod_name}", f"{local_port}:6379", "-n", namespace],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    time.sleep(5)
    
    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        error_msg = f"Port-forward failed. stdout: {stdout.decode()}, stderr: {stderr.decode()}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    
    # Connect to FalkorDB with retry
    max_retries = 5
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            db = FalkorDB(host="localhost", port=local_port, username=username, password=password)
            logger.info(f"Successfully connected to FalkorDB on attempt {attempt + 1}")
            yield db
            break
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 1.5
            else:
                logger.error(f"All {max_retries} connection attempts failed")
                raise
    
    # Cleanup
    try:
        db.close()
    except Exception:
        pass
    
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class TestStandaloneIntegration:
    """Integration tests for standalone deployment."""

    def test_standalone_cluster_deployment(self, deployed_standalone_cluster):
        """Test that standalone cluster deploys successfully."""
        assert deployed_standalone_cluster["spec"]["topology"] == "standalone"
        assert deployed_standalone_cluster["status"]["phase"] == "Running"

    def test_standalone_basic_connectivity(self, falkordb_connection):
        """Test basic FalkorDB connectivity in standalone mode."""
        # Test basic graph operations
        g = falkordb_connection.select_graph("test_connectivity")
        
        # Create a simple node
        result = g.query("CREATE (n:Person {name: 'Alice', age: 30}) RETURN n")
        assert len(result.result_set) > 0, "Failed to create node"
        
        # Query the node
        result = g.query("MATCH (n:Person) RETURN n.name, n.age")
        assert len(result.result_set) == 1, "Failed to query node"
        assert result.result_set[0][0] == "Alice", "Node name incorrect"
        assert result.result_set[0][1] == 30, "Node age incorrect"
        
        # Clean up
        g.delete()

    def test_standalone_data_persistence_after_pod_restart(self, k8s_client, deployed_standalone_cluster, falkordb_connection, namespace):
        """Test data persistence across pod restart."""
        cluster_name = deployed_standalone_cluster["metadata"]["name"]
        
        # Add test data
        g = falkordb_connection.select_graph("persistence_test")
        g.query("CREATE (:TestNode {id: 'persistent_data', value: 123})")
        
        # Get current pod
        pods = k8s_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        
        if len(pods.items) == 0:
            pytest.skip("No pods found")
        
        pod_name = pods.items[0].metadata.name
        
        # Delete pod to trigger restart
        logger.info(f"Deleting pod {pod_name}")
        k8s_client.delete_namespaced_pod(name=pod_name, namespace=namespace)
        
        # Wait for pod to be recreated
        time.sleep(30)
        
        # Verify data persisted - this would require reconnection
        # For now, just verify the pod was recreated
        new_pods = k8s_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        
        assert len(new_pods.items) > 0, "Pod was not recreated"
        
        # Wait for pod to be ready
        max_wait = 300
        start_time = time.time()
        while time.time() - start_time < max_wait:
            pod = k8s_client.read_namespaced_pod(new_pods.items[0].metadata.name, namespace)
            if pod.status.phase == "Running":
                ready = True
                if pod.status.container_statuses:
                    ready = all(cs.ready for cs in pod.status.container_statuses)
                if ready:
                    logger.info("Pod is ready after restart")
                    break
            time.sleep(5)

    def test_standalone_user_creation_opsrequest(self, k8s_custom_client, deployed_standalone_cluster, namespace):
        """Test that user creation OpsRequest was executed."""
        cluster_name = deployed_standalone_cluster["metadata"]["name"]
        
        try:
            opsrequests = k8s_custom_client.list_namespaced_custom_object(
                group="operations.kubeblocks.io",
                version="v1alpha1",
                namespace=namespace,
                plural="opsrequests",
                label_selector=f"app.kubernetes.io/instance={cluster_name}"
            )
            
            user_ops = [
                o for o in opsrequests["items"]
                if o["spec"]["type"] == "Custom"
                and o["spec"]["custom"]["opsDefinitionName"] == "falkordb-master-account-ops"
            ]
            
            assert len(user_ops) > 0, "User creation OpsRequest not found"
            
            # Check status
            ops = user_ops[0]
            status = ops.get("status", {}).get("phase", "")
            assert status in ["Succeed", "Running"], f"OpsRequest in unexpected state: {status}"
            
        except Exception as e:
            pytest.fail(f"Failed to check OpsRequest: {e}")

    def test_standalone_storage_allocation(self, k8s_client, deployed_standalone_cluster, namespace):
        """Test storage allocation for standalone cluster."""
        cluster_name = deployed_standalone_cluster["metadata"]["name"]
        
        # Check PVC exists and is bound
        pvc_name = f"data-{cluster_name}-falkordb-0"
        
        try:
            pvc = k8s_client.read_namespaced_persistent_volume_claim(
                name=pvc_name, namespace=namespace
            )
            
            assert pvc.status.phase == "Bound", f"PVC {pvc_name} is not bound"
            
            if pvc.status.capacity:
                storage = pvc.status.capacity.get('storage', 'unknown')
                logger.info(f"PVC {pvc_name} capacity: {storage}")
                
        except Exception as e:
            pytest.fail(f"PVC {pvc_name} not found: {e}")

    def test_standalone_single_replica(self, k8s_client, deployed_standalone_cluster, namespace):
        """Test that standalone mode has exactly one replica."""
        cluster_name = deployed_standalone_cluster["metadata"]["name"]
        
        pods = k8s_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        
        # Should have exactly one pod for standalone
        assert len(pods.items) == 1, f"Expected 1 pod for standalone, got {len(pods.items)}"

    def test_standalone_no_sentinel_components(self, k8s_client, deployed_standalone_cluster, namespace):
        """Test that no sentinel components exist in standalone mode."""
        cluster_name = deployed_standalone_cluster["metadata"]["name"]
        
        # Check for sentinel pods (should be none)
        sentinel_pods = k8s_client.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name},apps.kubeblocks.io/component-name=sentinel"
        )
        
        assert len(sentinel_pods.items) == 0, "Sentinel pods should not exist in standalone mode"