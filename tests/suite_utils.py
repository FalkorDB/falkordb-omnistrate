import time
import threading
import logging
from redis.exceptions import OutOfMemoryError
from .classes.omnistrate_fleet_instance import OmnistrateFleetInstance

import os

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)

log = logging.getLogger(__name__)


def add_data(
    instance: OmnistrateFleetInstance, ssl=False, key="test", n=1, network_type="PUBLIC"
):
    logging.info(f"Adding {n} data entries to graph '{key}'")
    db = instance.create_connection(ssl=ssl, network_type=network_type)
    g = db.select_graph(key)
    for _ in range(n):
        g.query("CREATE (n:Person {name: 'Alice'})")
    logging.debug(f"Successfully added {n} entries to graph '{key}'")


def has_data(
    instance: OmnistrateFleetInstance,
    ssl=False,
    key="test",
    min_rows=1,
    network_type="PUBLIC",
):
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
    instance: OmnistrateFleetInstance,
    ssl=False,
    key="test",
    min_rows=1,
    msg="data missing",
    network_type="PUBLIC",
):
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
    stop_evt,
    error_evt,
    instance: OmnistrateFleetInstance,
    ssl=False,
    key="test",
    network_type="PUBLIC",
):
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


def run_zero_downtime(
    instance: OmnistrateFleetInstance, ssl, fn, network_type="PUBLIC"
):
    """
    Run fn while generating continuous R/W traffic.
    Use this for replicated/clustered topologies only.
    """
    logging.info("Running function with zero-downtime traffic")
    stop_evt = threading.Event()
    err_evt = threading.Event()
    th = threading.Thread(
        target=zero_downtime_worker,
        args=(stop_evt, err_evt, instance, ssl, network_type),
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


def change_then_revert(
    instance: OmnistrateFleetInstance, ssl, do_fn, revert_fn, network_type="PUBLIC"
):
    """
    Runs do_fn under traffic, then *always* reverts back under traffic.
    Use for topology changes (replicas/shards) that must be undone before the next test.
    """
    logging.info("Changing topology and reverting under traffic")
    # forward
    run_zero_downtime(instance, ssl, do_fn, network_type)
    # revert
    run_zero_downtime(instance, ssl, revert_fn, network_type)
    logging.info("Completed topology change and revert")


def stress_oom(
    instance: OmnistrateFleetInstance,
    ssl=False,
    query_size="small",
    network_type="PUBLIC",
):
    """
    Keep writing until we hit OOM.
    """
    logging.info("Starting stress test to trigger OOM with query size '%s'", query_size)
    db = instance.create_connection(ssl=ssl, network_type=network_type)
    g = db.select_graph("test")
    big = "UNWIND RANGE(1, 100000) AS id CREATE (n:Person {name: 'Alice'})"
    medium = "UNWIND RANGE(1, 25000) AS id CREATE (n:Person {name: 'Alice'})"
    small = "UNWIND RANGE(1, 10000) AS id CREATE (n:Person {name: 'Alice'})"

    ref = os.getenv("BRANCH_NAME")
    logging.info(f"Branch Name: {ref}")

    cypher_query = f'''
    LOAD CSV FROM "https://media.githubusercontent.com/media/FalkorDB/falkordb-omnistrate/refs/heads/{ref}/scripts/data.csv" AS row CREATE (:Person {{name: row[0], age: toInteger(row[1])}})
    '''

    if query_size in ["big", "medium"]:
        logging.info("Loading Data from CSV to speed up OOM trigger")
        if query_size == "big":
            while True:
                g.query(cypher_query)
                memory_str = db.connection.execute_command("INFO","MEMORY")['used_memory_human']
                if 'M' in memory_str:
                    continue
                memory = float(memory_str.replace("G", ""))
                if memory > float(5.5):
                    break
                time.sleep(1)
        elif query_size == "medium":
            while True:
                g.query(cypher_query)
                memory = float(db.connection.execute_command("INFO","MEMORY")['used_memory_human'].replace("M", ""))
                if memory > float(800):
                    break
                time.sleep(1)


    q = small if query_size == "small" else medium if query_size == "medium" else big

    while True:
        try:
            logging.debug("Executing query: %s", q)
            g.query(q)
            time.sleep(1)
        except Exception as e:
            # Different drivers raise different OOM types/strings; be lenient.
            if (
                isinstance(e, OutOfMemoryError)
                or "OOM" in str(e).upper()
                or "OUT OF MEMORY" in str(e).upper()
            ):
                logging.warning("Out of memory condition triggered")
                return
            logging.exception("Unexpected error during stress test")
            raise


def assert_multi_zone(instance: OmnistrateFleetInstance, host_count=6):
    """
    Assert that the instance is multi-zone.
    """
    host_count = int(host_count)
    logging.info("Asserting multi-zone topology")
    network_topology: dict = instance.get_network_topology(force_refresh=True)
    logging.debug(f"Network topology: {network_topology}")
    resource_key = next(
        (
            k
            for [k, v] in network_topology.items()
            if ((v["resourceName"] == "node-mz") or (v["resourceName"] == "cluster-mz"))
        ),
        None,
    )

    resource = network_topology[resource_key]

    nodes = resource["nodes"]

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
            f"Host count does not match number of nodes. Current host count: {host_count}; Number of nodes: {len(nodes)}"
        )

    azs = set(node["availabilityZone"] for node in nodes)
    if len(azs) < 2:
        logging.error(
            "Multi-zone topology expected, but only found %d availability zones: %s",
            len(azs),
            azs,
        )
        raise AssertionError(
            f"Multi-zone topology expected, but only found {len(azs)} availability zones: {azs}"
        )
    logging.debug("Multi-zone topology assertion passed")
