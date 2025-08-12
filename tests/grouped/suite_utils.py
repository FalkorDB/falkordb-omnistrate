import time
import threading
import logging
from redis.exceptions import OutOfMemoryError

log = logging.getLogger(__name__)


def add_data(instance, ssl=False, key="test", n=1):
    db = instance.create_connection(ssl=ssl)
    g = db.select_graph(key)
    for _ in range(n):
        g.query("CREATE (n:Person {name: 'Alice'})")


def has_data(instance, ssl=False, key="test", min_rows=1):
    db = instance.create_connection(ssl=ssl, force_reconnect=True)
    g = db.select_graph(key)
    rs = g.query("MATCH (n:Person) RETURN n")
    return len(rs.result_set) >= min_rows


def assert_data(instance, ssl=False, key="test", min_rows=1, msg="data missing"):
    if not has_data(instance, ssl=ssl, key=key, min_rows=min_rows):
        raise AssertionError(msg)


def zero_downtime_worker(stop_evt, error_evt, instance, ssl=False, key="test"):
    try:
        db = instance.create_connection(ssl=ssl, force_reconnect=True)
        g = db.select_graph(key)
        while not stop_evt.is_set():
            g.query("CREATE (n:Person {name: 'Alice'})")
            g.ro_query("MATCH (n:Person {name: 'Alice'}) RETURN n")
            time.sleep(2)
    except Exception as e:
        log.exception(e)
        error_evt.set()


def run_zero_downtime(instance, ssl, fn):
    """
    Run fn while generating continuous R/W traffic.
    Use this for replicated/clustered topologies only.
    """
    stop_evt = threading.Event()
    err_evt = threading.Event()
    th = threading.Thread(
        target=zero_downtime_worker, args=(stop_evt, err_evt, instance, ssl)
    )
    th.start()
    try:
        fn()
    finally:
        stop_evt.set()
        th.join()
    if err_evt.is_set():
        raise AssertionError("Zero-downtime traffic encountered an error")


def change_then_revert(instance, ssl, do_fn, revert_fn):
    """
    Runs do_fn under traffic, then *always* reverts back under traffic.
    Use for topology changes (replicas/shards) that must be undone before the next test.
    """
    # forward
    run_zero_downtime(instance, ssl, do_fn)
    # revert
    run_zero_downtime(instance, ssl, revert_fn)


def stress_oom(instance, ssl=False, resource_key=None):
    """
    Keep writing until we hit OOM.
    """
    db = instance.create_connection(ssl=ssl)
    g = db.select_graph("test")
    big = "UNWIND RANGE(1, 100000) AS id CREATE (n:Person {name: 'Alice'})"
    small = "UNWIND RANGE(1, 10000) AS id CREATE (n:Person {name: 'Alice'})"
    q = small if resource_key == "free" else big
    while True:
        try:
            g.query(q)
        except Exception as e:
            # Different drivers raise different OOM types/strings; be lenient.
            if (
                isinstance(e, OutOfMemoryError)
                or "OOM" in str(e).upper()
                or "OUT OF MEMORY" in str(e).upper()
            ):
                return
            raise


def assert_multi_zone(instance, host_count=6):
    """
    Assert that the instance is multi-zone.
    """
    network_topology: dict = instance.get_network_topology(force_refresh=True)

    resource_key = next(
        (k for [k, v] in network_topology.items() if v["resourceName"] == "-mz"),
        None,
    )

    resource = network_topology[resource_key]

    nodes = resource["nodes"]

    if len(nodes) == 0:
        raise AssertionError("No nodes found in network topology")

    if len(nodes) != host_count:
        raise AssertionError(
            f"Host count does not match number of nodes. Current host count: {host_count}; Number of nodes: {len(nodes)}"
        )

    azs = set(node["availabilityZone"] for node in nodes)
    if len(azs) < 2:
        raise AssertionError(
            f"Multi-zone topology expected, but only found {len(azs)} availability zones: {azs}"
        )
