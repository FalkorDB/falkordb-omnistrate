from time import sleep
import os
from falkordb_cluster import FalkorDBCluster, FalkorDBClusterNode
import socket
import redis

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
TLS = os.getenv("TLS", "false") == "true"
CLUSTER_REPLICAS = int(os.getenv("CLUSTER_REPLICAS", "1"))
NODE_HOST = os.getenv("NODE_HOST", "localhost")
NODE_PORT = int(os.getenv("NODE_PORT", "6379"))
DEBUG = os.getenv("DEBUG", "0") == "1"
DISTRIBUTE_ACROSS_ZONES = os.getenv("DISTRIBUTE_ACROSS_ZONES", "0") == "1"

MIN_HOST_COUNT = 6
MIN_MASTER_COUNT = 3
MIN_SLAVE_COUNT = 3


def _handle_too_many_masters(cluster: FalkorDBCluster):
    # Choose one master to become slave from another master that doesn't have enough slaves
    extra_masters = [
        master
        for master in cluster.get_masters()
        if len(cluster.get_slaves_from_master(master.id)) < CLUSTER_REPLICAS
    ]

    if len(extra_masters) == 0:
        print("No extra masters to relocate")
        return

    if len(extra_masters) == 1:
        print("Only one extra master to relocate. Skipping...")
        return

    if len(extra_masters) > 1:
        print(f"{len(extra_masters)} extra masters to relocate.")
        cluster.relocate_slave(extra_masters[1].id, extra_masters[0].id)
        return main()


def _relocate_master(
    cluster: FalkorDBCluster,
    node: FalkorDBClusterNode,
):
    # Get the first node from the first group that does not have a master

    groups = cluster.groups(CLUSTER_REPLICAS)

    for i, group in enumerate(groups):
        if node in group:
            print(f"Skipping group {i}. Node {node.id} is already in this group")
            continue
        if not any(n.mode == "master" for n in group):
            suitable_relocation_node = group[0]
            break
    else:
        print(f"Cannot relocate master {node}, no suitable node found")
        return

    print(f"Relocating master {node} to {suitable_relocation_node} in group {i}")

    cluster.relocate_master(node.id, suitable_relocation_node.id)

    print(f"Master {node} relocated to {suitable_relocation_node}")

    return main()


def _handle_slave_pointing_to_master_in_different_group(
    cluster: FalkorDBCluster,
    slave: FalkorDBClusterNode,
    slave_master: FalkorDBClusterNode,
    group_master: FalkorDBClusterNode,
    group_slaves: list[FalkorDBClusterNode],
):

    print(f"Slave {slave} has master from different group: {slave_master}")
    # If there's a master in the same group with less replicas than expected, relocate the slave to that master
    if len(group_slaves) < CLUSTER_REPLICAS:
        cluster.relocate_slave(slave.id, group_master.id)
        print(f"Slave {slave} relocated to {group_master}")
        return main()


def main():
    cluster = FalkorDBCluster(
        host=NODE_HOST,
        port=NODE_PORT,
        password=ADMIN_PASSWORD,
        ssl=TLS,
    )
    # slots = client.cluster_slots()
    if len(cluster) < MIN_HOST_COUNT:
        print("Not enough hosts to rebalance")
        return

    if not cluster.is_connected():
        print("Cluster is not fully connected")
        return

    expected_shards = len(cluster) / (CLUSTER_REPLICAS + 1)
    if expected_shards % 1 != 0:
        print(f"Cannot rebalance, expected shards is not an integer: {expected_shards}")
        return

    if len(cluster) % (CLUSTER_REPLICAS + 1) != 0:
        print(
            f"Cannot rebalance, number of nodes does not match the shards. Nodes: {len(cluster)}, expected_shards: {expected_shards}"
        )
        return

    invalid_slaves = cluster.get_slaves_with_invalid_masters()
    if len(invalid_slaves) > 0:
        print(f"Invalid slaves: {invalid_slaves}")

    expected_masters = expected_shards

    if len(cluster.get_masters()) > expected_masters:
        print(f"Too many masters: {len(cluster.get_masters())}")
        return _handle_too_many_masters(cluster)

    for s in range(0, int(expected_shards)):
        group_start_idx = s * (CLUSTER_REPLICAS + 1)
        group_end_idx = group_start_idx + (CLUSTER_REPLICAS + 1)

        group_master: FalkorDBClusterNode | None = next(
            (
                node
                for node in cluster.nodes[group_start_idx:group_end_idx]
                if node.mode == "master"
            ),
            None,
        )

        if group_master is None:
            print(f"Group {s} has no master")
            continue

        group_slaves: list[FalkorDBClusterNode] = [
            node
            for node in cluster.nodes[group_start_idx:group_end_idx]
            if node.mode == "slave" and node.master_id == group_master.id
        ]

        for i in range(group_start_idx, group_end_idx):
            node = cluster.nodes[i]

            if node.mode == "master":
                if group_master is None or group_master == node:
                    group_master = node
                    if len(group_master.slots) == 0:
                        print(f"Master {group_master} has no slots")
                        cluster.rebalance_slots(group_master, expected_shards)
                        return main()
                elif DISTRIBUTE_ACROSS_ZONES:
                    print(f"Group {s} has more than 1 master: {group_master}, {node}")
                    return _relocate_master(cluster, node)
            else:
                slave_master = cluster.get_node_by_id(node.master_id)
                if slave_master is None:
                    print(f"Slave {node} has no master")
                if slave_master.mode != "master":
                    print(f"Slave {node} has invalid master: {slave_master}")

                # Check if master belongs to the same group as slave
                if DISTRIBUTE_ACROSS_ZONES and (
                    slave_master.idx < group_start_idx
                    or slave_master.idx >= group_end_idx
                ):
                    return _handle_slave_pointing_to_master_in_different_group(
                        cluster, node, slave_master, group_master, group_slaves
                    )

        if DISTRIBUTE_ACROSS_ZONES and len(group_slaves) != CLUSTER_REPLICAS:
            print(f"Group {s} has invalid number of slaves: {group_slaves}")

    print(f"Cluster after: {cluster}")


def _node_resolved():
    print("Checking node connection...")
    # Resolve hostnames to IPs
    try:
        socket.gethostbyname(NODE_HOST)
    except Exception as e:
        return False

    # ping node
    try:
        client = redis.Redis(
            host=NODE_HOST, port=NODE_PORT, password=ADMIN_PASSWORD, ssl=TLS
        )
        client.ping()
    except Exception as e:
        return False


while True:
    if not DEBUG:
        sleep(10)

    while not _node_resolved():
        sleep(5)

    try:
        main()
    except Exception as e:
        print(f"Error: {e}")
    if DEBUG:
        break
