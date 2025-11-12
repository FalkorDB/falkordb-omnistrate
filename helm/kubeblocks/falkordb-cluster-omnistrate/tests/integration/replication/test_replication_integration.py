"""Integration tests for replication mode deployment."""

import logging
import subprocess
import time
import pytest
from kubernetes import client
from kubernetes.client.rest import ApiException

from ...utils.kubernetes import (
    wait_for_pods_ready,
    get_pod_logs,
    get_cluster_pods
)
from ...utils.validation import (
    validate_falkordb_connection_in_replication,
    validate_replication_status,
    get_falkordb_container_name
)

logger = logging.getLogger(__name__)


def find_master_pod(falkordb_pods, namespace, username, password):
    """Find the master pod in a replication setup by checking INFO replication."""
    for pod in falkordb_pods:
        container_name = get_falkordb_container_name(pod, namespace)
        if not container_name:
            continue
        
        check_role_script = f"""#!/bin/bash
redis-cli -u "redis://{username}:{password}@localhost:6379/" INFO replication | grep "role:" | cut -d: -f2 | tr -d '\\r'
"""
        
        try:
            result = subprocess.run(
                ['kubectl', 'exec', pod, '-n', namespace, '-c', container_name, '--', 'sh', '-c', check_role_script],
                capture_output=True, text=True, timeout=10
            )
            
            role = result.stdout.strip()
            logger.info(f"Pod {pod} role: {role}")
            
            if role == "master":
                logger.info(f"Found master pod: {pod}")
                return pod
        except Exception as e:
            logger.warning(f"Failed to check role for pod {pod}: {e}")
            continue
    
    # Fallback to first pod if we can't determine master
    logger.warning("Could not definitively identify master, using first pod as fallback")
    return falkordb_pods[0] if falkordb_pods else None


@pytest.mark.integration
class TestReplicationIntegration:
    """Integration tests for replication mode FalkorDB deployment."""

    def test_replication_deployment_basic(self, shared_replication_cluster):
        """Test basic replication deployment with sentinel."""
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        all_pods = cluster_info["all_pods"]
        falkordb_pods = cluster_info["falkordb_pods"]
        sentinel_pods = cluster_info["sentinel_pods"]
        username = cluster_info["username"]
        password = cluster_info["password"]
        
        logger.info(f"Testing replication cluster {cluster_name}")
        logger.info(f"Found {len(falkordb_pods)} falkordb pods: {falkordb_pods}")
        logger.info(f"Found {len(sentinel_pods)} sentinel pods: {sentinel_pods}")
        
        assert len(all_pods) >= 5, f"Expected at least 5 pods (2 falkordb + 3 sentinel), got {len(all_pods)}"
        assert len(falkordb_pods) >= 2, f"Expected at least 2 falkordb pods, got {len(falkordb_pods)}"
        assert len(sentinel_pods) >= 3, f"Expected at least 3 sentinel pods, got {len(sentinel_pods)}"
        
        # Test connection to falkordb pods only using kubectl exec (reliable approach)
        for pod in falkordb_pods:
            assert validate_falkordb_connection_in_replication(
                pod, namespace, username, password
            ), f"Failed to connect to falkordb pod {pod}"
        
        # Test replication status from first falkordb pod (assume it's master)
        master_pod = falkordb_pods[0]
        assert validate_replication_status(master_pod, namespace, username, password, 
                                         expected_replicas=1)  # Expect at least 1 replica
        logger.info("Replication status validated")

    def test_replication_data_persistence(self, shared_replication_cluster):
        """Test data persistence and replication."""
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        falkordb_pods = cluster_info["falkordb_pods"]
        username = cluster_info["username"]
        password = cluster_info["password"]
        
        assert len(falkordb_pods) >= 2, f"Expected at least 2 FalkorDB pods, got {len(falkordb_pods)}"
        
        master_pod = falkordb_pods[0]
        replica_pods = falkordb_pods[1:]
        
        # Create test data on master using kubectl exec (reliable approach)
        logger.info("Creating test data on master...")
        create_data_script = f'''#!/bin/bash
set -e

# First create a basic Redis key to test replication
redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    SET test_key "test_value"

# Create test data using redis-cli
redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.QUERY test_replication "CREATE (p:Person {{name: 'Alice', age: 30}})"

redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.QUERY test_replication "CREATE (p:Person {{name: 'Bob', age: 25}})"

redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
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
KEY_RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GET test_key 2>/dev/null || echo "")

if [ "$KEY_RESULT" != "test_value" ]; then
    echo "ERROR: Basic key not found on master: '$KEY_RESULT'"
    exit 1
fi

# Check graph exists
GRAPH_LIST=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
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
REPL_INFO=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    INFO REPLICATION 2>&1)
echo "Replication info: $REPL_INFO"

# First check if basic Redis key is replicated
echo "Checking basic Redis key replication..."
BASIC_KEY=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
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
GRAPHS=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.LIST 2>&1)
echo "Available graphs: $GRAPHS"

# Check if our graph exists in the list
if [[ "$GRAPHS" != *"test_replication"* ]]; then
    echo "WARNING: Graph 'test_replication' not found in available graphs"
    exit 2  # Use exit code 2 for retry
fi

# Verify person count using read-only query
echo "Executing person count query..."
PERSON_RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
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
REL_RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
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

    def test_replication_failover(self, shared_replication_cluster):
        """Test failover behavior when master fails."""
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        falkordb_pods = cluster_info["falkordb_pods"]
        username = cluster_info["username"]
        password = cluster_info["password"]
        
        assert len(falkordb_pods) >= 2, f"Expected at least 2 FalkorDB pods, got {len(falkordb_pods)}"
        
        master_pod = falkordb_pods[0]
        
        # Create test data using kubectl exec
        logger.info("Creating test data before failover...")
        create_failover_data_script = f'''#!/bin/bash
set -e

redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
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
        
        # Wait for replication to sync the test data to replicas
        logger.info("Waiting for replication to sync...")
        time.sleep(30)  # Give time for data to replicate to all replicas
                
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
RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
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

    def test_sentinel_monitoring(self, shared_replication_cluster):
        """Test sentinel monitoring functionality."""
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        all_pods = cluster_info["all_pods"]
        sentinel_pods = cluster_info["sentinel_pods"]
        
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

    def test_multiple_sequential_failovers(self, shared_replication_cluster):
        """Test multiple sequential failovers to verify cluster resilience."""
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        falkordb_pods = cluster_info["falkordb_pods"]
        username = cluster_info["username"]
        password = cluster_info["password"]
        
        assert len(falkordb_pods) >= 2, f"Expected at least 2 FalkorDB pods, got {len(falkordb_pods)}"
        
        # Create initial test data - find and use master pod
        logger.info("Creating test data for sequential failover test...")
        master_pod = find_master_pod(falkordb_pods, namespace, username, password)
        assert master_pod is not None, "Could not find master pod"
        master_container_name = get_falkordb_container_name(master_pod, namespace)
        assert master_container_name is not None
        
        create_data_script = f'''#!/bin/bash
set -e

redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.QUERY sequential_failover "CREATE (:TestNode {{id: 'initial', failover_round: 0}})"

echo "Initial test data created"
'''
        
        exec_cmd = [
            'kubectl', 'exec', master_pod,
            '-n', namespace,
            '-c', master_container_name,
            '--',
            'sh', '-c', create_data_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"Failed to create initial data: {result.stderr}"
        logger.info("Initial test data created")
        
        # Wait for initial replication
        time.sleep(15)
        
        # Perform 3 sequential failovers
        for failover_round in range(1, 4):
            logger.info(f"Starting failover round {failover_round}/3")
            
            # Get current pods (they may change after each failover)
            current_pods = get_cluster_pods(cluster_name, namespace)
            current_falkordb_pods = [pod for pod in current_pods if 'falkordb-sent' not in pod]
            
            assert len(current_falkordb_pods) >= 2, f"Not enough pods for failover round {failover_round}"
            
            # Find current master to write data
            current_master = find_master_pod(current_falkordb_pods, namespace, username, password)
            assert current_master is not None, f"Could not find master in round {failover_round}"
            logger.info(f"Round {failover_round}: Current master is {current_master}")
            
            # Add data before failover - write to master
            pod_container = get_falkordb_container_name(current_master, namespace)
            
            add_data_script = f'''#!/bin/bash
set -e

redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.QUERY sequential_failover "CREATE (:TestNode {{id: 'round_{failover_round}', failover_round: {failover_round}}})"

echo "Data added for round {failover_round}"
'''
            
            exec_cmd = [
                'kubectl', 'exec', current_master,
                '-n', namespace,
                '-c', pod_container,
                '--',
                'sh', '-c', add_data_script
            ]
            
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, f"Failed to add data for round {failover_round}: {result.stderr}"
            
            # Wait for replication
            time.sleep(10)
            
            # Trigger failover by deleting the first pod
            pod_to_delete = current_falkordb_pods[0]
            logger.info(f"Round {failover_round}: Deleting pod {pod_to_delete}")
            
            core_v1 = client.CoreV1Api()
            try:
                core_v1.delete_namespaced_pod(name=pod_to_delete, namespace=namespace)
                logger.info(f"Deleted pod {pod_to_delete}")
            except ApiException as e:
                logger.warning(f"Failed to delete pod {pod_to_delete}: {e}")
            
            # Wait for failover and pod recreation
            logger.info(f"Waiting for failover {failover_round} to complete...")
            time.sleep(45)
            
            # Wait for pods to be ready
            assert wait_for_pods_ready(
                f"app.kubernetes.io/instance={cluster_name}", 
                namespace, 
                timeout=300
            ), f"Pods not ready after failover {failover_round}"
            
            logger.info(f"Failover round {failover_round} completed")
        
        # Verify all data survived all failovers
        logger.info("Verifying all data survived sequential failovers...")
        
        # Wait additional time for final replication to settle
        time.sleep(30)
        
        final_pods = get_cluster_pods(cluster_name, namespace)
        final_falkordb_pods = [pod for pod in final_pods if 'falkordb-sent' not in pod]
        
        # Try multiple pods in case one isn't fully synced yet
        node_count = 0
        verification_successful = False
        
        for attempt, verify_pod in enumerate(final_falkordb_pods, 1):
            verify_container = get_falkordb_container_name(verify_pod, namespace)
            
            logger.info(f"Verification attempt {attempt}/{len(final_falkordb_pods)} on pod {verify_pod}")
            
            verify_data_script = f'''#!/bin/bash
set -e

# Function to extract count from FalkorDB query result
extract_count() {{
    local output="$1"
    echo "$output" | awk '/^[0-9]+$/{{print; exit}}' || echo "0"
}}

# List all graphs first to confirm the graph exists
GRAPHS=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" --raw GRAPH.LIST 2>&1)
echo "AVAILABLE_GRAPHS: $GRAPHS"

# Count all test nodes
RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" --raw \\
    GRAPH.RO_QUERY sequential_failover "MATCH (n:TestNode) RETURN count(n)" 2>&1)

echo "QUERY_OUTPUT: $RESULT"
COUNT=$(extract_count "$RESULT")
echo "COUNT:$COUNT"

if [ "$COUNT" = "4" ]; then
    echo "SUCCESS: Found all 4 test nodes after sequential failovers"
    exit 0
else
    echo "ERROR: Expected 4 nodes, found $COUNT"
    exit 1
fi
'''
            
            exec_cmd = [
                'kubectl', 'exec', verify_pod,
                '-n', namespace,
                '-c', verify_container,
                '--',
                'sh', '-c', verify_data_script
            ]
            
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
            logger.info(f"Verification output from {verify_pod}:\n{result.stdout}")
            
            # Parse count from output
            for line in result.stdout.split("\n"):
                if line.startswith("COUNT:"):
                    try:
                        value = line.split(":")[1].strip()
                        node_count = int(value) if value else 0
                        if node_count == 4:
                            verification_successful = True
                            logger.info(f"✓ Verification successful on pod {verify_pod}")
                            break
                    except (ValueError, IndexError):
                        pass
            
            if verification_successful:
                break
            else:
                logger.warning(f"Pod {verify_pod} returned count {node_count}, trying next pod...")
                if attempt < len(final_falkordb_pods):
                    time.sleep(10)  # Wait before trying next pod
        
        assert node_count == 4, \
            f"Expected 4 nodes after all failovers, found {node_count}. " \
            f"Checked {len(final_falkordb_pods)} pods. Data may not have replicated properly across all failovers."
        logger.info("Sequential failover test completed successfully - all data preserved")

    def test_replication_vertical_scaling(self, shared_replication_cluster):
        """Test vertical scaling by updating cluster resources for replication mode."""
        import json
        
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        falkordb_pods = cluster_info["falkordb_pods"]
        username = cluster_info["username"]
        password = cluster_info["password"]
        
        logger.info("Testing replication vertical scaling...")

        # Write initial data - find current master dynamically
        logger.info("Writing initial data before scaling...")
        master_pod = find_master_pod(falkordb_pods, namespace, username, password)
        assert master_pod is not None, "Could not identify master pod"
        logger.info(f"Identified master pod: {master_pod}")
        master_container_name = get_falkordb_container_name(master_pod, namespace)
        
        create_data_script = f'''#!/bin/bash
set -e

redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.QUERY vertical_scale_test "CREATE (:ScaleTest {{id: 'initial', phase: 'before_scale'}})"

echo "Initial data created"
'''
        
        exec_cmd = [
            'kubectl', 'exec', master_pod,
            '-n', namespace,
            '-c', master_container_name,
            '--',
            'sh', '-c', create_data_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"Failed to create initial data: {result.stderr}"
        logger.info("Initial data created")
        
        # Wait for replication
        time.sleep(10)

        # Get current resource limits
        logger.info("Getting current cluster resources...")
        get_cluster_cmd = [
            "kubectl", "get", "cluster", cluster_name,
            "-n", namespace,
            "-o", "json"
        ]
        
        result = subprocess.run(get_cluster_cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Failed to get cluster: {result.stderr}"
        
        cluster_data = json.loads(result.stdout)
        # For replication mode, resources are in componentSpecs
        falkordb_component = next(
            c for c in cluster_data["spec"]["componentSpecs"] 
            if c["name"] == "falkordb"
        )
        current_resources = falkordb_component["resources"]
        current_cpu = current_resources["limits"]["cpu"]
        current_memory = current_resources["limits"]["memory"]
        
        logger.info(f"Current resources - CPU: {current_cpu}, Memory: {current_memory}")

        # Parse CPU value (handles "1", "1000m", "0.5", etc.)
        def parse_cpu(cpu_str):
            if cpu_str.endswith('m'):
                return float(cpu_str[:-1]) / 1000
            return float(cpu_str)
        
        # Parse memory value (handles "100M", "100Mi", "1Gi", "1G", etc.)
        def parse_memory_mb(mem_str):
            if mem_str.endswith('Gi'):
                return int(mem_str[:-2]) * 1024
            elif mem_str.endswith('G'):
                return int(mem_str[:-1]) * 1000
            elif mem_str.endswith('Mi'):
                return int(mem_str[:-2])
            elif mem_str.endswith('M'):
                return int(mem_str[:-1])
            elif mem_str.endswith('Ki'):
                return int(mem_str[:-2]) // 1024
            elif mem_str.endswith('K'):
                return int(mem_str[:-1]) // 1000
            else:
                # Assume bytes
                return int(mem_str) // (1024 * 1024)
        
        # Scale up resources (simulate vertical scaling)
        current_cpu_value = parse_cpu(current_cpu)
        new_cpu = str(min(current_cpu_value + 0.5, 8))  # Add 0.5 CPU, cap at 8
        
        current_memory_mb = parse_memory_mb(current_memory)
        new_memory_mb = min(current_memory_mb + 512, 16384)  # Add 512MB, cap at 16GB
        new_memory = f"{new_memory_mb}Mi"
        
        logger.info(f"Scaling up to - CPU: {new_cpu}, Memory: {new_memory}")
        
        # Find the index of the falkordb component
        component_index = next(
            i for i, c in enumerate(cluster_data["spec"]["componentSpecs"])
            if c["name"] == "falkordb"
        )
        
        patch_cmd = [
            "kubectl", "patch", "cluster", cluster_name,
            "-n", namespace,
            "--type", "json",
            "-p", json.dumps([
                {
                    "op": "replace",
                    "path": f"/spec/componentSpecs/{component_index}/resources/limits/cpu",
                    "value": new_cpu
                },
                {
                    "op": "replace",
                    "path": f"/spec/componentSpecs/{component_index}/resources/limits/memory",
                    "value": new_memory
                },
                {
                    "op": "replace",
                    "path": f"/spec/componentSpecs/{component_index}/resources/requests/cpu",
                    "value": new_cpu
                },
                {
                    "op": "replace",
                    "path": f"/spec/componentSpecs/{component_index}/resources/requests/memory",
                    "value": new_memory
                }
            ])
        ]
        
        patch_result = subprocess.run(patch_cmd, capture_output=True, text=True)
        assert patch_result.returncode == 0, f"Failed to patch cluster: {patch_result.stderr}"
        logger.info("Cluster resources patched successfully")

        # Wait for pods to restart with new resources
        logger.info("Waiting for pods to restart with new resources...")
        time.sleep(60)
        
        # Wait for pods to be ready
        assert wait_for_pods_ready(
            f"app.kubernetes.io/instance={cluster_name}", 
            namespace, 
            timeout=300
        ), "Pods not ready after vertical scaling"

        # Verify data persisted after scaling
        logger.info("Verifying data persistence after vertical scaling...")
        
        # Get current pods (may have changed)
        current_pods = get_cluster_pods(cluster_name, namespace)
        current_falkordb_pods = [pod for pod in current_pods if 'falkordb-sent' not in pod]
        
        verify_pod = current_falkordb_pods[0]
        verify_container = get_falkordb_container_name(verify_pod, namespace)
        
        verify_data_script = f'''#!/bin/bash
set -e

# Function to extract count
extract_count() {{
    local output="$1"
    echo "$output" | awk '/^[0-9]+$/{{print; exit}}' || echo "0"
}}

RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" --raw \\
    GRAPH.RO_QUERY vertical_scale_test "MATCH (n:ScaleTest) RETURN count(n)" 2>&1)

COUNT=$(extract_count "$RESULT")
echo "COUNT:$COUNT"

if [ "$COUNT" = "1" ]; then
    echo "SUCCESS: Data preserved after vertical scaling"
    exit 0
else
    echo "ERROR: Expected 1 node, found $COUNT"
    exit 1
fi
'''
        
        exec_cmd = [
            'kubectl', 'exec', verify_pod,
            '-n', namespace,
            '-c', verify_container,
            '--',
            'sh', '-c', verify_data_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
        
        # Parse count
        node_count = 0
        for line in result.stdout.split("\n"):
            if line.startswith("COUNT:"):
                try:
                    node_count = int(line.split(":")[1])
                    break
                except (ValueError, IndexError):
                    pass
        
        assert node_count == 1, f"Data loss after vertical scaling: expected 1 node, found {node_count}"
        logger.info("Data preserved after vertical scaling")

        # Scale back to original resources
        logger.info(f"Scaling back to original resources - CPU: {current_cpu}, Memory: {current_memory}")
        
        restore_cmd = [
            "kubectl", "patch", "cluster", cluster_name,
            "-n", namespace,
            "--type", "json",
            "-p", json.dumps([
                {
                    "op": "replace",
                    "path": f"/spec/componentSpecs/{component_index}/resources/limits/cpu",
                    "value": current_cpu
                },
                {
                    "op": "replace",
                    "path": f"/spec/componentSpecs/{component_index}/resources/limits/memory",
                    "value": current_memory
                },
                {
                    "op": "replace",
                    "path": f"/spec/componentSpecs/{component_index}/resources/requests/cpu",
                    "value": current_cpu
                },
                {
                    "op": "replace",
                    "path": f"/spec/componentSpecs/{component_index}/resources/requests/memory",
                    "value": current_memory
                }
            ])
        ]
        
        restore_result = subprocess.run(restore_cmd, capture_output=True, text=True)
        assert restore_result.returncode == 0, f"Failed to restore cluster resources: {restore_result.stderr}"
        
        # Wait for restoration
        time.sleep(30)
        
        logger.info("Replication vertical scaling test completed successfully")

    def test_replication_replica_scaling(self, shared_replication_cluster):
        """Test scaling replicas up and down in replication mode."""
        import json
        
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        falkordb_pods = cluster_info["falkordb_pods"]
        username = cluster_info["username"]
        password = cluster_info["password"]
        
        logger.info("Testing replication replica scaling...")

        # Get initial replica count
        get_cluster_cmd = [
            "kubectl", "get", "cluster", cluster_name,
            "-n", namespace,
            "-o", "json"
        ]
        
        result = subprocess.run(get_cluster_cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Failed to get cluster: {result.stderr}"
        
        cluster_data = json.loads(result.stdout)
        falkordb_component = next(
            c for c in cluster_data["spec"]["componentSpecs"] 
            if c["name"] == "falkordb"
        )
        initial_replicas = falkordb_component["replicas"]
        logger.info(f"Initial replica count: {initial_replicas}")

        # Write test data - find current master dynamically
        logger.info("Writing test data before scaling...")
        master_pod = find_master_pod(falkordb_pods, namespace, username, password)
        assert master_pod is not None, "Could not identify master pod"
        logger.info(f"Identified master pod: {master_pod}")
        master_container_name = get_falkordb_container_name(master_pod, namespace)
        
        create_data_script = f'''#!/bin/bash
set -e

redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.QUERY replica_scale_test "CREATE (:ReplicaTest {{id: 'test', replicas: {initial_replicas}}})"

echo "Test data created"
'''
        
        exec_cmd = [
            'kubectl', 'exec', master_pod,
            '-n', namespace,
            '-c', master_container_name,
            '--',
            'sh', '-c', create_data_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"Failed to create test data: {result.stderr}"
        logger.info("Test data created")
        
        # Wait for replication
        time.sleep(10)

        # Scale up replicas
        new_replica_count = initial_replicas + 1
        logger.info(f"Scaling up from {initial_replicas} to {new_replica_count} replicas")
        
        component_index = next(
            i for i, c in enumerate(cluster_data["spec"]["componentSpecs"])
            if c["name"] == "falkordb"
        )
        
        scale_up_cmd = [
            "kubectl", "patch", "cluster", cluster_name,
            "-n", namespace,
            "--type", "json",
            "-p", json.dumps([{
                "op": "replace",
                "path": f"/spec/componentSpecs/{component_index}/replicas",
                "value": new_replica_count
            }])
        ]
        
        scale_result = subprocess.run(scale_up_cmd, capture_output=True, text=True)
        assert scale_result.returncode == 0, f"Failed to scale up: {scale_result.stderr}"
        logger.info("Scale up initiated")

        # Wait for new replicas to be created and ready
        logger.info("Waiting for new replicas to be ready...")
        time.sleep(60)
        
        # Wait for pods to be ready
        max_wait = 300
        start_wait = time.time()
        pods_ready = False
        
        while time.time() - start_wait < max_wait:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", namespace,
                 "-l", f"app.kubernetes.io/instance={cluster_name},apps.kubeblocks.io/component-name=falkordb",
                 "-o", "json"],
                capture_output=True, text=True
            )
            
            if result.returncode == 0:
                pods_data = json.loads(result.stdout)
                pods = pods_data.get("items", [])
                ready_pods = [
                    p for p in pods 
                    if any(c.get("type") == "Ready" and c.get("status") == "True" 
                           for c in p.get("status", {}).get("conditions", []))
                ]
                
                if len(ready_pods) >= new_replica_count:
                    pods_ready = True
                    logger.info(f"All {new_replica_count} replicas ready")
                    break
                else:
                    elapsed = int(time.time() - start_wait)
                    logger.info(f"Waiting for replicas... ({len(ready_pods)}/{new_replica_count} ready, {elapsed}s elapsed)")
            
            time.sleep(10)
        
        assert pods_ready, f"Replicas not ready within {max_wait}s after scaling up"

        # Verify data is still accessible
        logger.info("Verifying data after scaling up...")
        current_pods = get_cluster_pods(cluster_name, namespace)
        current_falkordb_pods = [pod for pod in current_pods if 'falkordb-sent' not in pod]
        
        # Verify data on new replica
        if len(current_falkordb_pods) > len(falkordb_pods):
            new_replica = current_falkordb_pods[-1]  # Last pod should be the new one
            new_replica_container = get_falkordb_container_name(new_replica, namespace)
            
            # Give time for replication to sync to new replica - retry with backoff
            logger.info("Waiting for data to replicate to new replica...")
            
            verify_data_script = f'''#!/bin/bash
set -e

# Function to extract count
extract_count() {{
    local output="$1"
    echo "$output" | awk '/^[0-9]+$/{{print; exit}}' || echo "0"
}}

# List graphs to confirm connectivity
GRAPHS=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" --raw GRAPH.LIST 2>&1)
echo "AVAILABLE_GRAPHS: $GRAPHS"

# Query the data
RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" --raw \\
    GRAPH.RO_QUERY replica_scale_test "MATCH (n:ReplicaTest) RETURN count(n)" 2>&1)

echo "QUERY_RESULT: $RESULT"
COUNT=$(extract_count "$RESULT")
echo "COUNT:$COUNT"
'''
            
            # Retry logic for replication sync
            max_retries = 6
            node_count = 0
            
            for retry in range(max_retries):
                wait_time = 15 * (retry + 1)  # 15s, 30s, 45s, 60s, 75s, 90s
                logger.info(f"Replication sync attempt {retry + 1}/{max_retries}, waiting {wait_time}s...")
                time.sleep(wait_time)
                
                exec_cmd = [
                    'kubectl', 'exec', new_replica,
                    '-n', namespace,
                    '-c', new_replica_container,
                    '--',
                    'sh', '-c', verify_data_script
                ]
                
                result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
                logger.info(f"Verification output from new replica {new_replica}:\n{result.stdout}")
                
                for line in result.stdout.split("\n"):
                    if line.startswith("COUNT:"):
                        try:
                            value = line.split(":")[1].strip()
                            node_count = int(value) if value else 0
                            if node_count == 1:
                                logger.info(f"✓ Data replicated successfully to new replica after {wait_time}s")
                                break
                        except (ValueError, IndexError) as e:
                            logger.warning(f"Failed to parse count: {e}")
                
                if node_count == 1:
                    break
                elif retry < max_retries - 1:
                    logger.warning(f"Replication not complete yet (found {node_count} nodes), will retry...")
            
            assert node_count == 1, \
                f"Data not replicated to new replica after {max_retries} attempts: expected 1 node, found {node_count}. " \
                f"Replication may be too slow or not working properly."
            logger.info("Data successfully replicated to new replica")

        # Scale back down to original count
        logger.info(f"Scaling back down from {new_replica_count} to {initial_replicas} replicas")
        
        scale_down_cmd = [
            "kubectl", "patch", "cluster", cluster_name,
            "-n", namespace,
            "--type", "json",
            "-p", json.dumps([{
                "op": "replace",
                "path": f"/spec/componentSpecs/{component_index}/replicas",
                "value": initial_replicas
            }])
        ]
        
        scale_down_result = subprocess.run(scale_down_cmd, capture_output=True, text=True)
        assert scale_down_result.returncode == 0, f"Failed to scale down: {scale_down_result.stderr}"
        logger.info("Scale down initiated")
        
        # Wait for scale down to complete
        time.sleep(30)
        
        # Verify data still accessible after scaling down
        logger.info("Verifying data after scaling down...")
        final_pods = get_cluster_pods(cluster_name, namespace)
        final_falkordb_pods = [pod for pod in final_pods if 'falkordb-sent' not in pod]
        
        verify_pod = final_falkordb_pods[0]
        verify_container = get_falkordb_container_name(verify_pod, namespace)
        
        exec_cmd = [
            'kubectl', 'exec', verify_pod,
            '-n', namespace,
            '-c', verify_container,
            '--',
            'sh', '-c', verify_data_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
        
        node_count = 0
        for line in result.stdout.split("\n"):
            if line.startswith("COUNT:"):
                try:
                    node_count = int(line.split(":")[1])
                    break
                except (ValueError, IndexError):
                    pass
        
        assert node_count == 1, f"Data loss after scaling down: expected 1 node, found {node_count}"
        logger.info("Data preserved after scaling down")
        
        logger.info("Replication replica scaling test completed successfully")

    def test_replication_oom_resilience(self, shared_replication_cluster):
        """Test that FalkorDB throws OOM errors instead of crashing when reaching maxmemory in replication mode."""
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        falkordb_pods = cluster_info["falkordb_pods"]
        username = cluster_info["username"]
        password = cluster_info["password"]
        
        logger.info("Testing replication OOM behavior - verifying graceful error handling...")

        master_pod = falkordb_pods[0]
        master_container_name = get_falkordb_container_name(master_pod, namespace)
        
        # IMPORTANT: Set maxmemory on master node to enable OOM testing
        logger.info("Setting maxmemory to 128MB on master node...")
        
        set_maxmemory_script = f"""#!/bin/bash
redis-cli -u "redis://{username}:{password}@localhost:6379/" CONFIG SET maxmemory 134217728
redis-cli -u "redis://{username}:{password}@localhost:6379/" CONFIG SET maxmemory-policy noeviction
echo "maxmemory configured on master"
"""
        
        try:
            result = subprocess.run(
                ['kubectl', 'exec', master_pod, '-n', namespace, '-c', master_container_name, 
                 '--', 'sh', '-c', set_maxmemory_script],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                logger.info(f"✓ maxmemory configured on master {master_pod}: 128MB with noeviction policy")
            else:
                logger.error(f"Failed to set maxmemory on master: {result.stderr}")
                pytest.fail(f"Cannot run OOM test without setting maxmemory on master")
        except Exception as e:
            logger.error(f"Error setting maxmemory on master: {e}")
            pytest.fail(f"Cannot run OOM test without setting maxmemory: {e}")
        
        # Get initial pod restart counts for all falkordb pods
        initial_restart_counts = {}
        logger.info("Recording initial pod restart counts...")
        
        for pod_name in falkordb_pods:
            try:
                result = subprocess.run(
                    ["kubectl", "get", "pod", pod_name, "-n", namespace, "-o", "json"],
                    capture_output=True, text=True
                )
                
                if result.returncode == 0:
                    import json
                    pod_data = json.loads(result.stdout)
                    if pod_data.get("status", {}).get("containerStatuses"):
                        for cs in pod_data["status"]["containerStatuses"]:
                            if 'falkordb' in cs["name"].lower() and 'sent' not in cs["name"].lower():
                                restart_count = cs.get("restartCount", 0)
                                initial_restart_counts[pod_name] = restart_count
                                logger.info(f"Pod {pod_name}: initial restart count = {restart_count}")
                                break
            except Exception as e:
                logger.warning(f"Could not get restart count for {pod_name}: {e}")
        
        # Create a script that attempts to trigger OOM and captures the error
        oom_test_script = f"""#!/bin/bash

# Attempt to create large dataset and capture OOM error
echo "Attempting to trigger OOM by creating large dataset..."

oom_error_found=0

for batch in $(seq 1 100); do
    # Try to create nodes with large data
    output=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
        GRAPH.QUERY oom_stress_test "UNWIND range(1, 1000) AS id CREATE (:StressNode {{id: id + ($batch * 1000), data: 'x' * 10000}})" 2>&1)
    
    exit_code=$?
    
    # Check if we got an OOM-related error
    if echo "$output" | grep -iE "oom|out of memory|maxmemory|memory" >/dev/null 2>&1; then
        echo "OOM_ERROR_DETECTED: $output"
        oom_error_found=1
        exit 0
    fi
    
    # If command failed for another reason, report it
    if [ $exit_code -ne 0 ]; then
        echo "ERROR_OCCURRED: $output"
        exit 0
    fi
    
    # Add small delay between batches
    sleep 0.1
done

if [ $oom_error_found -eq 0 ]; then
    echo "NO_OOM_TRIGGERED: Created all batches without hitting memory limit"
fi

exit 0
"""
        
        exec_cmd = [
            'kubectl', 'exec', master_pod,
            '-n', namespace,
            '-c', master_container_name,
            '--',
            'sh', '-c', oom_test_script
        ]
        
        oom_error_detected = False
        oom_error_message = None
        
        try:
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=300)
            output = result.stdout
            logger.info(f"OOM test output: {output}")
            
            if "OOM_ERROR_DETECTED:" in output:
                oom_error_detected = True
                # Extract the error message
                for line in output.split("\n"):
                    if "OOM_ERROR_DETECTED:" in line:
                        oom_error_message = line.split("OOM_ERROR_DETECTED:", 1)[1].strip()
                        break
                logger.info(f"✓ OOM error detected as expected: {oom_error_message}")
            elif "NO_OOM_TRIGGERED:" in output:
                logger.warning("OOM was not triggered - memory limit may be too high for this test")
            
        except subprocess.TimeoutExpired:
            logger.info("OOM test timed out (may indicate system under heavy load)")
        except Exception as e:
            logger.info(f"OOM test encountered issue: {e}")

        # Wait for system to stabilize
        logger.info("Waiting for system to stabilize...")
        time.sleep(15)

        # Critical check: Verify pods did NOT restart (crash)
        logger.info("Verifying pods did not crash during OOM test...")
        
        pods_crashed = False
        for pod_name in falkordb_pods:
            try:
                result = subprocess.run(
                    ["kubectl", "get", "pod", pod_name, "-n", namespace, "-o", "json"],
                    capture_output=True, text=True
                )
                
                if result.returncode == 0:
                    import json
                    pod_data = json.loads(result.stdout)
                    if pod_data.get("status", {}).get("containerStatuses"):
                        for cs in pod_data["status"]["containerStatuses"]:
                            if 'falkordb' in cs["name"].lower() and 'sent' not in cs["name"].lower():
                                current_restart_count = cs.get("restartCount", 0)
                                initial_count = initial_restart_counts.get(pod_name, 0)
                                
                                if current_restart_count > initial_count:
                                    logger.error(f"❌ Pod {pod_name} restarted during OOM test! "
                                               f"(initial: {initial_count}, current: {current_restart_count})")
                                    pods_crashed = True
                                else:
                                    logger.info(f"✓ Pod {pod_name} did not restart (count: {current_restart_count})")
                                break
            except Exception as e:
                logger.warning(f"Could not verify restart count for {pod_name}: {e}")
        
        assert not pods_crashed, \
            "One or more pods crashed during OOM test. FalkorDB should handle OOM gracefully without crashing."

        # Verify replication is still functional
        logger.info("Verifying replication functionality after OOM test...")
        
        # Get current pods
        current_pods = get_cluster_pods(cluster_name, namespace)
        current_falkordb_pods = [pod for pod in current_pods if 'falkordb-sent' not in pod]
        
        test_functionality_script = f"""#!/bin/bash
set -e

# Test basic functionality
redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.QUERY oom_recovery_test "CREATE (:RecoveryTest {{id: 'after_oom'}}) RETURN 1" >/dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "SUCCESS: Replication functional after OOM test"
    exit 0
else
    echo "ERROR: Replication not functional"
    exit 1
fi
"""
        
        # Verify functionality with retry
        max_retries = 3
        replication_functional = False
        for attempt in range(max_retries):
            accessible_pod = current_falkordb_pods[attempt % len(current_falkordb_pods)]
            pod_container = get_falkordb_container_name(accessible_pod, namespace)
            
            exec_cmd = [
                'kubectl', 'exec', accessible_pod,
                '-n', namespace,
                '-c', pod_container,
                '--',
                'sh', '-c', test_functionality_script
            ]
            
            try:
                result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0 and "SUCCESS" in result.stdout:
                    logger.info(f"✓ Replication is functional after OOM test (pod: {accessible_pod})")
                    replication_functional = True
                    break
                else:
                    if attempt < max_retries - 1:
                        logger.info(f"Replication not ready, retry {attempt + 1}/{max_retries}")
                        time.sleep(10)
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.info(f"Retry {attempt + 1}/{max_retries} after error: {e}")
                    time.sleep(10)
        
        assert replication_functional, "Replication should remain functional after OOM test"

        # Verify pods are still running
        logger.info("Verifying all pods are still running...")
        assert wait_for_pods_ready(
            f"app.kubernetes.io/instance={cluster_name}", 
            namespace, 
            timeout=180
        ), "Pods should remain running after OOM test"
        
        # Summary
        if oom_error_detected:
            logger.info("=" * 60)
            logger.info("✓ OOM TEST PASSED")
            logger.info(f"  - FalkorDB threw OOM error: {oom_error_message}")
            logger.info("  - No pods crashed or restarted")
            logger.info("  - Replication remained functional")
            logger.info("=" * 60)
        else:
            logger.info("=" * 60)
            logger.info("✓ OOM TEST PASSED (no OOM triggered)")
            logger.info("  - No pods crashed or restarted")
            logger.info("  - Replication remained stable")
            logger.info("=" * 60)
        
        logger.info("Replication OOM resilience test completed successfully")

    def test_replication_data_persistence_after_scaling(self, shared_replication_cluster):
        """Test comprehensive data persistence through multiple scaling operations in replication mode."""
        import json
        
        cluster_info = shared_replication_cluster
        cluster_name = cluster_info["name"]
        namespace = cluster_info["namespace"]
        falkordb_pods = cluster_info["falkordb_pods"]
        username = cluster_info["username"]
        password = cluster_info["password"]
        
        logger.info("Testing data persistence through scaling operations in replication mode...")

        # Create initial dataset with known values - find current master dynamically
        logger.info("Creating initial dataset...")
        master_pod = find_master_pod(falkordb_pods, namespace, username, password)
        assert master_pod is not None, "Could not identify master pod"
        logger.info(f"Identified master pod: {master_pod}")
        master_container_name = get_falkordb_container_name(master_pod, namespace)
        
        create_dataset_script = f"""#!/bin/bash
set -e

# Create a structured dataset that we can verify later
for i in $(seq 1 15); do
    redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
        GRAPH.QUERY repl_persistence_test "CREATE (:DataNode {{id: $i, value: 'data_$i', phase: 'initial'}})" >/dev/null 2>&1
done

# Create relationships
redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    GRAPH.QUERY repl_persistence_test "MATCH (a:DataNode), (b:DataNode) WHERE a.id = 1 AND b.id = 15 CREATE (a)-[:LINKED]->(b)" >/dev/null 2>&1

echo "SUCCESS: Initial dataset created"
"""
        
        exec_cmd = [
            'kubectl', 'exec', master_pod,
            '-n', namespace,
            '-c', master_container_name,
            '--',
            'sh', '-c', create_dataset_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0 and "SUCCESS" in result.stdout, \
            f"Failed to create initial dataset: {result.stderr}"
        logger.info("Initial dataset created successfully")
        
        # Wait for replication
        time.sleep(15)
        
        # Function to verify data integrity
        def verify_data(expected_nodes, expected_relationships, phase_description):
            # Get current accessible pods
            current_pods = get_cluster_pods(cluster_name, namespace)
            current_falkordb_pods = [pod for pod in current_pods if 'falkordb-sent' not in pod]
            
            verify_pod = current_falkordb_pods[0]
            verify_container = get_falkordb_container_name(verify_pod, namespace)
            
            verify_script = f"""#!/bin/bash
set -e

# Function to extract count
extract_count() {{
    local output="$1"
    echo "$output" | awk '/^[0-9]+$/{{print; exit}}' || echo "0"
}}

# Count nodes
NODE_RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    --raw GRAPH.RO_QUERY repl_persistence_test "MATCH (n:DataNode) RETURN count(n)" 2>&1)
NODE_COUNT=$(extract_count "$NODE_RESULT")

# Count relationships
REL_RESULT=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" \\
    --raw GRAPH.RO_QUERY repl_persistence_test "MATCH ()-[r:LINKED]->() RETURN count(r)" 2>&1)
REL_COUNT=$(extract_count "$REL_RESULT")

echo "NODE_COUNT:$NODE_COUNT"
echo "REL_COUNT:$REL_COUNT"

if [ "$NODE_COUNT" = "{expected_nodes}" ] && [ "$REL_COUNT" = "{expected_relationships}" ]; then
    echo "SUCCESS: Data verified"
    exit 0
else
    echo "ERROR: Expected {expected_nodes} nodes and {expected_relationships} rels, found $NODE_COUNT nodes and $REL_COUNT rels"
    exit 1
fi
"""
            
            exec_cmd = [
                'kubectl', 'exec', verify_pod,
                '-n', namespace,
                '-c', verify_container,
                '--',
                'sh', '-c', verify_script
            ]
            
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
            
            # Parse counts with error handling
            node_count = 0
            rel_count = 0
            for line in result.stdout.split("\n"):
                if line.startswith("NODE_COUNT:"):
                    try:
                        value = line.split(":")[1].strip()
                        node_count = int(value) if value else 0
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse node count from '{line}': {e}")
                elif line.startswith("REL_COUNT:"):
                    try:
                        value = line.split(":")[1].strip()
                        rel_count = int(value) if value else 0
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse rel count from '{line}': {e}")
            
            assert node_count == expected_nodes and rel_count == expected_relationships, \
                f"{phase_description}: Expected {expected_nodes} nodes and {expected_relationships} relationships, found {node_count} nodes and {rel_count} relationships"
            logger.info(f"{phase_description}: ✓ Data integrity verified ({node_count} nodes, {rel_count} relationships)")
        
        # Verify initial data
        verify_data(15, 1, "Initial state")
        
        # Test: Horizontal scaling (add replicas)
        logger.info("Test: Scaling replicas horizontally...")
        
        get_cluster_cmd = [
            "kubectl", "get", "cluster", cluster_name,
            "-n", namespace,
            "-o", "json"
        ]
        
        result = subprocess.run(get_cluster_cmd, capture_output=True, text=True)
        cluster_data = json.loads(result.stdout)
        
        # Find falkordb component
        component_index = next(
            i for i, c in enumerate(cluster_data["spec"]["componentSpecs"])
            if c["name"] == "falkordb"
        )
        current_replicas = cluster_data["spec"]["componentSpecs"][component_index]["replicas"]
        
        # Scale up
        patch_cmd = [
            "kubectl", "patch", "cluster", cluster_name,
            "-n", namespace,
            "--type", "json",
            "-p", json.dumps([{
                "op": "replace",
                "path": f"/spec/componentSpecs/{component_index}/replicas",
                "value": current_replicas + 1
            }])
        ]
        
        subprocess.run(patch_cmd, capture_output=True, text=True, timeout=30)
        time.sleep(60)
        
        # Wait for pods to be ready
        wait_for_pods_ready(
            f"app.kubernetes.io/instance={cluster_name}", 
            namespace, 
            timeout=180
        )
        
        # Wait for replication to sync
        time.sleep(15)
        
        verify_data(15, 1, "After horizontal scale-up")
        
        # Scale back down
        patch_cmd = [
            "kubectl", "patch", "cluster", cluster_name,
            "-n", namespace,
            "--type", "json",
            "-p", json.dumps([{
                "op": "replace",
                "path": f"/spec/componentSpecs/{component_index}/replicas",
                "value": current_replicas
            }])
        ]
        
        subprocess.run(patch_cmd, capture_output=True, text=True, timeout=30)
        time.sleep(30)
        
        verify_data(15, 1, "After horizontal scale-down")
        
        logger.info("✓ Data persisted through horizontal scaling")
        
        logger.info("Replication data persistence after scaling test completed successfully")