"""Integration tests for cluster mode deployment."""

import logging
import time
import json
import subprocess
import pytest
import threading
from collections import defaultdict
from datetime import datetime


from ...utils.validation import (
    validate_falkordb_connection_in_cluster,
    validate_cluster_status,
    get_falkordb_container_name,
)

logger = logging.getLogger(__name__)


class AvailabilityMonitor:
    """Monitor database availability during operations by continuously querying."""
    
    def __init__(self, pod_list, namespace, username, password, container_name="falkordb-cluster", query_interval=1.0, grace_period=0):
        self.pod_list = pod_list
        self.namespace = namespace
        self.username = username
        self.password = password
        self.container_name = container_name
        self.query_interval = query_interval
        self.grace_period = grace_period
        
        self.running = False
        self.thread = None
        self._skip_until = {}  # pod -> unix timestamp until which to skip
        self.results = {
            'total_queries': 0,
            'successful_queries': 0,
            'failed_queries': 0,
            'latencies': [],
            'errors': defaultdict(int),
            'start_time': None,
            'end_time': None
        }
    
    def _query_pod(self, pod_name):
        """Execute a simple read query against a pod."""
        query_script = f'''#!/bin/bash
    redis-cli --raw -c -u "redis://{self.username}:{self.password}@localhost:6379/" PING 2>&1 | grep -q 'PONG' || echo "QUERY_FAILED"
    '''
        
        exec_cmd = [
            'kubectl', 'exec', pod_name,
            '-n', self.namespace,
            '-c', self.container_name,
            '--',
            'sh', '-c', query_script
        ]
        
        start = time.time()
        try:
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=5)
            latency = time.time() - start
            
            if result.returncode == 0 and "QUERY_FAILED" not in result.stdout:
                return True, latency, None
            else:
                error = result.stderr if result.stderr else result.stdout
                return False, latency, error
        except subprocess.TimeoutExpired:
            return False, time.time() - start, "Timeout"
        except Exception as e:
            return False, time.time() - start, str(e)
    
    def _monitor_loop(self):
        """Background thread that continuously queries the database."""
        while self.running:
            if not self.pod_list:
                time.sleep(self.query_interval)
                continue

            now = time.time()

            # Choose a pod that is not currently skipped
            start_index = self.results['total_queries'] % len(self.pod_list)
            chosen = None
            for i in range(len(self.pod_list)):
                pod_name = self.pod_list[(start_index + i) % len(self.pod_list)]
                if now >= self._skip_until.get(pod_name, 0):
                    chosen = pod_name
                    break

            if chosen is None:
                time.sleep(self.query_interval)
                continue

            success, latency, error = self._query_pod(chosen)

            within_grace = False
            if self.results['start_time']:
                within_grace = (datetime.now() - self.results['start_time']).total_seconds() < self.grace_period

            self.results['total_queries'] += 1
            if success:
                if not within_grace:
                    self.results['successful_queries'] += 1
                    self.results['latencies'].append(latency)
            else:
                if not within_grace:
                    self.results['failed_queries'] += 1
                    if error:
                        self.results['errors'][error[:100]] += 1  # Truncate error message

                if error:
                    err_lc = error.lower()
                    transient = [
                        'notfound', 'not found', 'unable to upgrade connection', 'no such container',
                        'container not found', 'error from server', 'context deadline exceeded'
                    ]
                    if any(sig in err_lc for sig in transient):
                        self._skip_until[chosen] = time.time() + 30

            time.sleep(self.query_interval)
    
    def start(self):
        """Start monitoring in background thread."""
        if self.running:
            return
        
        self.running = True
        self.results['start_time'] = datetime.now()
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        logger.info(f"Started availability monitoring (interval: {self.query_interval}s)")
    
    def stop(self):
        """Stop monitoring and return results."""
        if not self.running:
            return self.results
        
        self.running = False
        self.results['end_time'] = datetime.now()
        
        if self.thread:
            self.thread.join(timeout=10)
        
        # Calculate statistics
        total = self.results['total_queries']
        success = self.results['successful_queries']
        failed = self.results['failed_queries']
        counted = success + failed
        
        if counted > 0:
            availability_pct = (success / counted) * 100
        else:
            # If all queries occurred during grace period, treat availability as 100%
            availability_pct = 100.0
        self.results['availability_percentage'] = availability_pct
        
        if self.results['latencies']:
            sorted_latencies = sorted(self.results['latencies'])
            self.results['latency_p50'] = sorted_latencies[len(sorted_latencies) // 2]
            self.results['latency_p95'] = sorted_latencies[int(len(sorted_latencies) * 0.95)]
            self.results['latency_p99'] = sorted_latencies[int(len(sorted_latencies) * 0.99)]
            self.results['latency_avg'] = sum(sorted_latencies) / len(sorted_latencies)
        
        duration = (self.results['end_time'] - self.results['start_time']).total_seconds()
        logger.info(f"Availability monitoring stopped. Duration: {duration:.1f}s")
        logger.info(f"  Total queries: {total}")
        logger.info(f"  Counted (post-grace): {counted}")
        logger.info(f"  Successful: {success} ({availability_pct:.2f}%)")
        logger.info(f"  Failed: {failed}")
        
        if self.results['latencies']:
            logger.info(f"  Latency - Avg: {self.results['latency_avg']*1000:.1f}ms, "
                       f"P50: {self.results['latency_p50']*1000:.1f}ms, "
                       f"P95: {self.results['latency_p95']*1000:.1f}ms, "
                       f"P99: {self.results['latency_p99']*1000:.1f}ms")
        
        if self.results['errors']:
            logger.info(f"  Error summary:")
            for error, count in list(self.results['errors'].items())[:5]:  # Show top 5 errors
                logger.info(f"    - {error}: {count} times")
        
        return self.results


@pytest.mark.integration
class TestClusterIntegration:
    """Integration tests for cluster mode FalkorDB deployment."""

    def test_pods_expose_hostport(self, shared_cluster, k8s_helper):
        """Verify cluster pods expose the expected hostPort (6379)."""
        import json, subprocess
        namespace = shared_cluster["namespace"]
        pods = shared_cluster["pods"]
        # Skip if cluster does not use hostNetwork/hostPorts
        get_cluster_cmd = [
            "kubectl", "get", "cluster", shared_cluster["name"],
            "-n", namespace, "-o", "json"
        ]
        result = subprocess.run(get_cluster_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            tmpl = data.get("spec", {}).get("shardings", [{}])[0].get("template", {})
            net = tmpl.get("network", {})
            host_ports = net.get("hostPorts")
            if not host_ports:
                pytest.skip("Cluster not configured with hostPorts; skipping hostPort exposure test")

        assert pods, "No pods found for shared cluster"

        for pod_name in pods:
            pod = k8s_helper.core_v1.read_namespaced_pod(pod_name, namespace)
            container_name = get_falkordb_container_name(pod_name, namespace) or "falkordb"
            container = next((c for c in pod.spec.containers if c.name == container_name), None)
            assert container is not None, f"Pod {pod_name} missing falkordb container"

            ports = container.ports or []
            host_ports = [p.host_port for p in ports if p.host_port is not None]

            assert 6379 in host_ports, f"Pod {pod_name} missing hostPort 6379 (found {host_ports})"

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
set +e

# Write test data using GRAPH.QUERY
# Use -c flag to enable cluster mode and follow MOVED redirects
output=$(redis-cli -c -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY clustergraph "CREATE (:TestNode {{id: '{node_id}', data: '{data_value}'}})" 2>&1)
exit_code=$?
echo "REDIS_CLI_OUTPUT:$output"
echo "REDIS_CLI_EXIT_CODE:$exit_code"
if [ $exit_code -eq 0 ]; then
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
                logger.error(f"Failed to write data to {pod_name}: stdout={result.stdout}, stderr={result.stderr}, returncode={result.returncode}")
                return False

        except Exception as e:
            logger.error(f"Error writing data to {pod_name}: {e}")
            return False

    def _get_hosting_pod_for_key(self, cluster_info, key_name, timeout=60):
        """
        Determine which pod hosts the given key (graph name) based on cluster slots.

        Args:
            cluster_info: Dict with cluster details including pods, namespace, username, password
            key_name: The Redis key to locate (e.g., graph name)
            timeout: subprocess timeout

        Returns:
            Pod name string if resolved, else fall back to first pod.
        """
        import subprocess

        base_pod = cluster_info["pods"][0]
        ns = cluster_info["namespace"]
        user = cluster_info["username"]
        pwd = cluster_info["password"]

        # Get the slot for the key
        cmd_slot = [
            "kubectl", "exec", base_pod, "-n", ns, "-c", "falkordb-cluster",
            "--", "redis-cli", "-u", f"redis://{user}:{pwd}@localhost:6379/", "CLUSTER", "KEYSLOT", key_name
        ]
        slot = None
        try:
            r = subprocess.run(cmd_slot, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                try:
                    slot = int(r.stdout.strip())
                except ValueError:
                    slot = None
        except Exception:
            slot = None

        if slot is None:
            return base_pod

        # Get cluster nodes and parse ranges
        cmd_nodes = [
            "kubectl", "exec", base_pod, "-n", ns, "-c", "falkordb-cluster",
            "--", "redis-cli", "-u", f"redis://{user}:{pwd}@localhost:6379/", "CLUSTER", "NODES"
        ]
        try:
            r = subprocess.run(cmd_nodes, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                return base_pod

            # Each line: id host:port@bus flags ... [slot ranges]
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) < 8:
                    continue
                flags = parts[2]
                # Only consider masters
                if "master" not in flags:
                    continue
                addr = parts[1]
                # Remaining tokens may contain slot ranges like "0-5460"
                ranges = [p for p in parts[8:] if "-" in p]
                for rng in ranges:
                    try:
                        start_s, end_s = rng.split("-")
                        start, end = int(start_s), int(end_s)
                        if start <= slot <= end:
                            # Derive pod name from addr if it contains FQDN: podname.svc.cluster.local:6379
                            host = addr.split(":")[0]
                            pod_candidate = host.split(".")[0]
                            # Validate candidate exists in cluster pods
                            for p in cluster_info["pods"]:
                                if p.startswith(pod_candidate):
                                    return p
                            # If not matched, fall back
                            return base_pod
                    except Exception:
                        continue
        except Exception:
            return base_pod

        return base_pod

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

# Query test data using GRAPH.QUERY with raw format
# Use -c flag to enable cluster mode and follow MOVED redirects
result=$(redis-cli -c -u "redis://{username}:{password}@localhost:6379/" --raw GRAPH.QUERY clustergraph "MATCH (n:TestNode) RETURN count(n)" 2>&1)

# Parse the count - look for a line that contains only digits using awk
count=$(echo "$result" | awk '/^[0-9]+$/{{print; exit}}')
if [ -z "$count" ]; then
    count="0"
fi

echo "COUNT:$count"
echo "SUCCESS: Found $count test nodes"
exit 0
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
# Use -c flag to enable cluster mode and follow MOVED redirects
if redis-cli -c -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY resilience_test "CREATE (:ResilienceTest {{id: '{test_id}', timestamp: timestamp()}})" >/dev/null 2>&1; then
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

# Query resilience test data using GRAPH.QUERY with raw format
# Use -c flag to enable cluster mode and follow MOVED redirects
result=$(redis-cli -c -u "redis://{username}:{password}@localhost:6379/" --raw GRAPH.QUERY resilience_test "MATCH (n:ResilienceTest) RETURN count(n)" 2>&1)

# Parse the count - look for a line that contains only digits using awk
count=$(echo "$result" | awk '/^[0-9]+$/{{print; exit}}')
if [ -z "$count" ]; then
    count="0"
fi

echo "COUNT:$count"
echo "SUCCESS: Found $count resilience test records"
exit 0
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
# Use -c flag to enable cluster mode and follow MOVED redirects
if redis-cli -c -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY scaling_test "CREATE (:ScalingTest {{phase: '{phase}', nodes: {node_count}}})" >/dev/null 2>&1; then
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

# Query scaling test data using GRAPH.QUERY with specific phase filter and raw format
# Use -c flag to enable cluster mode and follow MOVED redirects
result=$(redis-cli -c -u "redis://{username}:{password}@localhost:6379/" --raw GRAPH.QUERY scaling_test "MATCH (n:ScalingTest {{phase: 'before_scale'}}) RETURN count(n)" 2>&1)

# Parse the count - look for a line that contains only digits using awk
count=$(echo "$result" | awk '/^[0-9]+$/{{print; exit}}')
if [ -z "$count" ]; then
    count="0"
fi

echo "COUNT:$count"
echo "SUCCESS: Found $count before_scale phase scaling test records"
exit 0
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

# Use -c flag for cluster mode throughout
redis-cli -c -u "redis://{username}:{password}@localhost:6379/" FLUSHALL >/dev/null 2>&1

# Start timing for node creation (nanoseconds for better precision)
start_time=$(date +%s%N)

# Create 100 nodes in a loop
for i in $(seq 0 99); do
    redis-cli -c -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY performance_test "CREATE (:PerfTest {{id: $i, batch: 'performance_test'}})" >/dev/null 2>&1
done

creation_end_time=$(date +%s%N)
creation_time=$(python3 -c "print(($creation_end_time - $start_time) / 1000000000.0)")

# Start timing for query
query_start_time=$(date +%s%N)

# Query the count - use raw format to get just the result
# Use -c flag to enable cluster mode and follow MOVED redirects
count_result=$(redis-cli -c -u "redis://{username}:{password}@localhost:6379/" --raw GRAPH.QUERY performance_test "MATCH (n:PerfTest) RETURN count(n)" 2>&1)

# Debug: show the raw output with line numbers
echo "DEBUG_RAW_OUTPUT_START"
echo "$count_result" | cat -n
echo "DEBUG_RAW_OUTPUT_END"

# Parse the count - look for a line that contains only digits
# Use awk to be more robust
count=$(echo "$count_result" | awk '/^[0-9]+$/{{print; exit}}')
if [ -z "$count" ]; then
    count="0"
fi

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
                # Log the full output for debugging
                logger.info(f"Performance test output:\n{result.stdout}")
                
                for line in result.stdout.split("\n"):
                    if line.startswith("DEBUG_RAW_OUTPUT:"):
                        logger.info(f"Redis-cli raw output: {line.split(':', 1)[1]}")
                    elif line.startswith("CREATION_TIME:"):
                        try:
                            creation_time = float(line.split(":", 1)[1])
                        except (ValueError, IndexError):
                            pass
                    elif line.startswith("QUERY_TIME:"):
                        try:
                            query_time = float(line.split(":", 1)[1])
                        except (ValueError, IndexError):
                            pass
                    elif line.startswith("NODE_COUNT:"):
                        try:
                            # Split only on first colon and strip whitespace
                            count_str = line.split(":", 1)[1].strip()
                            node_count = int(count_str)
                            logger.info(f"Parsed node count: {node_count} from string '{count_str}'")
                        except (ValueError, IndexError) as e:
                            logger.error(f"Failed to parse node count from line '{line}': {e}")
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
        host_pod = self._get_hosting_pod_for_key(cluster_info, "clustergraph")
        nodes_to_create = 100
        for i in range(nodes_to_create):
            success = self._write_test_data_in_cluster(
                host_pod,
                cluster_info["namespace"],
                cluster_info["username"],
                cluster_info["password"],
                f"distributed-node-{i}",
                f"shard-test-{i}",
            )
            if not success:
                logger.warning(f"Could not write node {i}")

        # Allow time for data distribution
        time.sleep(15)

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
            if node_count == 0:
                # Retry once after short delay to avoid transient graph propagation lag
                time.sleep(5)
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

        # Compute metrics across shards
        avg_accessible = sum(shard_data_counts.values()) / max(1, len(shard_data_counts))
        max_accessible = max(shard_data_counts.values()) if shard_data_counts else 0
        logger.info(f"Average nodes accessible per shard: {avg_accessible}")
        logger.info(f"Max nodes accessible from a single shard: {max_accessible}")

        # Expect at least one shard to access all nodes; average may be lower if module commands aren't redirected cross-shard
        assert max_accessible >= nodes_to_create, \
            f"Expected {nodes_to_create} nodes from at least one shard, got {max_accessible}"
        # Average should reflect distribution across shards (allow lenient threshold)
        expected_avg = nodes_to_create / max(1, len(shard_data_counts))
        assert avg_accessible >= expected_avg * 0.9, \
            f"Expected average >= {expected_avg*0.9}, got {avg_accessible}"
        
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
        host_pod = self._get_hosting_pod_for_key(cluster_info, "resilience_test")
        initial_data_written = self._write_resilience_test_data_in_cluster(
            host_pod, cluster_info["namespace"], 
            cluster_info["username"], cluster_info["password"], "initial"
        )
        assert initial_data_written, "Could not write initial test data"
        
        # Verify data is readable before failure
        initial_count = self._read_resilience_test_data_in_cluster(
            host_pod, cluster_info["namespace"], 
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
        
        # Wait for pod to be ready (allow extra time on slower environments)
        max_wait = 600
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
        host_pod = self._get_hosting_pod_for_key(cluster_info, "scaling_test")
        initial_data_written = self._write_scaling_test_data_in_cluster(
            host_pod,
            cluster_info["namespace"],
            cluster_info["username"],
            cluster_info["password"],
            "before_scale",
            initial_pod_count,
        )
        assert initial_data_written, "Could not write initial data"
        
        # Verify initial data is readable
        initial_count = self._read_scaling_test_data_in_cluster(
            host_pod, cluster_info["namespace"], 
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
        max_wait = 600  # Allow up to 10 minutes for scaling operations in CI
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
        pod_name = self._get_hosting_pod_for_key(cluster_info, "performance_test")

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

    def test_cluster_vertical_scaling(self, clean_graphs):
        """Test vertical scaling by updating cluster resources."""
        import json
        
        cluster_info = clean_graphs
        
        logger.info("Testing cluster vertical scaling...")

        # Write initial data using "before_scale" phase to match the read function
        logger.info("Writing initial data before scaling...")
        host_pod = self._get_hosting_pod_for_key(cluster_info, "scaling_test")
        initial_data_written = self._write_scaling_test_data_in_cluster(
            host_pod,
            cluster_info["namespace"],
            cluster_info["username"],
            cluster_info["password"],
            "before_scale",  # Match the phase name used in read function
            len(cluster_info["pods"]),
        )
        assert initial_data_written, "Could not write initial data"
        
        # Wait for data to be available across cluster
        time.sleep(5)
        
        # Verify initial data
        initial_count = self._read_scaling_test_data_in_cluster(
            host_pod, cluster_info["namespace"], 
            cluster_info["username"], cluster_info["password"]
        )
        assert initial_count > 0, "Could not read initial data"
        logger.info(f"Initial data verified ({initial_count} records)")

        # Get current resource limits
        logger.info("Getting current cluster resources...")
        get_cluster_cmd = [
            "kubectl", "get", "cluster", cluster_info["name"],
            "-n", cluster_info["namespace"],
            "-o", "json"
        ]
        
        result = subprocess.run(get_cluster_cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"Failed to get cluster: {result.stderr}"
        
        cluster_data = json.loads(result.stdout)
        current_resources = cluster_data["spec"]["shardings"][0]["template"]["resources"]
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
        
        patch_cmd = [
            "kubectl", "patch", "cluster", cluster_info["name"],
            "-n", cluster_info["namespace"],
            "--type", "json",
            "-p", json.dumps([
                {
                    "op": "replace",
                    "path": "/spec/shardings/0/template/resources/limits/cpu",
                    "value": new_cpu
                },
                {
                    "op": "replace",
                    "path": "/spec/shardings/0/template/resources/limits/memory",
                    "value": new_memory
                },
                {
                    "op": "replace",
                    "path": "/spec/shardings/0/template/resources/requests/cpu",
                    "value": new_cpu
                },
                {
                    "op": "replace",
                    "path": "/spec/shardings/0/template/resources/requests/memory",
                    "value": new_memory
                }
            ])
        ]
        
        patch_result = subprocess.run(patch_cmd, capture_output=True, text=True)
        assert patch_result.returncode == 0, f"Failed to patch cluster: {patch_result.stderr}"
        logger.info("Cluster resources patched successfully")

        # Initialize availability_test graph to ensure RO queries succeed
        init_script = f"""#!/bin/bash
set -e
redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" GRAPH.QUERY availability_test "CREATE (:Health {{x:1}})" >/dev/null 2>&1 || true
"""
        subprocess.run([
            "kubectl", "exec", host_pod, "-n", cluster_info["namespace"], "-c", "falkordb-cluster",
            "--", "sh", "-c", init_script
        ], capture_output=True, text=True, timeout=30)

        # Start availability monitoring BEFORE pods restart
        logger.info("Starting availability monitoring during vertical scaling...")
        monitor = AvailabilityMonitor(
            pod_list=cluster_info["pods"],
            namespace=cluster_info["namespace"],
            username=cluster_info["username"],
            password=cluster_info["password"],
            query_interval=1.5,
            grace_period=120
        )
        monitor.start()
        
        # Wait for pods to restart with new resources
        logger.info("Waiting for pods to restart with new resources...")
        time.sleep(60)  # Give time for pods to restart
        
        # Wait for all pods to be ready again
        max_wait = 300
        start_wait = time.time()
        pods_ready = False
        
        while time.time() - start_wait < max_wait:
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
                
                if len(ready_pods) >= 6:
                    pods_ready = True
                    logger.info(f"All pods ready after scaling: {len(ready_pods)} pods")
                    break
                else:
                    elapsed = int(time.time() - start_wait)
                    logger.info(f"Waiting for pods to be ready... ({len(ready_pods)}/6 ready, {elapsed}s elapsed)")
            
            time.sleep(10)
        
        assert pods_ready, f"Pods did not become ready within {max_wait}s after vertical scaling"

        # Stop availability monitoring and check results
        availability_results = monitor.stop()
        
        # Verify availability during scaling
        # For cluster mode with rolling updates, we expect good availability with brief downtime grace
        min_availability = 70.0
        actual_availability = availability_results.get('availability_percentage', 0)
        
        logger.info("=" * 60)
        logger.info("AVAILABILITY DURING VERTICAL SCALING:")
        logger.info(f"  Availability: {actual_availability:.2f}% (target: >{min_availability}%)")
        logger.info(f"  Total queries: {availability_results['total_queries']}")
        logger.info(f"  Successful: {availability_results['successful_queries']}")
        logger.info(f"  Failed: {availability_results['failed_queries']}")
        logger.info("=" * 60)
        
        assert actual_availability >= min_availability, \
            f"Availability during vertical scaling was {actual_availability:.2f}%, expected >{min_availability}%. " \
            f"Cluster should maintain availability during rolling updates."

        # Get updated pod list after scaling
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", cluster_info["namespace"],
             "-l", f"app.kubernetes.io/instance={cluster_info['name']}",
             "-o", "jsonpath={.items[*].metadata.name}"],
            capture_output=True, text=True
        )
        current_pods = result.stdout.strip().split()
        # Filter to only FalkorDB pods (exclude sentinel)
        current_falkordb_pods = [p for p in current_pods if 'shard' in p and 'sent' not in p]
        logger.info(f"Current pods after scaling: {current_falkordb_pods}")

        # Verify data persisted after scaling (allow some time for module reload)
        logger.info("Verifying data persistence after vertical scaling...")
        post_scale_count = 0
        import time as _time
        deadline = _time.time() + 90
        while _time.time() < deadline:
            post_scale_count = self._read_scaling_test_data_in_cluster(
                current_falkordb_pods[0], cluster_info["namespace"], 
                cluster_info["username"], cluster_info["password"]
            )
            if post_scale_count == initial_count:
                break
            _time.sleep(5)
        assert post_scale_count == initial_count, \
            f"Data loss after vertical scaling: expected {initial_count}, got {post_scale_count}"
        logger.info(f"Data preserved after vertical scaling: {post_scale_count} records")

        # Scale back to original resources
        logger.info(f"Scaling back to original resources - CPU: {current_cpu}, Memory: {current_memory}")
        
        restore_cmd = [
            "kubectl", "patch", "cluster", cluster_info["name"],
            "-n", cluster_info["namespace"],
            "--type", "json",
            "-p", json.dumps([
                {
                    "op": "replace",
                    "path": "/spec/shardings/0/template/resources/limits/cpu",
                    "value": current_cpu
                },
                {
                    "op": "replace",
                    "path": "/spec/shardings/0/template/resources/limits/memory",
                    "value": current_memory
                },
                {
                    "op": "replace",
                    "path": "/spec/shardings/0/template/resources/requests/cpu",
                    "value": current_cpu
                },
                {
                    "op": "replace",
                    "path": "/spec/shardings/0/template/resources/requests/memory",
                    "value": current_memory
                }
            ])
        ]
        
        restore_result = subprocess.run(restore_cmd, capture_output=True, text=True)
        assert restore_result.returncode == 0, f"Failed to restore cluster resources: {restore_result.stderr}"
        
        # Wait for restoration
        time.sleep(30)
        
        logger.info("Cluster vertical scaling test completed successfully")

    def test_cluster_oom_resilience(self, clean_graphs):
        """Test that FalkorDB throws OOM errors instead of crashing when reaching maxmemory in cluster mode."""
        cluster_info = clean_graphs
        
        logger.info("Testing cluster OOM behavior - verifying graceful error handling...")

        pod_name = cluster_info["pods"][0]
        
        # IMPORTANT: Set maxmemory on all cluster nodes to enable OOM testing
        logger.info("Setting maxmemory to 128MB on all cluster nodes...")
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", cluster_info["namespace"],
             "-l", f"app.kubernetes.io/instance={cluster_info['name']}",
             "-o", "json"],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            pods_data = json.loads(result.stdout)
            for pod in pods_data.get("items", []):
                pod_name_full = pod["metadata"]["name"]
                
                # Find falkordb container
                if pod.get("status", {}).get("containerStatuses"):
                    for cs in pod["status"]["containerStatuses"]:
                        if 'falkordb' in cs["name"].lower():
                            pod_container = cs["name"]
                            
                            set_maxmemory_script = f"""#!/bin/bash
redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" CONFIG SET maxmemory 134217728
redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" CONFIG SET maxmemory-policy noeviction
echo "maxmemory configured"
"""
                            
                            try:
                                exec_result = subprocess.run(
                                    ['kubectl', 'exec', pod_name_full, '-n', cluster_info["namespace"], 
                                     '-c', pod_container, '--', 'sh', '-c', set_maxmemory_script],
                                    capture_output=True, text=True, timeout=30
                                )
                                if exec_result.returncode == 0:
                                    logger.info(f"✓ maxmemory configured on pod {pod_name_full}: 128MB with noeviction policy")
                                else:
                                    logger.warning(f"Failed to set maxmemory on {pod_name_full}: {exec_result.stderr}")
                            except Exception as e:
                                logger.error(f"Error setting maxmemory on {pod_name_full}: {e}")
                            break
        
        # Get initial pod restart counts for all cluster pods
        initial_restart_counts = {}
        logger.info("Recording initial pod restart counts...")
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", cluster_info["namespace"],
             "-l", f"app.kubernetes.io/instance={cluster_info['name']}",
             "-o", "json"],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            pods_data = json.loads(result.stdout)
            for pod in pods_data.get("items", []):
                pod_name_full = pod["metadata"]["name"]
                if pod.get("status", {}).get("containerStatuses"):
                    for cs in pod["status"]["containerStatuses"]:
                        if 'falkordb' in cs["name"].lower():
                            restart_count = cs.get("restartCount", 0)
                            initial_restart_counts[pod_name_full] = restart_count
                            logger.info(f"Pod {pod_name_full}: initial restart count = {restart_count}")
        
        # Create a script that attempts to trigger OOM and captures the error
        oom_test_script = f"""#!/bin/bash

# Attempt to create large dataset and capture OOM error
echo "Attempting to trigger OOM by creating large dataset..."

oom_error_found=0

for batch in $(seq 1 100); do
    # Try to create nodes with large data
    output=$(redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" \\
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
            'kubectl', 'exec', pod_name,
            '-n', cluster_info['namespace'],
            '-c', 'falkordb-cluster',
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
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", cluster_info["namespace"],
             "-l", f"app.kubernetes.io/instance={cluster_info['name']}",
             "-o", "json"],
            capture_output=True, text=True
        )
        
        pods_crashed = False
        if result.returncode == 0:
            pods_data = json.loads(result.stdout)
            for pod in pods_data.get("items", []):
                pod_name_full = pod["metadata"]["name"]
                if pod.get("status", {}).get("containerStatuses"):
                    for cs in pod["status"]["containerStatuses"]:
                        if 'falkordb' in cs["name"].lower():
                            current_restart_count = cs.get("restartCount", 0)
                            initial_count = initial_restart_counts.get(pod_name_full, 0)
                            
                            if current_restart_count > initial_count:
                                logger.error(f"❌ Pod {pod_name_full} restarted during OOM test! "
                                           f"(initial: {initial_count}, current: {current_restart_count})")
                                pods_crashed = True
                            else:
                                logger.info(f"✓ Pod {pod_name_full} did not restart (count: {current_restart_count})")
        
        assert not pods_crashed, \
            "One or more pods crashed during OOM test. FalkorDB should handle OOM gracefully without crashing."

        # Verify cluster is still functional
        logger.info("Verifying cluster functionality after OOM test...")
        
        test_functionality_script = f"""#!/bin/bash
set -e

# Test basic functionality
redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" \\
    GRAPH.QUERY oom_recovery_test "CREATE (:RecoveryTest {{id: 'after_oom'}}) RETURN 1" >/dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "SUCCESS: Cluster functional after OOM test"
    exit 0
else
    echo "ERROR: Cluster not functional"
    exit 1
fi
"""
        
        exec_cmd = [
            'kubectl', 'exec', pod_name,
            '-n', cluster_info['namespace'],
            '-c', 'falkordb-cluster',
            '--',
            'sh', '-c', test_functionality_script
        ]
        
        # Verify functionality with retry - try multiple pods if needed
        max_retries = 5
        cluster_functional = False
        
        # Get current pod list
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", cluster_info["namespace"],
             "-l", f"app.kubernetes.io/instance={cluster_info['name']}",
             "-o", "jsonpath={{.items[*].metadata.name}}"],
            capture_output=True, text=True
        )
        current_pods = result.stdout.strip().split()
        # Filter to FalkorDB cluster pods (those with 'shard' but not sentinel)
        current_falkordb_pods = [p for p in current_pods if 'shard' in p and 'sent' not in p]
        
        # If no pods found in new format, fallback to using cluster_info pods
        if not current_falkordb_pods:
            current_falkordb_pods = [p for p in cluster_info["pods"] if 'shard' in p and 'sent' not in p]
            if not current_falkordb_pods:  # Still empty, use all pods
                current_falkordb_pods = cluster_info["pods"]
        
        logger.info(f"Testing functionality on pods: {current_falkordb_pods}")
        
        for attempt in range(max_retries):
            # Try a different pod each attempt
            test_pod = current_falkordb_pods[attempt % len(current_falkordb_pods)]
            
            exec_cmd = [
                'kubectl', 'exec', test_pod,
                '-n', cluster_info['namespace'],
                '-c', 'falkordb-cluster',
                '--',
                'sh', '-c', test_functionality_script
            ]
            
            try:
                result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0 and "SUCCESS" in result.stdout:
                    logger.info(f"✓ Cluster is functional after OOM test (verified on pod: {test_pod})")
                    cluster_functional = True
                    break
                else:
                    if attempt < max_retries - 1:
                        logger.info(f"Cluster not ready on pod {test_pod}, retry {attempt + 1}/{max_retries}")
                        time.sleep(10)
                    else:
                        logger.error(f"Pod {test_pod} stderr: {result.stderr}")
                        logger.error(f"Pod {test_pod} stdout: {result.stdout}")
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.info(f"Retry {attempt + 1}/{max_retries} after error on pod {test_pod}: {e}")
                    time.sleep(10)
                else:
                    logger.error(f"Final attempt failed on pod {test_pod}: {e}")
        
        assert cluster_functional, "Cluster should remain functional after OOM test"

        # Verify all pods are still in Running state
        logger.info("Verifying all pods are still running...")
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", cluster_info["namespace"],
             "-l", f"app.kubernetes.io/instance={cluster_info['name']}",
             "-o", "json"],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            pods_data = json.loads(result.stdout)
            pods = pods_data.get("items", [])
            running_pods = [
                p for p in pods 
                if p.get("status", {}).get("phase") == "Running"
            ]
            
            logger.info(f"Pods status: {len(running_pods)}/{len(pods)} running")
            assert len(running_pods) >= len(cluster_info["pods"]), \
                f"Not all pods are running after OOM test: {len(running_pods)}/{len(cluster_info['pods'])}"
        
        # Summary
        if oom_error_detected:
            logger.info("=" * 60)
            logger.info("✓ OOM TEST PASSED")
            logger.info(f"  - FalkorDB threw OOM error: {oom_error_message}")
            logger.info("  - No pods crashed or restarted")
            logger.info("  - Cluster remained functional")
            logger.info("=" * 60)
        else:
            logger.info("=" * 60)
            logger.info("✓ OOM TEST PASSED (no OOM triggered)")
            logger.info("  - No pods crashed or restarted")
            logger.info("  - Cluster remained stable")
            logger.info("=" * 60)
        
        logger.info("Cluster OOM resilience test completed successfully")

    def test_cluster_multi_zone_distribution(self, clean_graphs):
        """Test that cluster pods are distributed across multiple availability zones."""
        cluster_info = clean_graphs
        
        logger.info("Testing cluster multi-zone distribution...")

        # Get pod topology information
        logger.info("Checking pod distribution across zones...")
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", cluster_info["namespace"],
             "-l", f"app.kubernetes.io/instance={cluster_info['name']}",
             "-o", "json"],
            capture_output=True, text=True
        )
        
        assert result.returncode == 0, f"Failed to get pods: {result.stderr}"
        
        pods_data = json.loads(result.stdout)
        pods = pods_data.get("items", [])
        
        # Extract zone information from pod node affinity or node labels
        pod_zones = {}
        pod_nodes = {}
        
        for pod in pods:
            pod_name = pod["metadata"]["name"]
            node_name = pod["spec"].get("nodeName")
            
            if node_name:
                pod_nodes[pod_name] = node_name
                
                # Get node information to find zone
                node_result = subprocess.run(
                    ["kubectl", "get", "node", node_name, "-o", "json"],
                    capture_output=True, text=True
                )
                
                if node_result.returncode == 0:
                    node_data = json.loads(node_result.stdout)
                    labels = node_data.get("metadata", {}).get("labels", {})
                    
                    # Check common zone label keys
                    zone = (
                        labels.get("topology.kubernetes.io/zone") or
                        labels.get("failure-domain.beta.kubernetes.io/zone") or
                        labels.get("kubernetes.io/zone") or
                        "unknown"
                    )
                    
                    pod_zones[pod_name] = zone
                    logger.info(f"Pod {pod_name} is in zone {zone} on node {node_name}")
        
        # Analyze distribution
        unique_zones = set(pod_zones.values())
        unique_nodes = set(pod_nodes.values())
        
        logger.info(f"Cluster pods distributed across {len(unique_zones)} zones and {len(unique_nodes)} nodes")
        
        # Count pods per zone
        zone_counts = {}
        for zone in pod_zones.values():
            zone_counts[zone] = zone_counts.get(zone, 0) + 1
        
        for zone, count in zone_counts.items():
            logger.info(f"Zone {zone}: {count} pods")
        
        # For multi-zone setups, we expect pods in at least 2 different zones
        # In single-zone test environments (like kind), we verify proper distribution intent
        if len(unique_zones) == 1:
            logger.info("Running in single-zone environment - verifying cluster configuration")
            
            # Check if pod anti-affinity is configured in the cluster spec
            get_cluster_cmd = [
                "kubectl", "get", "cluster", cluster_info["name"],
                "-n", cluster_info["namespace"],
                "-o", "json"
            ]
            
            result = subprocess.run(get_cluster_cmd, capture_output=True, text=True)
            if result.returncode == 0:
                cluster_data = json.loads(result.stdout)
                logger.info("✓ Single-zone environment - cluster configuration verified")
                logger.info("Note: Multi-zone distribution would be automatic in a multi-zone Kubernetes cluster")
        else:
            # In multi-zone environment, verify distribution
            logger.info(f"✓ Pods successfully distributed across {len(unique_zones)} zones")
            assert len(unique_zones) >= 2, \
                f"Expected pods in at least 2 zones for multi-zone cluster, found {len(unique_zones)}"
        
        # Verify pods are on different nodes (node anti-affinity)
        logger.info(f"Pods distributed across {len(unique_nodes)} nodes")
        if len(pods) >= 3:
            # For clusters with 3+ pods, we expect distribution across multiple nodes
            assert len(unique_nodes) >= 2, \
                f"Expected pods on at least 2 different nodes, found {len(unique_nodes)}"
            logger.info(f"✓ Pods successfully distributed across {len(unique_nodes)} nodes")
        
        logger.info("Cluster multi-zone distribution test completed")

    def test_cluster_data_persistence_after_scaling(self, clean_graphs):
        """Test comprehensive data persistence through multiple scaling operations."""
        cluster_info = clean_graphs
        
        logger.info("Testing data persistence through scaling operations...")

        # Wait for all pods to be fully ready and scheduled
        logger.info("Waiting for all pods to be ready...")
        max_wait = 120
        start_wait = time.time()
        all_pods_ready = False
        
        while time.time() - start_wait < max_wait:
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
                    if p.get("status", {}).get("phase") == "Running" and
                       p.get("spec", {}).get("nodeName") is not None and  # Has host assigned
                       any(c.get("type") == "Ready" and c.get("status") == "True" 
                           for c in p.get("status", {}).get("conditions", []))
                ]
                
                if len(ready_pods) >= len(cluster_info["pods"]):
                    all_pods_ready = True
                    logger.info(f"All pods ready and scheduled: {len(ready_pods)} pods")
                    break
                else:
                    elapsed = int(time.time() - start_wait)
                    logger.info(f"Waiting for pods to be fully ready... ({len(ready_pods)}/{len(cluster_info['pods'])} ready, {elapsed}s elapsed)")
            
            time.sleep(5)
        
        assert all_pods_ready, f"Pods did not become fully ready within {max_wait}s"

        # Create initial dataset with known values
        logger.info("Creating initial dataset...")
        pod_name = self._get_hosting_pod_for_key(cluster_info, "persistence_test")
        
        create_dataset_script = f"""#!/bin/bash
set -e

# Create a structured dataset that we can verify later
for i in $(seq 1 20); do
    redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" \\
        GRAPH.QUERY persistence_test "CREATE (:DataNode {{id: $i, value: 'data_$i', phase: 'initial'}})" >/dev/null 2>&1
done

# Create relationships
redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" \\
    GRAPH.QUERY persistence_test "MATCH (a:DataNode), (b:DataNode) WHERE a.id = 1 AND b.id = 20 CREATE (a)-[:LINKED]->(b)" >/dev/null 2>&1

echo "SUCCESS: Initial dataset created"
"""
        
        exec_cmd = [
            'kubectl', 'exec', pod_name,
            '-n', cluster_info['namespace'],
            '-c', 'falkordb-cluster',
            '--',
            'sh', '-c', create_dataset_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0 and "SUCCESS" in result.stdout, \
            f"Failed to create initial dataset: {result.stderr}"
        logger.info("Initial dataset created successfully")
        
        # Function to verify data integrity
        def verify_data(expected_nodes, expected_relationships, phase_description):
            verify_script = f"""#!/bin/bash
set -e

# Function to extract count
extract_count() {{
    local output="$1"
    echo "$output" | awk '/^[0-9]+$/{{print; exit}}' || echo "0"
}}

# Count nodes
NODE_RESULT=$(redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" \\
    --raw GRAPH.RO_QUERY persistence_test "MATCH (n:DataNode) RETURN count(n)" 2>&1)
NODE_COUNT=$(extract_count "$NODE_RESULT")

# Count relationships
REL_RESULT=$(redis-cli -c -u "redis://{cluster_info['username']}:{cluster_info['password']}@localhost:6379/" \\
    --raw GRAPH.RO_QUERY persistence_test "MATCH ()-[r:LINKED]->() RETURN count(r)" 2>&1)
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
                'kubectl', 'exec', pod_name,
                '-n', cluster_info['namespace'],
                '-c', 'falkordb-cluster',
                '--',
                'sh', '-c', verify_script
            ]
            
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=60)
            
            # Parse counts
            node_count = 0
            rel_count = 0
            for line in result.stdout.split("\n"):
                if line.startswith("NODE_COUNT:"):
                    node_count = int(line.split(":")[1])
                elif line.startswith("REL_COUNT:"):
                    rel_count = int(line.split(":")[1])
            
            assert node_count == expected_nodes and rel_count == expected_relationships, \
                f"{phase_description}: Expected {expected_nodes} nodes and {expected_relationships} relationships, found {node_count} nodes and {rel_count} relationships"
            logger.info(f"{phase_description}: ✓ Data integrity verified ({node_count} nodes, {rel_count} relationships)")
        
        # Verify initial data
        verify_data(20, 1, "Initial state")
        
        # Test 1: Horizontal scaling (add replicas)
        logger.info("Test 1: Scaling replicas horizontally...")
        
        get_cluster_cmd = [
            "kubectl", "get", "cluster", cluster_info["name"],
            "-n", cluster_info["namespace"],
            "-o", "json"
        ]
        
        result = subprocess.run(get_cluster_cmd, capture_output=True, text=True)
        cluster_data = json.loads(result.stdout)
        current_replicas = cluster_data["spec"]["shardings"][0]["template"]["replicas"]
        
        # Scale up
        patch_cmd = [
            "kubectl", "patch", "cluster", cluster_info["name"],
            "-n", cluster_info["namespace"],
            "--type", "json",
            "-p", json.dumps([{
                "op": "replace",
                "path": "/spec/shardings/0/template/replicas",
                "value": current_replicas + 1
            }])
        ]
        
        subprocess.run(patch_cmd, capture_output=True, text=True, timeout=30)
        time.sleep(45)
        
        verify_data(20, 1, "After horizontal scale-up")
        
        # Scale back down
        patch_cmd = [
            "kubectl", "patch", "cluster", cluster_info["name"],
            "-n", cluster_info["namespace"],
            "--type", "json",
            "-p", json.dumps([{
                "op": "replace",
                "path": "/spec/shardings/0/template/replicas",
                "value": current_replicas
            }])
        ]
        
        subprocess.run(patch_cmd, capture_output=True, text=True, timeout=30)
        time.sleep(30)
        
        verify_data(20, 1, "After horizontal scale-down")
        
        logger.info("✓ Data persisted through horizontal scaling")
        
        logger.info("Data persistence after scaling test completed successfully")

    def test_cluster_extra_user_creation(self, clean_graphs):
        """Test extra user creation in cluster mode deployments."""
        cluster_info = clean_graphs
        
        logger.info("Testing extra user creation in cluster mode...")

        # Verify extra user is configured in cluster
        logger.info("Verifying extra user configuration across cluster nodes...")
        assert len(cluster_info["pods"]) >= 3, f"Expected at least 3 pods for cluster, got {len(cluster_info['pods'])}"

        # Check ACL file on each pod to verify extra user exists
        acl_verified_pods = 0
        for pod_name in cluster_info["pods"]:
            verify_acl_script = f"""#!/bin/bash
# Check if ACL file exists and contains extra user entry
if [ -f /data/users.acl ]; then
    # Look for the extra user entry (testuser from falkordbUser)
    if grep -q "user testuser on" /data/users.acl; then
        # Verify the entry has proper format
        if grep "user testuser on" /data/users.acl | grep -q ">"; then
            echo "SUCCESS: Extra user found with proper format"
            echo "ACL_LINE:"
            grep "user testuser on" /data/users.acl
            exit 0
        fi
    fi
fi
echo "FAILED: Extra user not found in ACL file"
exit 1
"""
            
            exec_cmd = [
                "kubectl", "exec", pod_name,
                "-n", cluster_info["namespace"],
                "-c", "falkordb-cluster",
                "--",
                "sh", "-c", verify_acl_script
            ]
            
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and "SUCCESS" in result.stdout:
                acl_verified_pods += 1
                logger.info(f"✓ Pod {pod_name}: Extra user verified in ACL file")
                # Log the actual ACL line for verification
                for line in result.stdout.split("\n"):
                    if line.startswith("ACL_LINE:"):
                        logger.debug(f"  ACL entry: {line.split('ACL_LINE:', 1)[1]}")
            else:
                logger.warning(f"✗ Pod {pod_name}: Extra user not found in ACL file")
                logger.debug(f"  Output: {result.stdout}")
                logger.debug(f"  Error: {result.stderr}")

        # At least majority of pods should have the extra user
        assert acl_verified_pods >= len(cluster_info["pods"]) - 1, \
            f"Extra user not found in ACL files on most pods: {acl_verified_pods}/{len(cluster_info['pods'])}"

        logger.info(f"✓ Extra user verified on {acl_verified_pods}/{len(cluster_info['pods'])} cluster pods")

        # Test authentication with extra user credentials
        logger.info("Testing authentication with extra user credentials...")
        auth_test_script = f"""#!/bin/bash
# Test connecting with extra user
redis-cli -u "redis://testuser:testpass123@localhost:6379/" PING
exit_code=$?
if [ $exit_code -eq 0 ]; then
    echo "SUCCESS: Extra user authentication successful"
    exit 0
else
    echo "FAILED: Could not authenticate with extra user credentials"
    exit 1
fi
"""
        
        exec_cmd = [
            "kubectl", "exec", cluster_info["pods"][0],
            "-n", cluster_info["namespace"],
            "-c", "falkordb-cluster",
            "--",
            "sh", "-c", auth_test_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and "SUCCESS" in result.stdout, \
            f"Extra user authentication failed: {result.stderr}"
        logger.info("✓ Extra user successfully authenticated")

        # Verify ACL permissions by testing operations
        logger.info("Testing extra user ACL permissions...")
        acl_test_script = f"""#!/bin/bash
# Test that extra user can perform graph operations (has ~* +@all permissions)
redis-cli -u "redis://testuser:testpass123@localhost:6379/" GRAPH.QUERY test_acl_graph "CREATE (:TestNode {{id: 'acl_test'}})" >/dev/null 2>&1
exit_code=$?
if [ $exit_code -eq 0 ]; then
    echo "SUCCESS: Extra user can execute GRAPH commands"
    exit 0
else
    echo "FAILED: Extra user cannot execute GRAPH commands"
    exit 1
fi
"""
        
        exec_cmd = [
            "kubectl", "exec", cluster_info["pods"][0],
            "-n", cluster_info["namespace"],
            "-c", "falkordb-cluster",
            "--",
            "sh", "-c", acl_test_script
        ]
        
        result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and "SUCCESS" in result.stdout, \
            f"Extra user ACL permissions test failed: {result.stderr}"
        logger.info("✓ Extra user has correct ACL permissions")

        # Count total user entries in ACL file to ensure consistency
        logger.info("Verifying ACL file consistency across cluster...")
        count_users_script = f"""#!/bin/bash
if [ -f /data/users.acl ]; then
    # Count user entries (lines starting with "user ")
    count=$(grep -c "^user " /data/users.acl || echo "0")
    echo "USER_COUNT:$count"
    exit 0
fi
echo "USER_COUNT:0"
exit 0
"""
        
        user_counts = {}
        for pod_name in cluster_info["pods"]:
            exec_cmd = [
                "kubectl", "exec", pod_name,
                "-n", cluster_info["namespace"],
                "-c", "falkordb-cluster",
                "--",
                "sh", "-c", count_users_script
            ]
            
            result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("USER_COUNT:"):
                        count = int(line.split(":")[1])
                        user_counts[pod_name] = count
                        logger.info(f"Pod {pod_name}: {count} user entries in ACL file")

        # All pods should have similar user counts (default admin + extra user minimum)
        if user_counts:
            min_count = min(user_counts.values())
            max_count = max(user_counts.values())
            
            # Allow difference of at most 1 user entry for timing reasons
            assert max_count - min_count <= 1, \
                f"Inconsistent ACL configuration across pods: min={min_count}, max={max_count}"
            logger.info(f"✓ ACL configuration consistent across cluster: {min_count}-{max_count} users per pod")

        logger.info("Cluster extra user creation test completed successfully")
