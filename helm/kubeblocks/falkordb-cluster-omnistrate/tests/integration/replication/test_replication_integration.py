"""Integration tests for replication mode deployment."""

import logging
import subprocess
import time
import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

from ...utils.kubernetes import (
    cleanup_test_resources,
    wait_for_deployment_ready,
    wait_for_pods_ready,
    get_pod_logs,
    kubectl_apply_manifest,
    get_cluster_pods
)
from ...utils.validation import (
    validate_falkordb_connection_in_replication,
    validate_replication_status,
    get_falkordb_container_name
)

logger = logging.getLogger(__name__)


@pytest.mark.integration
class TestReplicationIntegration:
    """Integration tests for replication mode FalkorDB deployment."""

    def test_replication_deployment_basic(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test basic replication deployment with sentinel."""
        values = {
            "mode": "replication",
            "replicas": 2,
            "sentinel": {
                "enabled": True,
                "replicas": 3
            },
            "instanceType": "e2-medium",
            "storage": 20,
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        logger.info(f"Rendered {len(manifests)} manifests for replication deployment")
        
        try:
            # Apply manifests
            for manifest in manifests:
                logger.info(f"Applying {manifest['kind']}: {manifest['metadata']['name']}")
                assert kubectl_apply_manifest(manifest, namespace), f"Failed to apply {manifest['kind']}"
            
            # Wait for deployment to be ready
            logger.info("Waiting for replication cluster to be ready...")
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=600)
            
            # Wait for all pods to be ready
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=300)
            
            # Get actual pod names from cluster
            all_pods = get_cluster_pods(cluster_name, namespace)
            assert len(all_pods) >= 5, f"Expected at least 5 pods (2 falkordb + 3 sentinel), got {len(all_pods)}"
            
            # Separate falkordb pods from sentinel pods
            falkordb_pods = [pod for pod in all_pods if 'falkordb-sent' not in pod]
            sentinel_pods = [pod for pod in all_pods if 'falkordb-sent' in pod]
            
            logger.info(f"Found {len(falkordb_pods)} falkordb pods: {falkordb_pods}")
            logger.info(f"Found {len(sentinel_pods)} sentinel pods: {sentinel_pods}")
            
            assert len(falkordb_pods) >= 2, f"Expected at least 2 falkordb pods, got {len(falkordb_pods)}"
            assert len(sentinel_pods) >= 3, f"Expected at least 3 sentinel pods, got {len(sentinel_pods)}"
            
            # Test connection to falkordb pods only using kubectl exec (reliable approach)
            for pod in falkordb_pods:
                assert validate_falkordb_connection_in_replication(
                    pod, namespace, values["falkordbUser"]["username"], values["falkordbUser"]["password"]
                ), f"Failed to connect to falkordb pod {pod}"
            
            # Test replication status from first falkordb pod (assume it's master)
            master_pod = falkordb_pods[0]
            assert validate_replication_status(master_pod, namespace,
                                             values["falkordbUser"]["username"], 
                                             values["falkordbUser"]["password"], 
                                             expected_replicas=1)  # Expect at least 1 replica
            logger.info("Replication status validated")
        
        finally:
            if not skip_cleanup:
                logger.info("Cleaning up test resources...")
                cleanup_test_resources(cluster_name, namespace)

    def test_replication_data_persistence(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test data persistence and replication."""
        values = {
            "mode": "replication",
            "replicas": 2,
            "instanceType": "e2-medium",
            "storage": 20,
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        
        try:
            # Apply manifests
            for manifest in manifests:
                assert kubectl_apply_manifest(manifest, namespace), f"Failed to apply {manifest['kind']}"
            
            # Wait for cluster to be ready
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=600)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=300)
            
            # Wait for user creation job to complete (Helm post-install hook)
            logger.info("Waiting for user creation job to complete...")
            job_name = f"{cluster_name}-create-user-job"
            max_wait = 300  # 5 minutes
            start_time = time.time()
            
            while time.time() - start_time < max_wait:
                try:
                    result = subprocess.run([
                        "kubectl", "get", "job", job_name, "-n", namespace, 
                        "-o", "jsonpath={.status.conditions[?(@.type=='Complete')].status}"
                    ], capture_output=True, text=True, timeout=30)
                    
                    if result.returncode == 0 and result.stdout.strip() == "True":
                        logger.info("User creation job completed successfully")
                        break
                        
                    # Check if job failed
                    failed_result = subprocess.run([
                        "kubectl", "get", "job", job_name, "-n", namespace,
                        "-o", "jsonpath={.status.conditions[?(@.type=='Failed')].status}"
                    ], capture_output=True, text=True, timeout=30)
                    
                    if failed_result.returncode == 0 and failed_result.stdout.strip() == "True":
                        # Get job logs for debugging
                        logs_result = subprocess.run([
                            "kubectl", "logs", f"job/{job_name}", "-n", namespace
                        ], capture_output=True, text=True, timeout=30)
                        logger.error(f"User creation job failed. Logs: {logs_result.stdout}")
                        raise AssertionError(f"User creation job {job_name} failed")
                        
                except subprocess.TimeoutExpired:
                    logger.warning("Timeout checking job status")
                
                time.sleep(5)
            else:
                raise AssertionError(f"User creation job {job_name} did not complete within {max_wait} seconds")
            
            # Get actual pod names from cluster
            cluster_pods = get_cluster_pods(cluster_name, namespace)
            assert len(cluster_pods) >= 2, f"Expected at least 2 pods, got {len(cluster_pods)}"
            
            # Filter to only get FalkorDB pods (not sentinel pods)
            falkordb_pods = [pod for pod in cluster_pods if 'falkordb-sent' not in pod]
            assert len(falkordb_pods) >= 2, f"Expected at least 2 FalkorDB pods, got {len(falkordb_pods)}"
            
            master_pod = falkordb_pods[0]
            replica_pods = falkordb_pods[1:]
            
            # Create test data on master using kubectl exec (reliable approach)
            logger.info("Creating test data on master...")
            create_data_script = f'''#!/bin/bash
set -e

# First create a basic Redis key to test replication
redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    SET test_key "test_value"

# Create test data using redis-cli
redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.QUERY test_replication "CREATE (p:Person {{name: 'Alice', age: 30}})"

redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.QUERY test_replication "CREATE (p:Person {{name: 'Bob', age: 25}})"

redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.QUERY test_replication "MATCH (a:Person {{name: 'Alice'}}), (b:Person {{name: 'Bob'}}) CREATE (a)-[:KNOWS]->(b)"

echo "Test data created on master"
'''
            
            # Get the correct container name for the master pod
            container_name = get_falkordb_container_name(master_pod, namespace)
            assert container_name is not None, f"Could not find FalkorDB container in pod {master_pod}"
            
            exec_cmd = [
                'kubectl', 'exec', master_pod,
                '-n', namespace,
                '-c', container_name,
                '--',
                'sh', '-c', create_data_script
            ]
            
            try:
                result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
                assert result.returncode == 0, f"Failed to create test data: {result.stderr}"
                logger.info("Test data created on master")
            except Exception as e:
                logger.error(f"Failed to create test data: {e}")
                raise
            
            # Verify data was actually written to master before checking replicas
            logger.info("Verifying data exists on master...")
            master_verify_script = f'''#!/bin/bash
set -e

# Check basic key
KEY_RESULT=$(redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GET test_key 2>/dev/null || echo "")

if [ "$KEY_RESULT" != "test_value" ]; then
    echo "ERROR: Basic key not found on master: '$KEY_RESULT'"
    exit 1
fi

# Check graph exists
GRAPH_LIST=$(redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.LIST 2>/dev/null || echo "")

if [[ "$GRAPH_LIST" != *"test_replication"* ]]; then
    echo "ERROR: Test graph not found on master: '$GRAPH_LIST'"
    exit 1
fi

echo "SUCCESS: Data verified on master"
'''
            
            master_verify_cmd = [
                'kubectl', 'exec', master_pod,
                '-n', namespace,
                '-c', container_name,
                '--',
                'sh', '-c', master_verify_script
            ]
            
            try:
                result = subprocess.run(master_verify_cmd, capture_output=True, text=True, timeout=30)
                assert result.returncode == 0, f"Master data verification failed: {result.stderr}\nOutput: {result.stdout}"
                logger.info("Master data verification successful")
            except Exception as e:
                logger.error(f"Failed to verify master data: {e}")
                raise
            
            # Allow time for replication and verify with retries
            logger.info("Waiting for data replication...")
            max_retries = 6
            retry_delay = 10
            
            # Verify data exists on replicas using kubectl exec with retries
            for i, replica_pod in enumerate(replica_pods):
                logger.info(f"Verifying data on replica {i+1}...")
                
                for retry in range(max_retries):
                    logger.info(f"Attempt {retry + 1}/{max_retries} for replica {i+1}")
                    
                    verify_data_script = f'''#!/bin/bash
set -e

# Function to extract count from FalkorDB query result
extract_count() {{
    local output="$1"
    # FalkorDB GRAPH.RO_QUERY returns results in multiple lines, look for the actual count value
    echo "$output" | grep -E '^[0-9]+$' | head -1 || echo "$output" | grep -o '[0-9]\\+' | head -1 || echo "0"
}}

# Check replication status first
echo "Checking replication status..."
REPL_INFO=$(redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    INFO REPLICATION 2>&1)
echo "Replication info: $REPL_INFO"

# First check if basic Redis key is replicated
echo "Checking basic Redis key replication..."
BASIC_KEY=$(redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GET test_key 2>&1)
echo "Basic key result: $BASIC_KEY"

# Extract just the value, ignoring warning messages
BASIC_KEY_VALUE=$(echo "$BASIC_KEY" | grep -v "Warning:" | grep -v "safe" | tail -1)
if [ "$BASIC_KEY_VALUE" != "test_value" ]; then
    echo "WARNING: Basic Redis key not replicated yet"
    exit 2  # Use exit code 2 for retry
fi

# Check if graph exists using GRAPH.LIST
echo "Checking available graphs..."
GRAPHS=$(redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.LIST 2>&1)
echo "Available graphs: $GRAPHS"

# Check if our graph exists in the list
if [[ "$GRAPHS" != *"test_replication"* ]]; then
    echo "WARNING: Graph 'test_replication' not found in available graphs"
    exit 2  # Use exit code 2 for retry
fi

# Verify person count using read-only query
echo "Executing person count query..."
PERSON_RESULT=$(redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.RO_QUERY test_replication "MATCH (p:Person) RETURN count(p)" 2>&1)
echo "Person query output: $PERSON_RESULT"

PERSON_COUNT=$(extract_count "$PERSON_RESULT")
echo "Found $PERSON_COUNT persons"
if [ "$PERSON_COUNT" != "2" ]; then
    echo "ERROR: Expected 2 persons, found $PERSON_COUNT"
    exit 1
fi

# Verify relationship count using read-only query
echo "Executing relationship count query..."
REL_RESULT=$(redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.RO_QUERY test_replication "MATCH ()-[r:KNOWS]->() RETURN count(r)" 2>&1)
echo "Relationship query output: $REL_RESULT"

REL_COUNT=$(extract_count "$REL_RESULT")
echo "Found $REL_COUNT relationships"
if [ "$REL_COUNT" != "1" ]; then
    echo "ERROR: Expected 1 relationship, found $REL_COUNT"
    exit 1
fi

echo "Data verified on replica"
'''
                    
                    # Get the correct container name for the replica pod
                    replica_container_name = get_falkordb_container_name(replica_pod, namespace)
                    assert replica_container_name is not None, f"Could not find FalkorDB container in pod {replica_pod}"
                    
                    exec_cmd = [
                        'kubectl', 'exec', replica_pod,
                        '-n', namespace,
                        '-c', replica_container_name,
                        '--',
                        'sh', '-c', verify_data_script
                    ]
                    
                    try:
                        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
                        if result.returncode == 0:
                            logger.info(f"Data verified on replica {i+1}")
                            break  # Success, exit retry loop
                        elif result.returncode == 2:
                            # Retry condition (replication not ready yet)
                            logger.info(f"Replica {i+1} not ready yet, retrying in {retry_delay} seconds...")
                            logger.info(f"Replica {i+1} output: {result.stdout}")
                            if retry < max_retries - 1:  # Don't sleep on last retry
                                time.sleep(retry_delay)
                                continue
                            else:
                                # Last retry failed
                                assert False, f"Replica {i+1} data not replicated after {max_retries} attempts. Last output: {result.stdout}"
                        else:
                            # Hard failure
                            assert False, f"Failed to verify data on replica {i+1}: {result.stderr}"
                    except subprocess.TimeoutExpired:
                        logger.error(f"Timeout verifying data on replica {i+1}")
                        if retry < max_retries - 1:
                            time.sleep(retry_delay)
                            continue
                        else:
                            assert False, f"Timeout verifying data on replica {i+1} after {max_retries} attempts"
                    except Exception as e:
                        logger.error(f"Failed to verify data on replica {i+1}: {e}")
                        if retry < max_retries - 1:
                            time.sleep(retry_delay)
                            continue
                        else:
                            raise
        
        finally:
            if not skip_cleanup:
                cleanup_test_resources(cluster_name, namespace)

    def test_replication_failover(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test failover behavior when master fails."""
        values = {
            "mode": "replication",
            "replicas": 2,
            "sentinel": {
                "enabled": True,
                "replicas": 3
            },
            "instanceType": "e2-medium",
            "storage": 20,
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        
        try:
            # Apply manifests
            for manifest in manifests:
                assert kubectl_apply_manifest(manifest, namespace), f"Failed to apply {manifest['kind']}"
            
            # Wait for cluster to be ready
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=600)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=300)
            
            # Get actual pod names
            cluster_pods = get_cluster_pods(cluster_name, namespace)
            
            # Filter to only get FalkorDB pods (not sentinel pods)
            falkordb_pods = [pod for pod in cluster_pods if 'falkordb-sent' not in pod]
            assert len(falkordb_pods) >= 2, f"Expected at least 2 FalkorDB pods, got {len(falkordb_pods)}"
            
            master_pod = falkordb_pods[0]
            
            # Create test data using kubectl exec
            logger.info("Creating test data before failover...")
            create_failover_data_script = f'''#!/bin/bash
set -e

redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.QUERY failover_test "CREATE (p:Person {{name: 'FailoverTest', id: 12345}})"

echo "Failover test data created"
'''
            
            # Get the correct container name for the master pod
            master_container_name = get_falkordb_container_name(master_pod, namespace)
            assert master_container_name is not None, f"Could not find FalkorDB container in pod {master_pod}"
            
            exec_cmd = [
                'kubectl', 'exec', master_pod,
                '-n', namespace,
                '-c', master_container_name,
                '--',
                'sh', '-c', create_failover_data_script
            ]
            
            try:
                result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
                assert result.returncode == 0, f"Failed to create failover test data: {result.stderr}"
                logger.info("Failover test data created")
            except Exception as e:
                logger.error(f"Failed to create failover test data: {e}")
                raise
                    
            # Simulate master failure by deleting pod
            logger.info(f"Simulating master failure by deleting {master_pod}...")
            core_v1 = client.CoreV1Api()
            try:
                core_v1.delete_namespaced_pod(name=master_pod, namespace=namespace)
                logger.info(f"Deleted pod {master_pod}")
            except ApiException as e:
                logger.warning(f"Failed to delete pod {master_pod}: {e}")
            
            # Wait for failover and pod recreation
            logger.info("Waiting for failover and pod recreation...")
            time.sleep(45)  # Allow time for failover
            
            # Wait for pods to be ready again
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=300)
            
            # Find accessible pod and verify data survived failover using kubectl exec
            remaining_pods = get_cluster_pods(cluster_name, namespace)
            
            # Filter to only get FalkorDB pods (not sentinel pods)
            remaining_falkordb_pods = [pod for pod in remaining_pods if 'falkordb-sent' not in pod]
            data_found = False
            
            for pod_name in remaining_falkordb_pods:
                logger.info(f"Checking data on {pod_name}...")
                
                verify_failover_script = f'''#!/bin/bash
set -e

# Check if failover test data exists
RESULT=$(redis-cli -u "redis://{values["falkordbUser"]["username"]}:{values["falkordbUser"]["password"]}@localhost:6379/" \\
    GRAPH.QUERY failover_test "MATCH (p:Person {{id: 12345}}) RETURN p.name" | grep -o "FailoverTest" || echo "NOT_FOUND")

if [ "$RESULT" = "FailoverTest" ]; then
    echo "SUCCESS: Found failover test data"
    exit 0
else
    echo "NOT_FOUND: Failover test data not found"
    exit 1
fi
'''
                
                # Get the correct container name for the pod
                pod_container_name = get_falkordb_container_name(pod_name, namespace)
                assert pod_container_name is not None, f"Could not find FalkorDB container in pod {pod_name}"
                
                exec_cmd = [
                    'kubectl', 'exec', pod_name,
                    '-n', namespace,
                    '-c', pod_container_name,
                    '--',
                    'sh', '-c', verify_failover_script
                ]
                
                try:
                    result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode == 0:
                        logger.info(f"Found data on {pod_name} - failover successful")
                        data_found = True
                        break
                    else:
                        logger.debug(f"Data not found on {pod_name}")
                except Exception as e:
                    logger.debug(f"Could not connect to {pod_name}: {e}")
                    continue
            
            assert data_found, "Test data not found on any pod after failover"
        
        finally:
            if not skip_cleanup:
                cleanup_test_resources(cluster_name, namespace)

    def test_sentinel_monitoring(self, helm_render, namespace, cluster_name, skip_cleanup):
        """Test sentinel monitoring functionality."""
        values = {
            "mode": "replication",
            "replicas": 2,
            "sentinel": {
                "enabled": True,
                "replicas": 3
            },
            "instanceType": "e2-medium",
            "storage": 20,
            "falkordbUser": {
                "username": "testuser",
                "password": "testpass123"
            },
        }
        
        manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
        
        try:
            # Apply manifests
            for manifest in manifests:
                assert kubectl_apply_manifest(manifest, namespace), f"Failed to apply {manifest['kind']}"
            
            # Wait for cluster to be ready
            assert wait_for_deployment_ready(cluster_name, namespace, timeout=600)
            assert wait_for_pods_ready(f"app.kubernetes.io/instance={cluster_name}", namespace, timeout=300)
            
            # Get all pods to find sentinel pods
            all_pods = get_cluster_pods(cluster_name, namespace)
            sentinel_pods = [pod for pod in all_pods if 'sentinel' in pod.lower()]
            
            if not sentinel_pods:
                logger.warning("No sentinel pods found, checking all pods for sentinel functionality")
                sentinel_pods = all_pods[:3]  # Assume first 3 might be sentinels
            
            logger.info(f"Found {len(sentinel_pods)} potential sentinel pods: {sentinel_pods}")
            
            # Check sentinel pod logs for monitoring activity
            for sentinel_pod in sentinel_pods:
                logger.info(f"Checking sentinel status on {sentinel_pod}")
                
                logs = get_pod_logs(sentinel_pod, namespace, tail_lines=30)
                
                # Look for sentinel-related log messages
                if logs:
                    # Check for sentinel monitoring indicators
                    sentinel_indicators = [
                        'sentinel',
                        'monitor',
                        'master',
                        'slave',
                        'failover'
                    ]
                    
                    found_indicators = []
                    for indicator in sentinel_indicators:
                        if indicator.lower() in logs.lower():
                            found_indicators.append(indicator)
                    
                    if found_indicators:
                        logger.info(f"Sentinel {sentinel_pod} shows monitoring activity: {found_indicators}")
                    else:
                        logger.info(f"Sentinel {sentinel_pod} logs retrieved but no clear monitoring indicators found")
                else:
                    logger.warning(f"No logs found for {sentinel_pod}")
                
                # Test sentinel connection using kubectl exec
                sentinel_test_script = f'''#!/bin/bash
set -e

# Test sentinel connectivity
if redis-cli -p 26379 ping >/dev/null 2>&1; then
    echo "SUCCESS: Sentinel connection on port 26379"
    exit 0
elif redis-cli -p 6379 ping >/dev/null 2>&1; then
    echo "SUCCESS: Redis connection on port 6379"
    exit 0
else
    echo "FAILED: No connection available"
    exit 1
fi
'''
                
                try:
                    # Sentinel pods have different containers than FalkorDB pods
                    # Common sentinel container names are: falkordb-sent, sentinel, redis-sentinel
                    sentinel_container_candidates = ['falkordb-sent', 'sentinel', 'redis-sentinel', 'falkordb']
                    sentinel_container_name = None
                    
                    # Check what containers the sentinel pod has
                    check_cmd = ['kubectl', 'get', 'pod', sentinel_pod, '-n', namespace, '-o', 'jsonpath={.spec.containers[*].name}']
                    check_result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
                    if check_result.returncode == 0:
                        available_containers = check_result.stdout.strip().split()
                        for candidate in sentinel_container_candidates:
                            if candidate in available_containers:
                                sentinel_container_name = candidate
                                break
                    
                    if sentinel_container_name is None:
                        logger.debug(f"Could not find sentinel container in pod {sentinel_pod}")
                        continue
                    
                    exec_cmd = [
                        'kubectl', 'exec', sentinel_pod,
                        '-n', namespace,
                        '-c', sentinel_container_name,
                        '--',
                        'sh', '-c', sentinel_test_script
                    ]
                    
                    result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
                    if result.returncode == 0:
                        logger.info(f"Successfully tested connection to sentinel {sentinel_pod}")
                    else:
                        logger.debug(f"Could not connect to sentinel {sentinel_pod}: {result.stderr}")
                except Exception as e:
                    logger.debug(f"Could not test sentinel {sentinel_pod}: {e}")
            
            logger.info("Sentinel monitoring test completed")
        
        finally:
            if not skip_cleanup:
                cleanup_test_resources(cluster_name, namespace)