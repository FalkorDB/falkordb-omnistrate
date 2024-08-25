from time import sleep
import os
from falkordb_cluster import FalkorDBCluster, FalkorDBClusterNode
import socket
import redis
import threading
from simple_http_server import route, server, HttpError
import logging


def _get_admin_pass():
    """
    Check if the password exists in the adminpassword file,\n
    if it does not, take the pass from the ADMIN_PASSWORD variable.
    """
    admin_password = os.getenv("ADMIN_PASSWORD")
    if admin_password:
        return admin_password
    secret_path = "/run/secrets/adminpassword"
    try:
        with open(secret_path) as f:
            return f.read().strip()  # Strip any extra whitespace or newlines
    except FileNotFoundError:
        raise FileNotFoundError(f"Secret file '{secret_path}' does not exist.")


HEALTHCHECK_PORT = os.getenv("HEALTHCHECK_PORT", "8081")
ADMIN_PASSWORD = _get_admin_pass()
TLS = os.getenv("TLS", "false") == "true"
CLUSTER_REPLICAS = int(os.getenv("CLUSTER_REPLICAS", "1"))
NODE_PORT = int(os.getenv("NODE_PORT", "6379"))
DEBUG = os.getenv("DEBUG", "0") == "1"
IS_MULTI_ZONE = os.getenv("IS_MULTI_ZONE", "0") == "1"
EXTERNAL_DNS_SUFFIX = os.getenv("EXTERNAL_DNS_SUFFIX")

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO)

MIN_HOST_COUNT = 6
MIN_MASTER_COUNT = 3
MIN_SLAVE_COUNT = 3


NODE_0_HOST = f"cluster-{'mz' if IS_MULTI_ZONE else 'sz'}-0.{EXTERNAL_DNS_SUFFIX}"

healthcheck_ok = False


def _handle_too_many_masters(cluster: FalkorDBCluster, expected_masters: int):
    # Choose one master to become slave from another master that doesn't have enough slaves
    sorted_masters: list[tuple[FalkorDBClusterNode, int]] = []
    for extra_master in cluster.get_masters():
        if len(extra_master.slots) > 0:
            continue
        sorted_masters.append(
            (extra_master, cluster.get_slaves_from_master(extra_master.id))
        )

    print(
        f"Too many masters: expected_masters: {expected_masters}\nsorted_masters: {sorted_masters}"
    )
    sorted_masters = sorted(sorted_masters, key=lambda x: len(x[1]))

    # Select the masters with the least slaves
    extra_masters: list[tuple[FalkorDBClusterNode, int]] = sorted_masters[
        : int(len(sorted_masters) - expected_masters)
    ]

    if len(extra_masters) == 0:
        logging.info("No extra masters to handle")
        return

    if len(extra_masters) == 1:
        logging.info("Only one extra master to handle. Skipping...")
        return

    if len(extra_masters) > 1:
        logging.info(f"{len(extra_masters)} extra masters to handle.")
        groups = cluster.groups(CLUSTER_REPLICAS)
        extra_master = extra_masters[0][0]
        logging.info(f"Extra master: {extra_master}")
        extra_master_group = next(
            (group for group in groups if extra_master in group),
            None,
        )
        group_master = next(
            (node for node in extra_master_group if node.is_master),
            None,
        )
        if group_master is None:
            logging.info(f"Group has no master. Finding another group...")
            for group in groups:
                if (
                    len([node for node in group if node.is_slave]) < CLUSTER_REPLICAS
                    and next((node for node in group if node.is_master), None)
                    is not None
                ):
                    group_master = next(
                        (node for node in group if node.is_master),
                    )
                    logging.info(f"Found group master: {group_master}")
                    break
        cluster.relocate_slave(extra_master.id, group_master.id)
        return main()


def _relocate_master(
    cluster: FalkorDBCluster,
    node: FalkorDBClusterNode,
):
    # Get the first node from the first group that does not have a master

    groups = cluster.groups(CLUSTER_REPLICAS)

    for i, group in enumerate(groups):
        if node in group:
            logging.info(f"Skipping group {i}. Node {node.id} is already in this group")
            continue
        if not any(n.is_master for n in group):
            suitable_relocation_node = group[0]
            break
    else:
        logging.info(f"Cannot relocate master {node}, no suitable node found")
        return

    logging.info(f"Relocating master {node} to {suitable_relocation_node} in group {i}")

    cluster.relocate_master(node.id, suitable_relocation_node.id)

    logging.info(f"Master {node} relocated to {suitable_relocation_node}")

    return main()


def _handle_slave_pointing_to_master_in_different_group(
    cluster: FalkorDBCluster,
    slave: FalkorDBClusterNode,
    slave_master: FalkorDBClusterNode,
    group_master: FalkorDBClusterNode,
    group_slaves: list[FalkorDBClusterNode],
):

    logging.info(f"Slave {slave} has master from different group: {slave_master}")
    # If there's a master in the same group with less replicas than expected, relocate the slave to that master
    if len(group_slaves) < CLUSTER_REPLICAS:
        cluster.relocate_slave(slave.id, group_master.id)
        logging.info(f"Slave {slave} relocated to {group_master}")
        return main()


def _handle_cluster_not_fully_connected(cluster: FalkorDBCluster):

    # Check if cluster status is ready. If not, skip this function.
    if not cluster.is_ready():
        logging.info("Cluster is not ready")
        return

    # If:
    # 1. the nodes that are not connected are all masters
    # 2. they don't have slots assigned
    # 3. and their index is > 6
    # Then delete them
    for node in cluster.nodes:
        if (
            not node.connected
            and node.idx > 6
            and node.is_master
            and len(node.slots) == 0
        ):
            logging.info(f"Deleting master {node}")
            cluster.delete_node(node.id)

    return main()


def main():
    cluster = FalkorDBCluster(
        host=NODE_0_HOST,
        port=NODE_PORT,
        password=ADMIN_PASSWORD,
        ssl=TLS,
    )
    # slots = client.cluster_slots()
    if len(cluster) < MIN_HOST_COUNT:
        logging.info("Not enough hosts to rebalance")
        return

    if not cluster.is_connected():
        logging.info("Cluster is not fully connected")
        return _handle_cluster_not_fully_connected(cluster)

    expected_shards = len(cluster) / (CLUSTER_REPLICAS + 1)
    if expected_shards % 1 != 0:
        logging.info(
            f"Cannot rebalance, expected shards is not an integer: {expected_shards}"
        )
        return

    expected_shards = int(expected_shards)

    if len(cluster) % (CLUSTER_REPLICAS + 1) != 0:
        logging.info(
            f"Cannot rebalance, number of nodes does not match the shards. Nodes: {len(cluster)}, expected_shards: {expected_shards}"
        )
        return

    invalid_slaves = cluster.get_slaves_with_invalid_masters()
    if len(invalid_slaves) > 0:
        logging.info(f"Invalid slaves: {invalid_slaves}")

    expected_masters = expected_shards

    if len(cluster.get_masters()) > expected_masters:
        logging.info(f"Too many masters: {len(cluster.get_masters())}")
        return _handle_too_many_masters(cluster, expected_masters)

    for s in range(0, int(expected_shards)):
        group_start_idx = s * (CLUSTER_REPLICAS + 1)
        group_end_idx = group_start_idx + (CLUSTER_REPLICAS + 1)

        group_master: FalkorDBClusterNode | None = next(
            (
                node
                for node in cluster.nodes[group_start_idx:group_end_idx]
                if node.is_master
            ),
            None,
        )

        if group_master is None:
            logging.info(f"Group {s} has no master")
            continue

        group_slaves: list[FalkorDBClusterNode] = [
            node
            for node in cluster.nodes[group_start_idx:group_end_idx]
            if node.is_slave and node.master_id == group_master.id
        ]

        for i in range(group_start_idx, group_end_idx):
            node = cluster.nodes[i]

            if node.is_master:
                if group_master is None or group_master == node:
                    group_master = node
                    if len(group_master.slots) == 0:
                        logging.info(f"Master {group_master} has no slots")
                        cluster.rebalance_slots(group_master, expected_shards)
                        return main()
                elif IS_MULTI_ZONE:
                    logging.info(
                        f"Group {s} has more than 1 master: {group_master}, {node}"
                    )
                    return _relocate_master(cluster, node)
            else:
                slave_master = cluster.get_node_by_id(node.master_id)
                if slave_master is None:
                    logging.info(f"Slave {node} has no master")
                if slave_master.mode != "master":
                    logging.info(f"Slave {node} has invalid master: {slave_master}")

                # Check if master belongs to the same group as slave
                if IS_MULTI_ZONE and (
                    slave_master.idx < group_start_idx
                    or slave_master.idx >= group_end_idx
                ):
                    return _handle_slave_pointing_to_master_in_different_group(
                        cluster, node, slave_master, group_master, group_slaves
                    )

        if len(group_slaves) != CLUSTER_REPLICAS:
            logging.info(f"Group {s} has invalid number of slaves: {group_slaves}")

    logging.info(f"Cluster after: {cluster}")


def _node_resolved():
    logging.info(f"Checking node connection: {NODE_0_HOST}:{NODE_PORT}")
    # Resolve hostnames to IPs
    try:
        socket.gethostbyname(NODE_0_HOST)
    except Exception as e:
        if DEBUG:
            logging.error(f"Error resolving host: {e}")
        return False

    # ping node
    try:
        client = redis.Redis(
            host=NODE_0_HOST, port=NODE_PORT, password=ADMIN_PASSWORD, ssl=TLS
        )
        client.ping()
        return True
    except Exception as e:
        if DEBUG:
            logging.error(f"Error pinging node: {e}")
        return False


def loop():
    global healthcheck_ok
    while True:
        sleep(10)

        while not _node_resolved():
            healthcheck_ok = False
            sleep(5)

        try:
            main()
            healthcheck_ok = True
        except Exception as e:
            logging.exception(f"Error: {e}")
            healthcheck_ok = False


if __name__ == "__main__":

    threading.Thread(target=loop, daemon=True).start()

    @route("/healthcheck")
    def healthcheck():
        if healthcheck_ok:
            return "OK"
        raise HttpError(500, "Not ready")

    server.start(port=int(HEALTHCHECK_PORT))

    logging.info("Server started")
