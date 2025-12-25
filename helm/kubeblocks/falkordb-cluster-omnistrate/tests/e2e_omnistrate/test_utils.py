"""
Test utilities for E2E Omnistrate tests.

This module provides utility functions for:
- Data operations (add, verify)
- Zero-downtime testing during operations
- OOM stress testing
- Multi-zone topology validation
"""

import time
import threading
import logging
import os
import concurrent.futures
from redis.exceptions import OutOfMemoryError, ReadOnlyError
import secrets

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)

log = logging.getLogger(__name__)


def add_data(instance, ssl=False, key="test", n=1, network_type="PUBLIC"):
    """
    Add data entries to a graph using parallel workers scaled by n.
    
    Args:
        instance: OmnistrateFleetInstance
        ssl: Use SSL connection
        key: Graph name
        n: Number of entries to add (also scales number of workers)
        network_type: PUBLIC or INTERNAL
    """
    logging.info(f"Adding {n} data entries to graph '{key}'")
    db = instance.create_connection(ssl=ssl, network_type=network_type)
    g = db.select_graph(key)
    
    # Scale workers based on n: larger n uses more parallelism
    if n <= 10:
        num_workers = 1
    elif n <= 100:
        num_workers = 2
    elif n <= 1000:
        num_workers = 4
    else:
        num_workers = max(4, min(n // 100, 16))  # Scale up to max 16 workers
    
    entries_per_worker = n // num_workers
    remainder = n % num_workers
    
    def worker(count):
        """Worker that adds entries to the graph."""
        try:
            for _ in range(count):
                g.query("CREATE (n:Person {name: 'Alice'})")
        except Exception as e:
            logging.exception(f"Error in add_data worker: {e}")
            raise
    
    logging.debug(f"Using {num_workers} workers to add {n} entries (base: {entries_per_worker} per worker)")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = []
        for i in range(num_workers):
            count = entries_per_worker + (1 if i < remainder else 0)
            futures.append(executor.submit(worker, count))
        
        # Wait for all workers to complete
        concurrent.futures.wait(futures)
        
        # Check for errors
        for f in futures:
            exc = f.exception()
            if exc is not None:
                raise AssertionError(f"Failed to add data: {exc}") from exc
    
    logging.debug(f"Successfully added {n} entries to graph '{key}' using {num_workers} workers")



def has_data(instance, ssl=False, key="test", min_rows=1, network_type="PUBLIC"):
    """
    Check if graph has at least min_rows entries.
    
    Args:
        instance: OmnistrateFleetInstance
        ssl: Use SSL connection
        key: Graph name
        min_rows: Minimum number of rows expected
        network_type: PUBLIC or INTERNAL
        
    Returns:
        True if graph has at least min_rows entries
    """
    logging.info(f"Checking if graph '{key}' has at least {min_rows} rows")
    db = instance.create_connection(
        ssl=ssl, force_reconnect=True, network_type=network_type
    )
    g = db.select_graph(key)
    rs = g.query("MATCH (n:Person) RETURN n")
    result = len(rs.result_set) >= min_rows
    logging.debug(
        f"Graph '{key}' has {len(rs.result_set)} rows. Meets requirement: {result}"
    )
    return result


def assert_data(
    instance,
    ssl=False,
    key="test",
    min_rows=1,
    msg="data missing",
    network_type="PUBLIC",
):
    """
    Assert that graph has at least min_rows entries.
    
    Args:
        instance: OmnistrateFleetInstance
        ssl: Use SSL connection
        key: Graph name
        min_rows: Minimum number of rows expected
        msg: Error message if assertion fails
        network_type: PUBLIC or INTERNAL
        
    Raises:
        AssertionError if data verification fails
    """
    logging.info(
        f"Asserting data presence in graph '{key}' with at least {min_rows} rows"
    )
    if not has_data(
        instance, ssl=ssl, key=key, min_rows=min_rows, network_type=network_type
    ):
        logging.error(msg)
        raise AssertionError(msg)
    logging.debug(f"Assertion passed for graph '{key}' with at least {min_rows} rows")


def zero_downtime_worker(
    stop_evt, error_evt, instance, ssl=False, key="test", network_type="PUBLIC"
):
    """
    Worker thread that continuously writes and reads data.
    
    Args:
        stop_evt: Event to signal worker to stop
        error_evt: Event to signal an error occurred
        instance: OmnistrateFleetInstance
        ssl: Use SSL connection
        key: Graph name
        network_type: PUBLIC or INTERNAL
    """
    logging.info("Starting zero-downtime worker")
    try:
        db = instance.create_connection(
            ssl=ssl, force_reconnect=True, network_type=network_type
        )
        g = db.select_graph(key)
        while not stop_evt.is_set():
            g.query("CREATE (n:Person {name: 'Alice'})")
            g.ro_query("MATCH (n:Person {name: 'Alice'}) RETURN n")
            time.sleep(2)
    except Exception as e:
        logging.exception("Error in zero-downtime worker")
        error_evt.set()


def run_zero_downtime(instance, ssl, fn, network_type="PUBLIC"):
    """
    Run function while generating continuous R/W traffic.
    
    Use this for replicated/clustered topologies only to verify
    zero-downtime during operations like failover or scaling.
    
    Args:
        instance: OmnistrateFleetInstance
        ssl: Use SSL connection
        fn: Function to execute during traffic
        network_type: PUBLIC or INTERNAL
        
    Raises:
        AssertionError if zero-downtime traffic encounters an error
    """
    logging.info("Running function with zero-downtime traffic")
    stop_evt = threading.Event()
    err_evt = threading.Event()
    th = threading.Thread(
        target=zero_downtime_worker,
        args=(stop_evt, err_evt, instance, ssl, "test", network_type),
    )
    th.start()
    try:
        fn()
    finally:
        stop_evt.set()
        th.join()
    if err_evt.is_set():
        logging.error("Zero-downtime traffic encountered an error")
        raise AssertionError("Zero-downtime traffic encountered an error")
    logging.info("Completed function with zero-downtime traffic")


def change_then_revert(instance, ssl, do_fn, revert_fn, network_type="PUBLIC"):
    """
    Execute a change and then revert it, both under continuous traffic.
    
    Use for topology changes (replicas/shards) that must be undone
    before the next test.
    
    Args:
        instance: OmnistrateFleetInstance
        ssl: Use SSL connection
        do_fn: Function to execute for the change
        revert_fn: Function to execute to revert the change
        network_type: PUBLIC or INTERNAL
    """
    logging.info("Changing topology and reverting under traffic")
    # Forward change
    run_zero_downtime(instance, ssl, do_fn, network_type)
    # Revert change
    run_zero_downtime(instance, ssl, revert_fn, network_type)
    logging.info("Completed topology change and revert")


def stress_oom(
    instance,
    ssl=False,
    query_size="small",
    network_type="PUBLIC",
    stress_oomers=3,
    is_cluster=False,
    timeout_seconds=300,
):
    """
    Stress test by writing data until OOM is triggered.
    
    Args:
        instance: OmnistrateFleetInstance
        ssl: Use SSL connection
        query_size: Size of queries (small, medium, big)
        network_type: PUBLIC or INTERNAL
        stress_oomers: Base number of parallel stress workers (adjusted by query size)
        is_cluster: Whether instance is a cluster topology
        timeout_seconds: Maximum time to wait for OOM (default 300s = 5 minutes)
        
    Raises:
        AssertionError if OOM is not triggered or unexpected error occurs
    """
    logging.info("Starting stress test to trigger OOM with query size '%s'", query_size)
    db = instance.create_connection(ssl=ssl, network_type=network_type)
    g = db.select_graph("test")
    
    # Aggressive query templates - much larger ranges for faster OOM
    big = "UNWIND RANGE(1, 500000) AS id CREATE (n:Person {{random: '{}', id: id, data: '{}'}})"
    medium = "UNWIND RANGE(1, 200000) AS id CREATE (n:Person {{random: '{}', id: id, data: '{}'}})"
    small = "UNWIND RANGE(1, 100000) AS id CREATE (n:Person {{random: '{}', id: id}})"

    # Aggressive multipliers for faster OOM - more workers means faster memory consumption
    size_multiplier = {"small": 8, "medium": 12, "big": 16}
    num_clients = int(os.environ.get("STRESS_OOM_CLIENTS", stress_oomers * size_multiplier.get(query_size, 1)))

    if query_size in ("medium", "big"):
        try:
            cypher_query = """
            LOAD CSV FROM "https://storage.googleapis.com/falkordb-benchmark-datasets/oom_dataset.csv" AS row CREATE (:Person {name: row[0], age: toInteger(row[1])})
            """
            g.query(cypher_query)
            logging.info("Preloaded OOM dataset successfully")
        except Exception as e:
            # If we hit maxmemory during preload, that's OK - we're already at OOM
            if "maxmemory" in str(e).lower() or "out of memory" in str(e).lower():
                logging.info("Hit maxmemory during preload, skipping dataset load and proceeding with stress test")
            else:
                logging.error("Failed to preload OOM dataset: %s", str(e))
                raise AssertionError("Failed to preload OOM dataset") from e

    q_template = small if query_size == "small" else medium if query_size == "medium" else big

    start_time = time.time()
    oom_triggered = False

    def stress_worker():
        """Worker that executes queries until OOM."""
        nonlocal oom_triggered
        while True:
            # Check timeout
            if time.time() - start_time > timeout_seconds:
                logging.warning("Stress worker timed out after %d seconds", timeout_seconds)
                return "TIMEOUT"
            
            # Check if OOM already triggered by another worker
            if oom_triggered:
                return "OOM_DETECTED"
            
            try:
                # Generate unique random tokens to prevent caching and maximize memory usage
                token1 = secrets.token_hex(16)
                token2 = secrets.token_hex(32)  # Extra data for medium/big queries
                q = q_template.format(token1, token2) if query_size in ("medium", "big") else q_template.format(token1)
                logging.debug("Executing query with tokens")
                g.query(q)
                # No sleep - execute as fast as possible to trigger OOM quickly
            except Exception as e:
                if (
                    isinstance(e, OutOfMemoryError)
                    or "OOM" in str(e).upper()
                    or "OUT OF MEMORY" in str(e).upper()
                ):
                    logging.warning("Out of memory condition triggered in worker")
                    oom_triggered = True
                    return "OOM"
                logging.exception("Unexpected error during stress test in worker")
                raise

    logging.info(f"Running stress test with {num_clients} parallel clients (query_size={query_size}, timeout={timeout_seconds}s)")

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_clients) as executor:
        futures = [executor.submit(stress_worker) for _ in range(num_clients)]
        # Wait for any worker to hit OOM or error
        done, _ = concurrent.futures.wait(
            futures, timeout=timeout_seconds + 10, return_when=concurrent.futures.FIRST_EXCEPTION
        )
        # Cancel remaining workers
        for f in futures:
            f.cancel()
        
        # Check results
        oom_detected = False
        for f in done:
            result = f.result() if not f.cancelled() else None
            if result == "OOM":
                oom_detected = True
                break
            exc = f.exception()
            if exc is not None:
                # Check if the exception is OOM-related
                if isinstance(exc, OutOfMemoryError) or "OOM" in str(exc).upper() or "OUT OF MEMORY" in str(exc).upper():
                    oom_detected = True
                    break
                raise AssertionError(
                    "Stress worker raised an unexpected error, OOM did not occur"
                ) from exc
        
        if not oom_detected:
            raise AssertionError(f"OOM was not triggered within {timeout_seconds} seconds")

    # Clean up after OOM
    if is_cluster:
        try:
            g.client.execute_command("FLUSHALL", target_nodes="primaries")
            _try_bgrewriteaof(g.client, target_nodes="primaries")
        except ReadOnlyError:
            logging.warning("Primary nodes are read-only, re-initializing cache")
            g.client.connection.nodes_manager.initialize()
            g.client.execute_command("FLUSHALL", target_nodes="primaries")
            _try_bgrewriteaof(g.client, target_nodes="primaries")
    else:
        g.client.execute_command("FLUSHALL")
        _try_bgrewriteaof(g.client)


def _try_bgrewriteaof(client, **kwargs):
    """
    Attempt to rewrite AOF, ignore failures.
    
    Args:
        client: Redis client
        **kwargs: Additional arguments for BGREWRITEAOF command
    """
    try:
        client.execute_command("BGREWRITEAOF", **kwargs)
    except Exception as e:
        logging.warning("BGREWRITEAOF failed: %s", e)


def assert_multi_zone(instance, host_count=6):
    """
    Assert that instance is deployed in multiple availability zones.
    
    Args:
        instance: OmnistrateFleetInstance
        host_count: Expected number of hosts
        
    Raises:
        AssertionError if not multi-zone or host count doesn't match
    """
    host_count = int(host_count)
    logging.info("Asserting multi-zone topology")
    network_topology = instance.get_network_topology(force_refresh=True)
    logging.debug(f"Network topology: {network_topology}")
    
    # Find the multi-zone resource
    resource_key = next(
        (
            k
            for k, v in network_topology.items()
            if v.get("resourceName") in ("node-mz", "cluster-mz")
        ),
        None,
    )

    if not resource_key:
        logging.error("No multi-zone resource found in network topology")
        raise AssertionError("No multi-zone resource found in network topology")

    resource = network_topology[resource_key]
    nodes = resource.get("nodes", [])

    if len(nodes) == 0:
        logging.error("No nodes found in network topology")
        raise AssertionError("No nodes found in network topology")

    logging.debug("Host count provided: %d, Nodes found: %d", host_count, len(nodes))
    logging.debug("Nodes details: %s", nodes)

    if len(nodes) != host_count:
        logging.error(
            "Host count does not match number of nodes. Current host count: %d; Number of nodes: %d",
            host_count,
            len(nodes),
        )
        raise AssertionError(
            f"Host count does not match number of nodes. "
            f"Current host count: {host_count}; Number of nodes: {len(nodes)}"
        )

    azs = set(node.get("availabilityZone") for node in nodes if node.get("availabilityZone"))
    if len(azs) < 2:
        logging.error(
            "Multi-zone topology expected, but only found %d availability zones: %s",
            len(azs),
            azs,
        )
        raise AssertionError(
            f"Multi-zone topology expected, but only found {len(azs)} availability zones: {azs}"
        )
    logging.info("Multi-zone topology assertion passed with %d zones", len(azs))
