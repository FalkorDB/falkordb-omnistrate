"""
Utilities for Kubernetes interactions during integration testing.
"""

import time
import logging
import subprocess
import base64
from contextlib import contextmanager
from typing import Dict, Any, Optional, List, Tuple
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


def _ensure_k8s_config():
    """Ensure Kubernetes configuration is loaded."""
    try:
        config.load_kube_config()
    except Exception as e:
        logger.warning(f"Could not load kubeconfig: {e}, trying in-cluster config")
        try:
            config.load_incluster_config()
        except Exception as e2:
            logger.error(f"Could not load in-cluster config either: {e2}")
            raise


class KubernetesHelper:
    """Helper class for Kubernetes operations during testing."""
    
    def __init__(self, namespace: str = "default"):
        # Load Kubernetes configuration
        _ensure_k8s_config()
        
        self.namespace = namespace
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.custom_objects = client.CustomObjectsApi()
    
    def wait_for_pod_ready(self, pod_name: str, timeout: int = 300) -> bool:
        """
        Wait for a pod to be ready.
        
        Args:
            pod_name: Name of the pod to wait for
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if pod becomes ready, False if timeout
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                pod = self.core_v1.read_namespaced_pod(pod_name, self.namespace)
                if pod.status.phase == "Running":
                    for condition in pod.status.conditions or []:
                        if condition.type == "Ready" and condition.status == "True":
                            return True
                time.sleep(5)
            except ApiException:
                time.sleep(5)
        return False
    
    def wait_for_cluster_ready(self, cluster_name: str, timeout: int = 600) -> bool:
        """
        Wait for a KubeBlocks cluster to be ready.
        
        Args:
            cluster_name: Name of the cluster
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if cluster becomes ready, False if timeout
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                cluster = self.custom_objects.get_namespaced_custom_object(
                    group="apps.kubeblocks.io",
                    version="v1",
                    namespace=self.namespace,
                    plural="clusters",
                    name=cluster_name,
                )
                
                phase = cluster.get("status", {}).get("phase", "")
                if phase == "Running":
                    return True
                
                logger.info(f"Waiting for cluster {cluster_name}, current phase: {phase}")
                time.sleep(10)
            except ApiException as e:
                logger.warning(f"Waiting for cluster to be created: {e}")
                time.sleep(10)
        return False
    
    def get_cluster(self, cluster_name: str) -> Optional[Dict[str, Any]]:
        """
        Get cluster custom resource.
        
        Args:
            cluster_name: Name of the cluster
            
        Returns:
            Cluster resource dictionary or None if not found
        """
        try:
            return self.custom_objects.get_namespaced_custom_object(
                group="apps.kubeblocks.io",
                version="v1",
                namespace=self.namespace,
                plural="clusters",
                name=cluster_name,
            )
        except ApiException:
            return None
    
    def get_pods_by_selector(self, label_selector: str) -> List[client.V1Pod]:
        """
        Get pods by label selector.
        
        Args:
            label_selector: Kubernetes label selector
            
        Returns:
            List of pods matching the selector
        """
        try:
            result = self.core_v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=label_selector
            )
            return result.items
        except ApiException:
            return []
    
    def get_services_by_selector(self, label_selector: str) -> List[client.V1Service]:
        """
        Get services by label selector.
        
        Args:
            label_selector: Kubernetes label selector
            
        Returns:
            List of services matching the selector
        """
        try:
            result = self.core_v1.list_namespaced_service(
                namespace=self.namespace,
                label_selector=label_selector
            )
            return result.items
        except ApiException:
            return []
    
    def get_secret(self, secret_name: str) -> Optional[client.V1Secret]:
        """
        Get a secret by name.
        
        Args:
            secret_name: Name of the secret
            
        Returns:
            Secret object or None if not found
        """
        try:
            return self.core_v1.read_namespaced_secret(
                name=secret_name,
                namespace=self.namespace
            )
        except ApiException:
            return None
    
    def decode_secret_data(self, secret: client.V1Secret, key: str) -> Optional[str]:
        """
        Decode base64 secret data.
        
        Args:
            secret: Secret object
            key: Key to decode
            
        Returns:
            Decoded string value or None if key not found
        """
        if secret.data and key in secret.data:
            return base64.b64decode(secret.data[key]).decode('utf-8')
        return None
    
    def create_opsrequest(self, ops_spec: Dict[str, Any]) -> bool:
        """
        Create an OpsRequest custom resource.
        
        Args:
            ops_spec: OpsRequest specification
            
        Returns:
            True if created successfully, False otherwise
        """
        try:
            self.custom_objects.create_namespaced_custom_object(
                group="operations.kubeblocks.io",
                version="v1alpha1",
                namespace=self.namespace,
                plural="opsrequests",
                body=ops_spec
            )
            return True
        except ApiException as e:
            logger.error(f"Failed to create OpsRequest: {e}")
            return False
    
    def delete_pod(self, pod_name: str) -> bool:
        """
        Delete a pod.
        
        Args:
            pod_name: Name of the pod to delete
            
        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            self.core_v1.delete_namespaced_pod(
                name=pod_name,
                namespace=self.namespace
            )
            return True
        except ApiException as e:
            logger.error(f"Failed to delete pod {pod_name}: {e}")
            return False
    
    def patch_cluster(self, cluster_name: str, patch: Dict[str, Any]) -> bool:
        """
        Patch a cluster resource.
        
        Args:
            cluster_name: Name of the cluster
            patch: Patch data
            
        Returns:
            True if patched successfully, False otherwise
        """
        try:
            self.custom_objects.patch_namespaced_custom_object(
                group="apps.kubeblocks.io",
                version="v1",
                namespace=self.namespace,
                plural="clusters",
                name=cluster_name,
                body=patch
            )
            return True
        except ApiException as e:
            logger.error(f"Failed to patch cluster {cluster_name}: {e}")
            return False


def setup_port_forward(pod_name: str, namespace: str, local_port: int, remote_port: int = 6379) -> subprocess.Popen:
    """
    Set up port forwarding to a pod.
    
    Args:
        pod_name: Name of the pod
        namespace: Kubernetes namespace
        local_port: Local port to bind to
        remote_port: Remote port on the pod
        
    Returns:
        Subprocess.Popen object for the port-forward process
    """
    # Kill any existing port-forward processes to avoid conflicts
    try:
        subprocess.run(["pkill", "-f", f"port-forward.*{pod_name}"], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
    except Exception:
        pass
    
    # Start port-forward in background
    proc = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod_name}", f"{local_port}:{remote_port}", "-n", namespace],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for port-forward to establish
    time.sleep(3)
    
    # Check if port-forward process is still running
    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        error_msg = f"Port-forward failed to start. stdout: {stdout.decode()}, stderr: {stderr.decode()}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    
    return proc


def cleanup_port_forward(proc: subprocess.Popen, cluster_name: str = "") -> None:
    """
    Clean up port-forward process.
    
    Args:
        proc: Port-forward process
        cluster_name: Optional cluster name for additional cleanup
    """
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception as e:
            logger.warning(f"Error terminating port-forward: {e}")
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
    
    # Extra cleanup: kill any lingering port-forward processes
    if cluster_name:
        try:
            subprocess.run(["pkill", "-f", f"port-forward.*{cluster_name}"], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def cleanup_test_resources(cluster_name: str, namespace: str = "default") -> None:
    """
    Clean up test resources including cluster, services, PVCs, and other related resources.
    
    Args:
        cluster_name: Name of the cluster to clean up
        namespace: Kubernetes namespace
    """
    logger.info(f"Cleaning up test resources for cluster '{cluster_name}' in namespace '{namespace}'")
    
    helper = KubernetesHelper(namespace)
    
    try:
        # Delete the cluster custom resource
        logger.info(f"Deleting cluster '{cluster_name}'")
        helper.custom_objects.delete_namespaced_custom_object(
            group="apps.kubeblocks.io",
            version="v1",
            namespace=namespace,
            plural="clusters",
            name=cluster_name
        )
    except ApiException as e:
        if e.status != 404:  # Ignore not found errors
            logger.warning(f"Failed to delete cluster {cluster_name}: {e}")
    
    try:
        # Delete any OpsRequests related to the cluster
        ops_requests = helper.custom_objects.list_namespaced_custom_object(
            group="operations.kubeblocks.io",
            version="v1alpha1",
            namespace=namespace,
            plural="opsrequests",
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        
        for ops in ops_requests.get("items", []):
            ops_name = ops["metadata"]["name"]
            logger.info(f"Deleting OpsRequest '{ops_name}'")
            try:
                helper.custom_objects.delete_namespaced_custom_object(
                    group="operations.kubeblocks.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="opsrequests",
                    name=ops_name
                )
            except ApiException as e:
                if e.status != 404:
                    logger.warning(f"Failed to delete OpsRequest {ops_name}: {e}")
    
    except ApiException as e:
        logger.warning(f"Failed to list OpsRequests: {e}")
    
    try:
        # Delete services
        services = helper.core_v1.list_namespaced_service(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        
        for service in services.items:
            service_name = service.metadata.name
            logger.info(f"Deleting service '{service_name}'")
            try:
                helper.core_v1.delete_namespaced_service(
                    name=service_name,
                    namespace=namespace
                )
            except ApiException as e:
                if e.status != 404:
                    logger.warning(f"Failed to delete service {service_name}: {e}")
    
    except ApiException as e:
        logger.warning(f"Failed to list services: {e}")
    
    try:
        # Delete PVCs
        pvcs = helper.core_v1.list_namespaced_persistent_volume_claim(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        
        for pvc in pvcs.items:
            pvc_name = pvc.metadata.name
            logger.info(f"Deleting PVC '{pvc_name}'")
            try:
                helper.core_v1.delete_namespaced_persistent_volume_claim(
                    name=pvc_name,
                    namespace=namespace
                )
            except ApiException as e:
                if e.status != 404:
                    logger.warning(f"Failed to delete PVC {pvc_name}: {e}")
    
    except ApiException as e:
        logger.warning(f"Failed to list PVCs: {e}")
    
    try:
        # Delete secrets related to the cluster
        secrets = helper.core_v1.list_namespaced_secret(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        
        for secret in secrets.items:
            secret_name = secret.metadata.name
            logger.info(f"Deleting secret '{secret_name}'")
            try:
                helper.core_v1.delete_namespaced_secret(
                    name=secret_name,
                    namespace=namespace
                )
            except ApiException as e:
                if e.status != 404:
                    logger.warning(f"Failed to delete secret {secret_name}: {e}")
    
    except ApiException as e:
        logger.warning(f"Failed to list secrets: {e}")
    
    try:
        # Delete jobs related to the cluster
        jobs = helper.apps_v1.list_namespaced_job(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        
        for job in jobs.items:
            job_name = job.metadata.name
            logger.info(f"Deleting job '{job_name}'")
            try:
                helper.apps_v1.delete_namespaced_job(
                    name=job_name,
                    namespace=namespace,
                    propagation_policy="Background"
                )
            except ApiException as e:
                if e.status != 404:
                    logger.warning(f"Failed to delete job {job_name}: {e}")
    
    except AttributeError:
        # batch_v1 API not available in the helper class
        try:
            from kubernetes import client
            batch_v1 = client.BatchV1Api()
            jobs = batch_v1.list_namespaced_job(
                namespace=namespace,
                label_selector=f"app.kubernetes.io/instance={cluster_name}"
            )
            
            for job in jobs.items:
                job_name = job.metadata.name
                logger.info(f"Deleting job '{job_name}'")
                try:
                    batch_v1.delete_namespaced_job(
                        name=job_name,
                        namespace=namespace,
                        propagation_policy="Background"
                    )
                except ApiException as e:
                    if e.status != 404:
                        logger.warning(f"Failed to delete job {job_name}: {e}")
        
        except Exception as e:
            logger.warning(f"Failed to clean up jobs: {e}")
    
    except ApiException as e:
        logger.warning(f"Failed to list jobs: {e}")
    
    # Wait a bit for resources to be deleted
    logger.info("Waiting for resources to be cleaned up...")
    time.sleep(5)
    
    logger.info(f"Cleanup completed for cluster '{cluster_name}'")


def wait_for_deployment_ready(cluster_name: str, namespace: str, timeout: int = 600) -> bool:
    """
    Wait for a FalkorDB cluster deployment to be ready.
    
    Args:
        cluster_name: Name of the cluster
        namespace: Kubernetes namespace
        timeout: Maximum time to wait in seconds
        
    Returns:
        True if deployment becomes ready, False if timeout
    """
    helper = KubernetesHelper(namespace)
    return helper.wait_for_cluster_ready(cluster_name, timeout)


def wait_for_pods_ready(label_selector: str, namespace: str, timeout: int = 300) -> bool:
    """
    Wait for pods matching a label selector to be ready.
    
    Args:
        label_selector: Kubernetes label selector (e.g., "app.kubernetes.io/instance=my-cluster")
        namespace: Kubernetes namespace
        timeout: Maximum time to wait in seconds
        
    Returns:
        True if all pods become ready, False if timeout
    """
    _ensure_k8s_config()
    core_v1 = client.CoreV1Api()
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            pods = core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector
            )
            
            if not pods.items:
                logger.info(f"No pods found with selector '{label_selector}', continuing to wait...")
                time.sleep(10)
                continue
            
            all_ready = True
            for pod in pods.items:
                if pod.status.phase != "Running":
                    all_ready = False
                    break
                
                # Check if pod is ready
                ready = False
                for condition in pod.status.conditions or []:
                    if condition.type == "Ready" and condition.status == "True":
                        ready = True
                        break
                
                if not ready:
                    all_ready = False
                    break
            
            if all_ready:
                logger.info(f"All {len(pods.items)} pods are ready for selector '{label_selector}'")
                return True
            
            logger.info(f"Waiting for {len(pods.items)} pods to be ready...")
            time.sleep(10)
            
        except ApiException as e:
            logger.warning(f"Error checking pod status: {e}")
            time.sleep(10)
    
    logger.error(f"Timeout waiting for pods with selector '{label_selector}' to be ready")
    return False

def wait_for_job_completion(job_name: str, namespace: str, timeout: int = 600) -> bool:
    """
    Wait for a Kubernetes Job to complete.
    
    Args:
        job_name: Name of the Job
        namespace: Kubernetes namespace
        timeout: Maximum time to wait in seconds
    Returns:
        True if Job completes successfully, False if timeout or failure
    """
    _ensure_k8s_config()
    batch_v1 = client.BatchV1Api()
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            job = batch_v1.read_namespaced_job(name=job_name, namespace=namespace)
            if job.status.succeeded and job.status.succeeded >= 1:
                logger.info(f"Job '{job_name}' completed successfully")
                return True
            if job.status.failed and job.status.failed >= 1:
                logger.error(f"Job '{job_name}' failed")
                return False

            logger.info(f"Waiting for Job '{job_name}' to complete...")
            time.sleep(10)

        except ApiException as e:
            logger.warning(f"Error checking Job status: {e}")
            time.sleep(10)

    logger.error(f"Timeout waiting for Job '{label_selector}' to complete")
    return False

def wait_for_ops_request_completion(ops_name: str, namespace: str, timeout: int = 600) -> bool:
    """
    Wait for an OpsRequest to complete.
    
    Args:
        ops_name: Name of the OpsRequest
        namespace: Kubernetes namespace
        timeout: Maximum time to wait in seconds

    Returns:
        True if OpsRequest completes successfully, False if timeout or failure
    """
    _ensure_k8s_config()
    custom_objects = client.CustomObjectsApi()
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            ops = custom_objects.get_namespaced_custom_object(
                group="operations.kubeblocks.io",
                version="v1alpha1",
                namespace=namespace,
                name=ops_name,
                plural="opsrequests"
            )
            if ops["status"]["phase"] == "Succeed":
                logger.info(f"OpsRequest '{ops_name}' completed successfully")
                return True
            if ops["status"]["phase"] == "Failed":
                logger.error(f"OpsRequest '{ops_name}' failed")
                return False

            logger.info(f"Waiting for OpsRequest '{ops_name}' to complete...")
            time.sleep(10)

        except ApiException as e:
            logger.warning(f"Error checking OpsRequest status: {e}")
            time.sleep(10)

    logger.error(f"Timeout waiting for OpsRequest '{ops_name}' to complete")
    return False


@contextmanager
def port_forward_pod(pod_name: str, namespace: str, remote_port: int, local_port: Optional[int] = None):
    """
    Port forward to a pod using kubectl port-forward.
    
    Args:
        pod_name: Name of the pod
        namespace: Kubernetes namespace
        remote_port: Port on the pod to forward to
        local_port: Local port to bind to (auto-assigned if None)
        
    Yields:
        The local port number that was bound
    """
    import random
    
    if local_port is None:
        # Find an available port
        local_port = random.randint(30000, 40000)
        while _is_port_in_use(local_port):
            local_port = random.randint(30000, 40000)
    
    cmd = [
        "kubectl", "port-forward",
        f"pod/{pod_name}",
        f"{local_port}:{remote_port}",
        "-n", namespace
    ]
    
    logger.info(f"Starting port-forward: {' '.join(cmd)}")
    
    try:
        # Start the port-forward process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait a moment for the port-forward to establish
        time.sleep(2)
        
        # Check if process is still running
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"Port-forward failed: {stderr}")
        
        logger.info(f"Port-forward established on localhost:{local_port}")
        yield local_port
        
    finally:
        # Clean up the port-forward process
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        except Exception as e:
            logger.warning(f"Error cleaning up port-forward: {e}")
        
        logger.info(f"Port-forward to {pod_name} cleaned up")


def _is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            return False
        except OSError:
            return True


def get_pod_logs(pod_name: str, namespace: str, tail_lines: int = 100) -> str:
    """
    Get logs from a pod.
    
    Args:
        pod_name: Name of the pod
        namespace: Kubernetes namespace
        tail_lines: Number of lines to retrieve from the end
        
    Returns:
        Pod logs as a string
    """
    _ensure_k8s_config()
    core_v1 = client.CoreV1Api()
    try:
        logs = core_v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=tail_lines
        )
        return logs
    except ApiException as e:
        logger.error(f"Failed to get logs for pod {pod_name}: {e}")
        return ""


def kubectl_apply_manifest(manifest: dict, namespace: str) -> bool:
    """
    Apply a Kubernetes manifest using kubectl.
    
    Args:
        manifest: Kubernetes manifest as a dictionary
        namespace: Kubernetes namespace
        
    Returns:
        True if successful, False otherwise
    """
    import yaml
    import tempfile
    import os
    
    try:
        # Write manifest to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(manifest, f)
            temp_file = f.name
        
        # Apply using kubectl
        cmd = ['kubectl', 'apply', '-f', temp_file, '-n', namespace]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            logger.info(f"Successfully applied {manifest.get('kind', 'unknown')} {manifest.get('metadata', {}).get('name', 'unknown')}")
            return True
        else:
            logger.error(f"Failed to apply manifest: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"Error applying manifest: {e}")
        return False
    finally:
        # Clean up temp file
        try:
            if 'temp_file' in locals():
                os.unlink(temp_file)
        except:
            pass


def get_cluster_pods(cluster_name: str, namespace: str) -> List[str]:
    """
    Get list of pod names for a cluster.
    
    Args:
        cluster_name: Name of the cluster
        namespace: Kubernetes namespace
        
    Returns:
        List of pod names
    """
    core_v1 = client.CoreV1Api()
    try:
        pods = core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"app.kubernetes.io/instance={cluster_name}"
        )
        return [pod.metadata.name for pod in pods.items]
    except ApiException as e:
        logger.error(f"Failed to get pods for cluster {cluster_name}: {e}")
        return []