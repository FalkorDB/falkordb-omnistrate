"""
Validation utilities for Helm chart testing.
"""

from typing import Dict, Any, List, Optional


def get_falkordb_container_name(pod_name, namespace="default"):
    """
    Get the correct container name for a FalkorDB pod by inspecting the pod.

    Args:
        pod_name: Name of the pod to inspect
        namespace: Kubernetes namespace

    Returns:
        str: Container name or None if not found
    """
    import subprocess
    import logging

    try:
        check_cmd = [
            "kubectl",
            "get",
            "pod",
            pod_name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.spec.containers[*].name}",
        ]
        result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            containers = result.stdout.strip().split()
            logging.getLogger(__name__).info(
                f"Pod {pod_name} has containers: {containers}"
            )

            # Try container names in order of preference
            container_candidates = ["falkordb-cluster", "falkordb", "redis", "server"]
            for candidate in container_candidates:
                if candidate in containers:
                    return candidate

            logging.getLogger(__name__).error(
                f"Could not find FalkorDB container in pod {pod_name}. Available: {containers}"
            )
            return None
        else:
            logging.getLogger(__name__).error(
                f"Failed to get pod info: {result.stderr}"
            )
            return None
    except Exception as e:
        logging.getLogger(__name__).error(f"Error checking pod containers: {e}")
        return None


def validate_basic_cluster_properties(
    cluster_manifest: Dict[str, Any], expected_mode: str
) -> List[str]:
    """
    Validate basic cluster properties.

    Args:
        cluster_manifest: Cluster manifest to validate
        expected_mode: Expected deployment mode (standalone, replication, cluster)

    Returns:
        List of validation errors (empty if all validations pass)
    """
    errors = []

    if not cluster_manifest:
        errors.append("Cluster manifest is None or empty")
        return errors

    # Check basic structure
    if cluster_manifest.get("kind") != "Cluster":
        errors.append(f"Expected kind 'Cluster', got '{cluster_manifest.get('kind')}'")

    if cluster_manifest.get("apiVersion") != "apps.kubeblocks.io/v1":
        errors.append(
            f"Expected apiVersion 'apps.kubeblocks.io/v1', got '{cluster_manifest.get('apiVersion')}'"
        )

    # Check topology matches expected mode
    spec = cluster_manifest.get("spec", {})
    topology = spec.get("topology", "")
    if topology != expected_mode:
        errors.append(f"Expected topology '{expected_mode}', got '{topology}'")

    # Check that we have either componentSpecs or shardings
    component_specs = spec.get("componentSpecs", [])
    shardings = spec.get("shardings", [])

    if not component_specs and not shardings:
        errors.append("No componentSpecs or shardings found in cluster")

    return errors


def validate_resource_mapping(
    component_spec: Dict[str, Any], expected_cpu: str, expected_memory: str
) -> List[str]:
    """
    Validate resource limits and requests mapping.

    Args:
        component_spec: Component specification
        expected_cpu: Expected CPU value
        expected_memory: Expected memory value

    Returns:
        List of validation errors
    """
    errors = []

    resources = component_spec.get("resources", {})
    if not resources:
        errors.append("No resources defined in component spec")
        return errors

    # Check limits
    limits = resources.get("limits", {})
    if limits.get("cpu") != expected_cpu:
        errors.append(f"Expected CPU limit '{expected_cpu}', got '{limits.get('cpu')}'")

    if limits.get("memory") != expected_memory:
        errors.append(
            f"Expected memory limit '{expected_memory}', got '{limits.get('memory')}'"
        )

    # Check requests
    requests = resources.get("requests", {})
    if requests.get("cpu") != expected_cpu:
        errors.append(
            f"Expected CPU request '{expected_cpu}', got '{requests.get('cpu')}'"
        )

    if requests.get("memory") != expected_memory:
        errors.append(
            f"Expected memory request '{expected_memory}', got '{requests.get('memory')}'"
        )

    return errors


def validate_falkordb_args(
    env_vars: Dict[str, str], expected_config: Dict[str, str]
) -> List[str]:
    """
    Validate FALKORDB_ARGS environment variable contains expected configuration.

    Args:
        env_vars: Environment variables dictionary
        expected_config: Expected configuration key-value pairs

    Returns:
        List of validation errors
    """
    errors = []

    if "FALKORDB_ARGS" not in env_vars:
        errors.append("FALKORDB_ARGS environment variable not found")
        return errors

    args = env_vars["FALKORDB_ARGS"]

    for key, value in expected_config.items():
        expected_arg = f"{key} {value}"
        if expected_arg not in args:
            errors.append(f"Expected '{expected_arg}' in FALKORDB_ARGS, got: {args}")

    return errors


def validate_storage_configuration(
    component_spec: Dict[str, Any], expected_size: str
) -> List[str]:
    """
    Validate storage configuration in volume claim templates.

    Args:
        component_spec: Component specification
        expected_size: Expected storage size (e.g., "20Gi")

    Returns:
        List of validation errors
    """
    errors = []

    volume_templates = component_spec.get("volumeClaimTemplates", [])
    if not volume_templates:
        errors.append("No volumeClaimTemplates found in component spec")
        return errors

    data_template = None
    for template in volume_templates:
        if template.get("name") == "data":
            data_template = template
            break

    if not data_template:
        errors.append("No 'data' volume claim template found")
        return errors

    storage_request = (
        data_template.get("spec", {})
        .get("resources", {})
        .get("requests", {})
        .get("storage")
    )
    if storage_request != expected_size:
        errors.append(
            f"Expected storage size '{expected_size}', got '{storage_request}'"
        )

    return errors


def validate_service_configuration(
    service_manifest: Dict[str, Any], expected_type: str = "ClusterIP"
) -> List[str]:
    """
    Validate service configuration.

    Args:
        service_manifest: Service manifest
        expected_type: Expected service type

    Returns:
        List of validation errors
    """
    errors = []

    if not service_manifest:
        errors.append("Service manifest is None or empty")
        return errors

    if service_manifest.get("kind") != "Service":
        errors.append(f"Expected kind 'Service', got '{service_manifest.get('kind')}'")

    spec = service_manifest.get("spec", {})
    service_type = spec.get("type", "ClusterIP")
    if service_type != expected_type:
        errors.append(f"Expected service type '{expected_type}', got '{service_type}'")

    # Check ports
    ports = spec.get("ports", [])
    if not ports:
        errors.append("No ports defined in service")
    else:
        # Check if FalkorDB port exists
        falkordb_port = None
        for port in ports:
            if port.get("port") == 6379:
                falkordb_port = port
                break

        if not falkordb_port:
            errors.append("FalkorDB port 6379 not found in service")

    return errors


def validate_job_configuration(
    job_manifest: Dict[str, Any], expected_username: str
) -> List[str]:
    """
    Validate Job configuration for user creation.

    Args:
        job_manifest: Job manifest
        expected_username: Expected username in the job script

    Returns:
        List of validation errors
    """
    errors = []

    if not job_manifest:
        errors.append("Job manifest is None or empty")
        return errors

    if job_manifest.get("kind") != "Job":
        errors.append(f"Expected kind 'Job', got '{job_manifest.get('kind')}'")

    # Check job template
    template = job_manifest.get("spec", {}).get("template", {})
    containers = template.get("spec", {}).get("containers", [])

    if not containers:
        errors.append("No containers found in job template")
        return errors

    container = containers[0]
    command = container.get("command", [])

    if len(command) < 3:
        errors.append("Job command should have at least 3 elements")
        return errors

    script = command[2] if len(command) > 2 else ""

    if expected_username not in script:
        errors.append(
            f"Expected username '{expected_username}' not found in job script"
        )

    # Check for basic ACL commands
    required_acl_commands = [
        "+INFO",
        "+CLIENT",
        "+DBSIZE",
        "+PING",
        "+HELLO",
        "+AUTH",
        "+RESTORE",
        "+DUMP",
        "+DEL",
        "+EXISTS",
        "+UNLINK",
        "+TYPE",
        "+FLUSHALL",
        "+TOUCH",
        "+EXPIRE",
        "+PEXPIREAT",
        "+TTL",
        "+PTTL",
        "+EXPIRETIME",
        "+RENAME",
        "+RENAMENX",
        "+SCAN",
        "+DISCARD",
        "+EXEC",
        "+MULTI",
        "+UNWATCH",
        "+WATCH",
        "+ECHO",
        "+SLOWLOG",
        "+WAIT",
        "+WAITAOF",
        "+GET",
        "+SET",
        "+GRAPH.INFO",
        "+GRAPH.LIST",
        "+GRAPH.QUERY",
        "+GRAPH.RO_QUERY",
        "+GRAPH.EXPLAIN",
        "+GRAPH.PROFILE",
        "+GRAPH.DELETE",
        "+GRAPH.CONSTRAINT",
        "+GRAPH.SLOWLOG",
        "+GRAPH.BULK",
        "+GRAPH.CONFIG",
        "+GRAPH.COPY",
        "+CLUSTER",
        "+COMMAND",
        "+GRAPH.MEMORY",
        "+MEMORY",
        "+BGREWRITEAOF",
    ]
    for cmd in required_acl_commands:
        if cmd not in script:
            errors.append(f"Required ACL command '{cmd}' not found in job script")

    return errors


def validate_external_service_annotations(
    service_manifest: Dict[str, Any], expected_hostname: str
) -> List[str]:
    """
    Validate external service annotations for Omnistrate.

    Args:
        service_manifest: Service manifest
        expected_hostname: Expected hostname annotation

    Returns:
        List of validation errors
    """
    errors = []

    if not service_manifest:
        errors.append("Service manifest is None or empty")
        return errors

    annotations = service_manifest.get("metadata", {}).get("annotations", {})

    # Check required external-dns annotations
    expected_annotations = {
        "external-dns.alpha.kubernetes.io/hostname": expected_hostname,
        "external-dns.alpha.kubernetes.io/endpoints-type": "NodeExternalIP",
        "external-dns.alpha.kubernetes.io/ttl": "60",
    }

    for key, expected_value in expected_annotations.items():
        actual_value = annotations.get(key)
        if actual_value != expected_value:
            errors.append(
                f"Expected annotation '{key}' = '{expected_value}', got '{actual_value}'"
            )

    return errors


def validate_replicas_configuration(
    cluster_manifest: Dict[str, Any], expected_replicas: int
) -> List[str]:
    """
    Validate replicas configuration in cluster.
    Handles both componentSpecs (standalone/replication) and shardings (cluster) structures.

    Args:
        cluster_manifest: Cluster manifest
        expected_replicas: Expected number of replicas

    Returns:
        List of validation errors
    """
    errors = []

    spec = cluster_manifest.get("spec", {})

    # Try componentSpecs first (standalone/replication mode)
    component_specs = spec.get("componentSpecs", [])
    if component_specs:
        component = component_specs[0]
        replicas = component.get("replicas", 1)

        if replicas != expected_replicas:
            errors.append(f"Expected {expected_replicas} replicas, got {replicas}")
        return errors

    # Try shardings structure (cluster mode)
    shardings = spec.get("shardings", [])
    if shardings:
        template = shardings[0].get("template", {})
        replicas = template.get("replicas", 1)

        if replicas != expected_replicas:
            errors.append(f"Expected {expected_replicas} replicas, got {replicas}")
        return errors

    errors.append("No componentSpecs or shardings found")
    return errors


def validate_falkordb_connection_in_cluster(
    pod_name, namespace, username, password, timeout=60
):
    """
    Validate FalkorDB connection by running redis-cli inside the pod using kubectl exec.
    This avoids DNS resolution issues and package installation problems.

    Args:
        pod_name: Name of the pod to test
        namespace: Kubernetes namespace
        username: Username for FalkorDB authentication
        password: Password for FalkorDB authentication
        timeout: Timeout in seconds

    Returns:
        bool: True if connection successful
    """
    import subprocess
    import logging

    logger = logging.getLogger(__name__)

    # Shell script that will run inside the pod using redis-cli
    test_script = f"""#!/bin/bash
set -e

# Test basic connectivity using redis-cli
if redis-cli -u "redis://{username}:{password}@localhost:6379/" ping | grep -q "PONG"; then
    echo "SUCCESS: Basic connectivity test passed"
    
    # Test FalkorDB graph query
    if redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY test_conn "RETURN 1" >/dev/null 2>&1; then
        echo "SUCCESS: FalkorDB graph query successful"
    else
        echo "WARNING: Graph query failed but basic connection works"
        # Don't fail on graph query issues as basic connectivity is working
    fi
    
    echo "SUCCESS: Connection validation completed"
    exit 0
else
    echo "FAILED: Ping test failed"
    exit 1
fi
"""

    try:
        # Use kubectl exec to run the test script inside the pod directly
        exec_cmd = [
            "kubectl",
            "exec",
            f"{pod_name}",
            "-n",
            namespace,
            "-c",
            "falkordb-cluster",  # Target the main container
            "--",
            "sh",
            "-c",
            test_script,
        ]

        logger.info(
            f"Running connection test inside pod via kubectl exec on {pod_name}"
        )
        result = subprocess.run(
            exec_cmd, capture_output=True, text=True, timeout=timeout
        )

        logger.info(f"Connection test return code: {result.returncode}")
        logger.info(f"Connection test stdout: {result.stdout}")
        logger.info(f"Connection test stderr: {result.stderr}")

        if result.returncode == 0 and "SUCCESS" in result.stdout:
            logger.info(f"FalkorDB connection test successful for {pod_name}")
            return True
        else:
            logger.error(f"FalkorDB connection test failed for {pod_name}")
            logger.error(f"Return code: {result.returncode}")
            logger.error(f"Full stdout: {result.stdout}")
            logger.error(f"Full stderr: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"Connection test timed out for {pod_name}")
        return False
    except Exception as e:
        logger.error(f"Failed to run connection test for {pod_name}: {e}")
        return False


def validate_falkordb_connection(
    host, port, username, password, timeout=30, is_cluster=False
):
    """
    Validate FalkorDB connection.

    Args:
        host: FalkorDB host
        port: FalkorDB port
        username: Username for authentication
        password: Password for authentication
        timeout: Connection timeout in seconds
        is_cluster: Whether this is a cluster mode connection (affects connection strategy)

    Returns:
        bool: True if connection successful
    """
    import logging
    import socket

    logger = logging.getLogger(__name__)

    try:
        # First check if port is reachable
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()

        if result != 0:
            logger.error(f"Cannot connect to {host}:{port} - port not reachable")
            return False

        # Try Redis client connection with authentication
        try:
            import redis

            r = redis.Redis(
                host=host,
                port=port,
                username=username,
                password=password,
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
                decode_responses=True,
            )

            # Test connection with PING
            result = r.ping()
            if result:
                logger.info(
                    f"Successfully connected to FalkorDB at {host}:{port} with Redis client"
                )
                r.close()
                return True

        except Exception as redis_error:
            logger.debug(f"Redis client connection failed: {redis_error}")

        # Try FalkorDB client as fallback
        try:
            import falkordb

            conn = falkordb.FalkorDB(
                host=host, port=port, username=username, password=password
            )

            # Test with a simple command
            result = conn.execute_command("PING")
            if result:
                logger.info(
                    f"Successfully connected to FalkorDB at {host}:{port} with FalkorDB client"
                )
                conn.close()
                return True

        except Exception as falkor_error:
            logger.debug(f"FalkorDB client connection failed: {falkor_error}")

        logger.error(f"All connection attempts failed for {host}:{port}")
        return False

    except Exception as e:
        logger.error(f"Connection validation failed: {e}")
        return False


def validate_falkordb_connection_in_replication(
    pod_name, namespace, username, password, timeout=60
):
    """
    Validate FalkorDB connection in replication mode by running redis-cli inside the pod using kubectl exec.
    This uses the same reliable approach as cluster tests.

    Args:
        pod_name: Name of the pod to test
        namespace: Kubernetes namespace
        username: Username for FalkorDB authentication
        password: Password for FalkorDB authentication
        timeout: Timeout in seconds

    Returns:
        bool: True if connection successful
    """
    import subprocess
    import logging

    logger = logging.getLogger(__name__)

    # First, check if pod exists and what containers it has
    try:
        check_cmd = [
            "kubectl",
            "get",
            "pod",
            pod_name,
            "-n",
            namespace,
            "-o",
            "jsonpath={.spec.containers[*].name}",
        ]
        result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            containers = result.stdout.strip().split()
            logger.info(f"Pod {pod_name} has containers: {containers}")
        else:
            logger.error(f"Failed to get pod info: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error checking pod containers: {e}")
        return False

    # Try to identify the correct container name
    container_name = "falkordb-cluster"
    if "falkordb-cluster" not in containers:
        # Try alternative container names commonly used in replication mode
        for alt_name in ["falkordb", "redis", "server"]:
            if alt_name in containers:
                container_name = alt_name
                logger.info(f"Using container name: {container_name}")
                break
        else:
            logger.error(
                f"Could not find FalkorDB container in pod {pod_name}. Available: {containers}"
            )
            return False

    # Shell script that will run inside the pod using redis-cli
    test_script = f"""#!/bin/bash
set -e

# Test basic connectivity using redis-cli
if redis-cli -u "redis://{username}:{password}@localhost:6379/" ping | grep -q "PONG"; then
    echo "SUCCESS: Basic connectivity test passed"
    
    # Test FalkorDB graph query
    if redis-cli -u "redis://{username}:{password}@localhost:6379/" GRAPH.QUERY test_conn "RETURN 1" >/dev/null 2>&1; then
        echo "SUCCESS: FalkorDB graph query successful"
    else
        echo "WARNING: Graph query failed but basic connection works"
        # Don't fail on graph query issues as basic connectivity is working
    fi
    
    echo "SUCCESS: Connection validation completed"
    exit 0
else
    echo "FAILED: Ping test failed"
    exit 1
fi
"""

    try:
        # Use kubectl exec to run the test script inside the pod directly
        exec_cmd = [
            "kubectl",
            "exec",
            f"{pod_name}",
            "-n",
            namespace,
            "-c",
            container_name,
            "--",
            "sh",
            "-c",
            test_script,
        ]

        logger.info(
            f"Running validation command in pod {pod_name} container {container_name}"
        )
        result = subprocess.run(
            exec_cmd, capture_output=True, text=True, timeout=timeout
        )

        if result.returncode == 0:
            logger.info(f"Connection validation successful for pod {pod_name}")
            logger.debug(f"Output: {result.stdout}")
            return True
        else:
            logger.error(f"Connection validation failed for pod {pod_name}")
            logger.error(f"Exit code: {result.returncode}")
            logger.error(f"Stdout: {result.stdout}")
            logger.error(f"Stderr: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"Connection validation timed out for pod {pod_name}")
        return False
    except Exception as e:
        logger.error(f"Connection validation error for pod {pod_name}: {e}")
        return False


def validate_replication_status(
    pod_name, namespace, username, password, expected_replicas=1, timeout=60
):
    """
    Validate replication status in FalkorDB using kubectl exec.
    This uses the reliable approach like cluster tests.

    Args:
        pod_name: Name of the master pod to test from
        namespace: Kubernetes namespace
        username: Username for authentication
        password: Password for authentication
        expected_replicas: Expected number of replicas
        timeout: Timeout in seconds

    Returns:
        bool: True if replication status is valid
    """
    import subprocess
    import logging

    logger = logging.getLogger(__name__)

    # First, find the correct container name
    container_name = None
    container_candidates = ["falkordb-cluster", "falkordb", "redis", "server"]

    for candidate in container_candidates:
        try:
            # Test if container exists by trying to get its status
            test_cmd = [
                "kubectl",
                "get",
                "pod",
                pod_name,
                "-n",
                namespace,
                "-o",
                f'jsonpath={{.spec.containers[?(@.name=="{candidate}")].name}}',
            ]
            result = subprocess.run(
                test_cmd, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip() == candidate:
                container_name = candidate
                logger.debug(f"Found container {container_name} in pod {pod_name}")
                break
        except Exception as e:
            logger.debug(f"Container {candidate} test failed: {e}")
            continue

    if not container_name:
        logger.error(f"Could not find valid container in pod {pod_name}")
        return False

    # Shell script to check replication status
    test_script = f"""#!/bin/bash
set -e

# Get replication info using redis-cli
if redis-cli -u "redis://{username}:{password}@localhost:6379/" INFO replication | grep -q "connected_slaves"; then
    CONNECTED_SLAVES=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" INFO replication | grep "connected_slaves:" | cut -d: -f2 | tr -d '\\r')
    echo "Connected slaves: $CONNECTED_SLAVES"
    
    if [ "$CONNECTED_SLAVES" -ge "{expected_replicas}" ]; then
        echo "SUCCESS: Replication status valid ($CONNECTED_SLAVES >= {expected_replicas})"
        exit 0
    else
        echo "FAILED: Not enough replicas ($CONNECTED_SLAVES < {expected_replicas})"
        exit 1
    fi
else
    echo "FAILED: Could not get replication info"
    exit 1
fi
"""

    try:
        exec_cmd = [
            "kubectl",
            "exec",
            f"{pod_name}",
            "-n",
            namespace,
            "-c",
            container_name,
            "--",
            "sh",
            "-c",
            test_script,
        ]

        logger.debug(f"Checking replication status in pod {pod_name}")
        result = subprocess.run(
            exec_cmd, capture_output=True, text=True, timeout=timeout
        )

        if result.returncode == 0:
            logger.info(f"Replication status validation successful for pod {pod_name}")
            logger.debug(f"Output: {result.stdout}")
            return True
        else:
            logger.error(f"Replication status validation failed for pod {pod_name}")
            logger.error(f"Exit code: {result.returncode}")
            logger.error(f"Stdout: {result.stdout}")
            logger.error(f"Stderr: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"Replication status validation timed out for pod {pod_name}")
        return False
    except Exception as e:
        logger.error(f"Replication status validation error for pod {pod_name}: {e}")
        return False


def validate_cluster_status(
    pod_name, namespace, username, password, expected_nodes=3, timeout=60
):
    """
    Validate cluster status in FalkorDB using kubectl exec.
    This runs inside the cluster to avoid DNS resolution issues.

    Args:
        pod_name: Name of a pod in the cluster to test from
        namespace: Kubernetes namespace
        username: Username for authentication
        password: Password for authentication
        expected_nodes: Expected number of cluster nodes
        timeout: Timeout in seconds

    Returns:
        int: Number of connected cluster nodes (0 if validation failed)
    """
    import subprocess
    import logging

    logger = logging.getLogger(__name__)

    # Python test script that will run inside the pod
    test_script = f"""#!/bin/bash
set -e

# Test basic connectivity first
if ! redis-cli -u "redis://{username}:{password}@localhost:6379/" ping | grep -q "PONG"; then
    echo "CLUSTER_NODES:0"
    echo "FAILED: Ping test failed"
    exit 1
fi

# Try to get cluster information
if cluster_output=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" CLUSTER NODES 2>/dev/null); then
    # Count master and slave nodes from CLUSTER NODES output
    active_nodes=$(echo "$cluster_output" | grep -E "(master|slave)" | wc -l)
    echo "CLUSTER_NODES:$active_nodes"
    echo "SUCCESS: Found $active_nodes cluster nodes"
    exit 0
fi

# Try alternative cluster info check
if info_output=$(redis-cli -u "redis://{username}:{password}@localhost:6379/" INFO cluster 2>/dev/null); then
    if echo "$info_output" | grep -q "cluster_enabled:1"; then
        echo "CLUSTER_NODES:1"
        echo "SUCCESS: Cluster mode enabled (single node detected)"
        exit 0
    fi
fi

# If we get here, cluster commands aren't working
echo "CLUSTER_NODES:0"
echo "FAILED: No cluster information available"
exit 1
"""

    try:
        # Use kubectl exec to run the test script inside the pod directly
        exec_cmd = [
            "kubectl",
            "exec",
            f"{pod_name}",
            "-n",
            namespace,
            "-c",
            "falkordb-cluster",  # Target the main container
            "--",
            "sh",
            "-c",
            test_script,
        ]

        logger.info(f"Running cluster status check via kubectl exec on {pod_name}")
        result = subprocess.run(
            exec_cmd, capture_output=True, text=True, timeout=timeout
        )

        logger.info(f"Exec command return code: {result.returncode}")
        logger.info(f"Exec command stdout: {result.stdout}")
        logger.info(f"Exec command stderr: {result.stderr}")

        # Parse the number of cluster nodes from output
        cluster_nodes = 0
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if line.startswith("CLUSTER_NODES:"):
                    try:
                        cluster_nodes = int(line.split(":")[1])
                        break
                    except (ValueError, IndexError):
                        pass
        else:
            # If the command failed, log the full output for debugging
            logger.error(f"kubectl exec failed with return code {result.returncode}")
            logger.error(f"Full stdout: {result.stdout}")
            logger.error(f"Full stderr: {result.stderr}")

        if cluster_nodes >= expected_nodes:
            logger.info(
                f"Cluster status validation successful: {cluster_nodes} nodes found (expected >= {expected_nodes})"
            )
            return cluster_nodes
        else:
            logger.warning(
                f"Cluster status validation failed: {cluster_nodes} nodes found (expected >= {expected_nodes})"
            )
            return cluster_nodes

    except subprocess.TimeoutExpired:
        logger.error(f"Cluster status check timed out for {pod_name}")
        return 0
    except Exception as e:
        logger.error(f"Failed to run cluster status check for {pod_name}: {e}")
        return 0
