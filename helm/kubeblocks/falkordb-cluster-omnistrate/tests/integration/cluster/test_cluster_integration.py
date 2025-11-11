"""Integration tests for cluster mode deployment."""

import logging
import time
import pytest


from ...utils.validation import (
    validate_falkordb_connection_in_cluster,
    validate_cluster_status,
)

logger = logging.getLogger(__name__)


@pytest.mark.integration
class TestClusterIntegration:
    """Integration tests for cluster mode FalkorDB deployment."""

    def _write_test_data_in_cluster(
        self, pod_name, namespace, username, password, node_id, data_value, timeout=60
    ):
        """
        Write test data to FalkorDB by running commands inside the cluster pod.

        Args:
            pod_name: Name of the pod to execute in
            namespace: Kubernetes namespace
            username: FalkorDB username
            password: FalkorDB password
            node_id: ID for the test node
            data_value: Data value to store
            timeout: Timeout in seconds

        Returns:
            bool: True if data was written successfully
        """
        import subprocess

        # Create a script that writes data using redis-cli
        write_script = f"""#!/bin/bash
set -e

# Write test data using GRAPH.QUERY
if redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY clustergraph "CREATE (:TestNode {{id: '{node_id}', data: '{data_value}'}})" >/dev/null 2>&1; then
    echo "SUCCESS: Test data written for {node_id}"
    exit 0
else
    echo "FAILED: Could not write test data for {node_id}"
    exit 1
fi
"""

        try:
            exec_cmd = [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                namespace,
                "-c",
                "falkordb-cluster",
                "--",
                "sh",
                "-c",
                write_script,
            ]

            logger.debug(f"Writing test data to {pod_name}")
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=timeout
            )

            if result.returncode == 0 and "SUCCESS" in result.stdout:
                return True
            else:
                logger.error(f"Failed to write data to {pod_name}: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"Error writing data to {pod_name}: {e}")
            return False

    def _read_test_data_in_cluster(
        self, pod_name, namespace, username, password, timeout=60
    ):
        """
        Read test data from FalkorDB by running commands inside the cluster pod.

        Args:
            pod_name: Name of the pod to execute in
            namespace: Kubernetes namespace
            username: FalkorDB username
            password: FalkorDB password
            timeout: Timeout in seconds

        Returns:
            int: Number of test nodes found (0 if failed)
        """
        import subprocess

        # Create a script that reads data using redis-cli
        read_script = f"""#!/bin/bash
set -e

# Query test data using GRAPH.QUERY
if result=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY clustergraph "MATCH (n:TestNode) RETURN count(n)" 2>/dev/null); then
    # Extract count from the result - redis-cli returns results in a specific format
    count=$(echo "$result" | grep -o '[0-9]\\+' | head -1)
    if [ -n "$count" ]; then
        echo "COUNT:$count"
        echo "SUCCESS: Found $count test nodes"
        exit 0
    else
        echo "COUNT:0"
        echo "SUCCESS: No test nodes found (empty result)"
        exit 0
    fi
else
    echo "COUNT:0"
    echo "FAILED: Could not query test data"
    exit 1
fi
"""

        try:
            exec_cmd = [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                namespace,
                "-c",
                "falkordb-cluster",
                "--",
                "sh",
                "-c",
                read_script,
            ]

            logger.debug(f"Reading test data from {pod_name}")
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=timeout
            )

            # Parse the count from output
            node_count = 0
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("COUNT:"):
                        try:
                            node_count = int(line.split(":")[1])
                            break
                        except (ValueError, IndexError):
                            pass

            return node_count

        except Exception as e:
            logger.error(f"Error reading data from {pod_name}: {e}")
            return 0

    def _write_resilience_test_data_in_cluster(
        self, pod_name, namespace, username, password, test_id, timeout=60
    ):
        """
        Write resilience test data to FalkorDB by running commands inside the cluster pod.

        Args:
            pod_name: Name of the pod to execute in
            namespace: Kubernetes namespace
            username: FalkorDB username
            password: FalkorDB password
            test_id: ID for the resilience test record
            timeout: Timeout in seconds

        Returns:
            bool: True if data was written successfully
        """
        import subprocess

        # Create a script that writes resilience test data using redis-cli
        write_script = f"""#!/bin/bash
set -e

# Write resilience test data using GRAPH.QUERY
if redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY resilience_test "CREATE (:ResilienceTest {{id: '{test_id}', timestamp: timestamp()}})" >/dev/null 2>&1; then
    echo "SUCCESS: Resilience test data written for {test_id}"
    exit 0
else
    echo "FAILED: Could not write resilience test data for {test_id}"
    exit 1
fi
"""

        try:
            exec_cmd = [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                namespace,
                "-c",
                "falkordb-cluster",
                "--",
                "sh",
                "-c",
                write_script,
            ]

            logger.debug(f"Writing resilience test data to {pod_name}")
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=timeout
            )

            if result.returncode == 0 and "SUCCESS" in result.stdout:
                return True
            else:
                logger.error(
                    f"Failed to write resilience data to {pod_name}: {result.stderr}"
                )
                return False

        except Exception as e:
            logger.error(f"Error writing resilience data to {pod_name}: {e}")
            return False

    def _read_resilience_test_data_in_cluster(
        self, pod_name, namespace, username, password, timeout=60
    ):
        """
        Read resilience test data from FalkorDB by running commands inside the cluster pod.

        Args:
            pod_name: Name of the pod to execute in
            namespace: Kubernetes namespace
            username: FalkorDB username
            password: FalkorDB password
            timeout: Timeout in seconds

        Returns:
            int: Number of resilience test records found (0 if failed)
        """
        import subprocess

        # Create a script that reads resilience test data using redis-cli
        read_script = f"""#!/bin/bash
set -e

# Query resilience test data using GRAPH.QUERY
if result=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY resilience_test "MATCH (n:ResilienceTest) RETURN count(n)" 2>/dev/null); then
    # Extract count from the result - redis-cli returns results in a specific format
    count=$(echo "$result" | grep -o '[0-9]\\+' | head -1)
    if [ -n "$count" ]; then
        echo "COUNT:$count"
        echo "SUCCESS: Found $count resilience test records"
        exit 0
    else
        echo "COUNT:0"
        echo "SUCCESS: No resilience test records found (empty result)"
        exit 0
    fi
else
    echo "COUNT:0"
    echo "FAILED: Could not query resilience test data"
    exit 1
fi
"""

        try:
            exec_cmd = [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                namespace,
                "-c",
                "falkordb-cluster",
                "--",
                "sh",
                "-c",
                read_script,
            ]

            logger.debug(f"Reading resilience test data from {pod_name}")
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=timeout
            )

            # Parse the count from output
            record_count = 0
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("COUNT:"):
                        try:
                            record_count = int(line.split(":")[1])
                            break
                        except (ValueError, IndexError):
                            pass

            return record_count

        except Exception as e:
            logger.error(f"Error reading resilience data from {pod_name}: {e}")
            return 0

    def _write_scaling_test_data_in_cluster(
        self, pod_name, namespace, username, password, phase, node_count, timeout=60
    ):
        """
        Write scaling test data to FalkorDB by running commands inside the cluster pod.

        Args:
            pod_name: Name of the pod to execute in
            namespace: Kubernetes namespace
            username: FalkorDB username
            password: FalkorDB password
            phase: Phase of the scaling test
            node_count: Number of nodes in this phase
            timeout: Timeout in seconds

        Returns:
            bool: True if data was written successfully
        """
        import subprocess

        # Create a script that writes scaling test data using redis-cli
        write_script = f"""#!/bin/bash
set -e

# Write scaling test data using GRAPH.QUERY
if redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY scaling_test "CREATE (:ScalingTest {{phase: '{phase}', nodes: {node_count}}})" >/dev/null 2>&1; then
    echo "SUCCESS: Scaling test data written for phase {phase}"
    exit 0
else
    echo "FAILED: Could not write scaling test data for phase {phase}"
    exit 1
fi
"""

        try:
            exec_cmd = [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                namespace,
                "-c",
                "falkordb-cluster",
                "--",
                "sh",
                "-c",
                write_script,
            ]

            logger.debug(f"Writing scaling test data to {pod_name}")
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=timeout
            )

            if result.returncode == 0 and "SUCCESS" in result.stdout:
                return True
            else:
                logger.error(
                    f"Failed to write scaling data to {pod_name}: {result.stderr}"
                )
                return False

        except Exception as e:
            logger.error(f"Error writing scaling data to {pod_name}: {e}")
            return False

    def _read_scaling_test_data_in_cluster(
        self, pod_name, namespace, username, password, timeout=60
    ):
        """
        Read scaling test data from FalkorDB by running commands inside the cluster pod.

        Args:
            pod_name: Name of the pod to execute in
            namespace: Kubernetes namespace
            username: FalkorDB username
            password: FalkorDB password
            timeout: Timeout in seconds

        Returns:
            int: Number of scaling test records found (0 if failed)
        """
        import subprocess

        # Create a script that reads scaling test data using redis-cli
        read_script = f"""#!/bin/bash
set -e

# Query scaling test data using GRAPH.QUERY with specific phase filter
if result=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY scaling_test "MATCH (n:ScalingTest {{phase: 'initial'}}) RETURN count(n)" 2>/dev/null); then
    # Extract count from the result - redis-cli returns results in a specific format
    count=$(echo "$result" | grep -o '[0-9]\\+' | head -1)
    if [ -n "$count" ]; then
        echo "COUNT:$count"
        echo "SUCCESS: Found $count initial phase scaling test records"
        exit 0
    else
        echo "COUNT:0"
        echo "SUCCESS: No initial phase scaling test records found (empty result)"
        exit 0
    fi
else
    echo "COUNT:0"
    echo "FAILED: Could not query scaling test data"
    exit 1
fi
"""

        try:
            exec_cmd = [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                namespace,
                "-c",
                "falkordb-cluster",
                "--",
                "sh",
                "-c",
                read_script,
            ]

            logger.debug(f"Reading scaling test data from {pod_name}")
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=timeout
            )

            # Parse the count from output
            record_count = 0
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("COUNT:"):
                        try:
                            record_count = int(line.split(":")[1])
                            break
                        except (ValueError, IndexError):
                            pass

            return record_count

        except Exception as e:
            logger.error(f"Error reading scaling data from {pod_name}: {e}")
            return 0

    def _run_performance_test_in_cluster(
        self, pod_name, namespace, username, password, timeout=120
    ):
        """
        Run performance test inside the cluster pod.

        Args:
            pod_name: Name of the pod to execute in
            namespace: Kubernetes namespace
            username: FalkorDB username
            password: FalkorDB password
            timeout: Timeout in seconds

        Returns:
            tuple: (creation_time, query_time, node_count)
        """
        import subprocess

        # Create a script that runs the performance test using redis-cli
        # Note: Using date +%s%3N for milliseconds and Python for arithmetic (bc not available)
        perf_script = f"""#!/bin/bash
set -e

redis-cli -u "redis://{username}:{password}@localhost:6379/" FLUSHALL >/dev/null 2>&1

# Start timing for node creation (nanoseconds for better precision)
start_time=$(date +%s%N)

# Create 100 nodes in a loop
for i in $(seq 0 99); do
    redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY performance_test "CREATE (:PerfTest {{id: $i, batch: 'performance_test'}})" >/dev/null 2>&1
done

creation_end_time=$(date +%s%N)
creation_time=$(python3 -c "print(($creation_end_time - $start_time) / 1000000000.0)")

# Start timing for query
query_start_time=$(date +%s%N)

# Query the count
count_result=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY performance_test "MATCH (n:PerfTest) RETURN count(n)" 2>/dev/null)
count=$(echo "$count_result" | grep -o '[0-9]\\+' | head -1)

query_end_time=$(date +%s%N)
query_time=$(python3 -c "print(($query_end_time - $query_start_time) / 1000000000.0)")

echo "CREATION_TIME:$creation_time"
echo "QUERY_TIME:$query_time"
echo "NODE_COUNT:$count"
echo "SUCCESS: Performance test completed"
"""

        try:
            exec_cmd = [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                namespace,
                "-c",
                "falkordb-cluster",
                "--",
                "sh",
                "-c",
                perf_script,
            ]

            logger.debug(f"Running performance test in {pod_name}")
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=timeout
            )

            # Parse results from output
            creation_time = 0.0
            query_time = 0.0
            node_count = 0

            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("CREATION_TIME:"):
                        try:
                            creation_time = float(line.split(":")[1])
                        except (ValueError, IndexError):
                            pass
                    elif line.startswith("QUERY_TIME:"):
                        try:
                            query_time = float(line.split(":")[1])
                        except (ValueError, IndexError):
                            pass
                    elif line.startswith("NODE_COUNT:"):
                        try:
                            node_count = int(line.split(":")[1])
                        except (ValueError, IndexError):
                            pass
            else:
                logger.error(f"Performance test failed: {result.stderr}")

            return creation_time, query_time, node_count

        except Exception as e:
            logger.error(f"Error running performance test in {pod_name}: {e}")
            return 0.0, 0.0, 0

    def test_cluster_deployment_basic(self, clean_graphs):
        """Test basic cluster deployment and connectivity."""
        cluster_info = clean_graphs
        
        logger.info("Testing basic cluster deployment and connectivity...")

        # Validate FalkorDB connections to cluster nodes
        logger.info("Validating FalkorDB connections to cluster nodes...")
        assert (
            len(cluster_info["pods"]) >= 6
        ), f"Expected at least 6 pods for sharded cluster, got {len(cluster_info['pods'])}"

        connected_nodes = 0
        for pod in cluster_info["pods"]:
            try:
                # Use in-cluster validation to avoid DNS resolution issues
                if validate_falkordb_connection_in_cluster(
                    pod, cluster_info["namespace"], cluster_info["username"], cluster_info["password"]
                ):
                    connected_nodes += 1
                    logger.info(f"Successfully connected to {pod}")
                else:
                    logger.warning(f"Could not connect to {pod}")
            except Exception as e:
                logger.warning(f"Could not connect to {pod}: {e}")

        assert (
            connected_nodes >= 3
        ), f"Expected at least 3 connected nodes, got {connected_nodes}"

        # Validate cluster status using kubectl debug inside a pod
        logger.info("Validating cluster status...")
        cluster_nodes_found = validate_cluster_status(
            cluster_info["pods"][0], cluster_info["namespace"], 
            cluster_info["username"], cluster_info["password"], expected_nodes=6
        )
        assert (
            cluster_nodes_found >= 6
        ), f"Expected at least 6 cluster nodes, got {cluster_nodes_found}"

        logger.info("Cluster deployment test completed successfully")

    def test_cluster_data_distribution(self, clean_graphs):
        """Test data distribution across cluster shards using hash slots."""
        import subprocess
        
        cluster_info = clean_graphs
        
        logger.info("Testing cluster data distribution across shards...")

        # Write multiple test data points to force distribution across shards
        # In cluster mode, keys are distributed based on hash slots
        logger.info("Writing test data that will be distributed across shards...")
        nodes_to_create = 100
        for i in range(nodes_to_create):
            success = self._write_test_data_in_cluster(
                cluster_info["pods"][0],  # Write through first pod
                cluster_info["namespace"],
                cluster_info["username"],
                cluster_info["password"],
                f"distributed-node-{i}",
                f"shard-test-{i}",
            )
            if not success:
                logger.warning(f"Could not write node {i}")

        # Allow time for data distribution
        time.sleep(5)

        # Check which shards actually have data by examining cluster slots
        logger.info("Checking data distribution across shards...")
        
        # Get cluster info to see which shards have data
        cluster_info_script = f"""#!/bin/bash
redis-cli -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" CLUSTER INFO
"""
        
        exec_cmd = [
            "kubectl", "exec", cluster_info["pods"][0],
            "-n", cluster_info["namespace"],
            "-c", "falkordb-cluster",
            "--", "sh", "-c", cluster_info_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info(f"Cluster info:\n{result.stdout}")
        
        # Count how many nodes each pod can see
        logger.info("Verifying data is accessible from all shards...")
        shard_data_counts = {}
        
        for pod in cluster_info["pods"]:
            node_count = self._read_test_data_in_cluster(
                pod, cluster_info["namespace"], 
                cluster_info["username"], cluster_info["password"]
            )
            
            # Extract shard name (e.g., "shard-7xq" from "shared-cluster-shard-7xq-0")
            shard_name = "-".join(pod.split("-")[:-1])
            
            if shard_name not in shard_data_counts:
                shard_data_counts[shard_name] = 0
            shard_data_counts[shard_name] = max(shard_data_counts[shard_name], node_count)
            
            logger.info(f"Pod {pod} can access {node_count} nodes")

        # All pods should see all data (cluster mode makes data globally accessible)
        total_accessible = sum(shard_data_counts.values()) / len(shard_data_counts)
        logger.info(f"Average nodes accessible per shard: {total_accessible}")
        
        # In a cluster, all nodes should be accessible from any shard
        # We expect to see all 100 nodes from each shard
        assert total_accessible >= nodes_to_create * 0.95, \
            f"Expected ~{nodes_to_create} nodes accessible, but got {total_accessible}"
        
        # Verify we have data in multiple shards by checking unique shard names
        unique_shards = len(shard_data_counts)
        logger.info(f"Data distributed across {unique_shards} unique shards")
        assert unique_shards >= 3, f"Expected data in at least 3 shards, got {unique_shards}"
        
        logger.info(f"Data distribution test completed - {nodes_to_create} nodes distributed across {unique_shards} shards")

    def test_cluster_node_failure_resilience(self, clean_graphs):
        """Test cluster resilience to node failures."""
        import subprocess
        
        cluster_info = clean_graphs
        
        logger.info("Testing cluster node failure resilience...")

        # Write initial test data using in-cluster execution
        logger.info("Writing initial test data...")
        initial_data_written = self._write_resilience_test_data_in_cluster(
            cluster_info["pods"][0], cluster_info["namespace"], 
            cluster_info["username"], cluster_info["password"], "initial"
        )
        assert initial_data_written, "Could not write initial test data"
        
        # Verify data is readable before failure
        initial_count = self._read_resilience_test_data_in_cluster(
            cluster_info["pods"][0], cluster_info["namespace"], 
            cluster_info["username"], cluster_info["password"]
        )
        assert initial_count > 0, "Could not read initial test data"
        logger.info(f"Initial data written and verified ({initial_count} records)")

        # Actually delete a pod to test resilience
        failed_pod = cluster_info["pods"][2]  # Delete a secondary pod
        logger.info(f"Deleting pod {failed_pod} to test resilience...")
        delete_result = subprocess.run(
            ["kubectl", "delete", "pod", failed_pod, "-n", cluster_info["namespace"]],
            capture_output=True, text=True
        )
        assert delete_result.returncode == 0, f"Failed to delete pod: {delete_result.stderr}"
        logger.info(f"Pod {failed_pod} deleted successfully")

        # Wait for pod to be recreated and become ready
        logger.info("Waiting for pod to be recreated...")
        time.sleep(30)  # Give time for pod to restart
        
        # Wait for pod to be ready
        max_wait = 120
        start_wait = time.time()
        pod_ready = False
        
        while time.time() - start_wait < max_wait:
            result = subprocess.run(
                ["kubectl", "get", "pod", failed_pod, "-n", cluster_info["namespace"], 
                 "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}"],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip() == "True":
                pod_ready = True
                logger.info(f"Pod {failed_pod} is ready again")
                break
            time.sleep(5)
        
        assert pod_ready, f"Pod {failed_pod} did not become ready within {max_wait}s"

        # Verify cluster is still functional and data is accessible
        logger.info("Verifying cluster functionality after pod recovery...")
        
        # Check that data is still accessible from another pod
        surviving_pod = cluster_info["pods"][0] if cluster_info["pods"][0] != failed_pod else cluster_info["pods"][1]
        post_failure_count = self._read_resilience_test_data_in_cluster(
            surviving_pod, cluster_info["namespace"], 
            cluster_info["username"], cluster_info["password"]
        )
        assert post_failure_count == initial_count, \
            f"Data loss detected: expected {initial_count} records, got {post_failure_count}"
        logger.info(f"Data preserved: {post_failure_count} records still accessible")

        # Verify we can still write new data
        logger.info("Verifying write capability after pod recovery...")
        post_recovery_written = self._write_resilience_test_data_in_cluster(
            surviving_pod,
            cluster_info["namespace"],
            cluster_info["username"],
            cluster_info["password"],
            "after_recovery",
        )
        assert post_recovery_written, "Could not write data after pod recovery"
        logger.info("Cluster resilience test completed successfully")

    def test_cluster_scaling_capability(self, clean_graphs):
        """Test cluster scaling by actually scaling up the cluster."""
        import subprocess
        import json
        
        cluster_info = clean_graphs
        
        logger.info("Testing cluster scaling capabilities...")

        # Get initial pod count and shard configuration
        initial_pod_count = len(cluster_info["pods"])
        logger.info(f"Initial cluster has {initial_pod_count} pods (3 shards × 2 replicas)")

        # Write initial data to verify it persists through scaling
        logger.info("Writing initial data before scaling...")
        initial_data_written = self._write_scaling_test_data_in_cluster(
            cluster_info["pods"][0],
            cluster_info["namespace"],
            cluster_info["username"],
            cluster_info["password"],
            "before_scale",
            initial_pod_count,
        )
        assert initial_data_written, "Could not write initial data"
        
        # Verify initial data is readable
        initial_count = self._read_scaling_test_data_in_cluster(
            cluster_info["pods"][0], cluster_info["namespace"], 
            cluster_info["username"], cluster_info["password"]
        )
        assert initial_count > 0, "Could not read initial data"
        logger.info(f"Initial data verified ({initial_count} records)")

        # Scale the cluster by increasing replicas from 2 to 3
        logger.info("Scaling cluster from 2 to 3 replicas per shard...")
        patch_cmd = [
            "kubectl", "patch", "cluster", cluster_info["name"],
            "-n", cluster_info["namespace"],
            "--type", "json",
            "-p", json.dumps([{
                "op": "replace",
                "path": "/spec/shardings/0/template/replicas",
                "value": 3
            }])
        ]
        
        patch_result = subprocess.run(patch_cmd, capture_output=True, text=True)
        assert patch_result.returncode == 0, f"Failed to scale cluster: {patch_result.stderr}"
        logger.info("Cluster scaling initiated")

        # Wait for new pods to be created and become ready
        logger.info("Waiting for new pods to be created...")
        max_wait = 300  # Increased to 5 minutes for scaling operations
        start_wait = time.time()
        new_pods_ready = False
        
        while time.time() - start_wait < max_wait:
            # Get current pod count
            result = subprocess.run(
                ["kubectl", "get", "pods", "-n", cluster_info["namespace"],
                 "-l", f"app.kubernetes.io/instance={cluster_info['name']}",
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
                
                # We expect 9 pods (3 shards × 3 replicas)
                if len(ready_pods) >= 9:
                    new_pods_ready = True
                    logger.info(f"Scaling complete: {len(ready_pods)} pods ready")
                    break
                else:
                    elapsed = int(time.time() - start_wait)
                    logger.info(f"Waiting for pods... ({len(ready_pods)}/9 ready, {elapsed}s elapsed)")
            
            time.sleep(10)
        
        assert new_pods_ready, f"Cluster did not scale within {max_wait}s"

        # Verify data is still accessible after scaling
        logger.info("Verifying data persistence after scaling...")
        post_scale_count = self._read_scaling_test_data_in_cluster(
            cluster_info["pods"][0], cluster_info["namespace"], 
            cluster_info["username"], cluster_info["password"]
        )
        assert post_scale_count == initial_count, \
            f"Data loss after scaling: expected {initial_count}, got {post_scale_count}"
        logger.info(f"Data preserved: {post_scale_count} records still accessible")

        # Scale back down to original configuration
        logger.info("Scaling cluster back to 2 replicas per shard...")
        scale_down_cmd = [
            "kubectl", "patch", "cluster", cluster_info["name"],
            "-n", cluster_info["namespace"],
            "--type", "json",
            "-p", json.dumps([{
                "op": "replace",
                "path": "/spec/shardings/0/template/replicas",
                "value": 2
            }])
        ]
        
        scale_down_result = subprocess.run(scale_down_cmd, capture_output=True, text=True)
        assert scale_down_result.returncode == 0, f"Failed to scale down: {scale_down_result.stderr}"
        
        # Wait for scale down to complete
        time.sleep(30)
        
        logger.info("Cluster scaling test completed successfully")

    def test_cluster_performance_basic(self, clean_graphs):
        """Test basic cluster performance characteristics."""
        cluster_info = clean_graphs
        
        logger.info("Testing cluster performance characteristics...")

        # Perform basic performance test using in-cluster execution
        logger.info("Performing basic performance test...")
        pod_name = cluster_info["pods"][0]

        creation_time, query_time, node_count = (
            self._run_performance_test_in_cluster(
                pod_name, cluster_info["namespace"], 
                cluster_info["username"], cluster_info["password"]
            )
        )

        # Verify the test results
        assert node_count == 100, f"Expected 100 nodes, got {node_count}"
        logger.info(f"Created 100 nodes in {creation_time:.2f} seconds")
        logger.info(f"Query completed in {query_time:.2f} seconds")

        # Basic performance assertions (very lenient for CI environments)
        assert (
            creation_time < 30
        ), f"Node creation took too long: {creation_time:.2f}s"
        assert query_time < 5, f"Query took too long: {query_time:.2f}s"

        logger.info("Cluster performance test completed successfully")
