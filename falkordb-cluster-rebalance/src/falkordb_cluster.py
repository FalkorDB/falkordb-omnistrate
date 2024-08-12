from redis.cluster import RedisCluster, ClusterNode
from time import sleep, time
import subprocess
from datetime import datetime


class FalkorDBClusterNode:

    def __init__(
        self,
        ip: str,
        port: str,
        id: str,
        hostname: str,
        mode: str,
        master_id: str | None,
        slots: list[list[str]],
        connected: bool,
    ):
        self.ip = ip
        self.port = port
        self.id = id
        self.hostname = hostname
        self.mode = mode
        self.master_id = master_id
        self.slots = slots
        self.connected = connected

    def __repr__(self):
        return (
            f"FalkorDBClusterNode(\n"
            f"  idx={self.idx},\n"
            f"  ip={self.ip},\n"
            f"  port={self.port},\n"
            f"  id={self.id},\n"
            f"  hostname={self.hostname},\n"
            f"  mode={self.mode},\n"
            f"  master_id={self.master_id},\n"
            f"  slots={self.slots}\n"
            f"  connected={self.connected}\n"
            f")"
        )

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other) -> bool:
        return isinstance(other, FalkorDBClusterNode) and self.id == other.id

    @property
    def idx(self) -> int | None:
        if not self.hostname:
            return None

        return (
            int(self.hostname.split(".")[0].split("-")[-1])
            if "-" in self.hostname and "." in self.hostname
            else None
        )

    @property
    def is_master(self) -> bool:
        return self.mode == "master"

    @property
    def is_slave(self) -> bool:
        return self.mode == "slave"

    @staticmethod
    def from_dict(key: str, val: dict) -> "FalkorDBClusterNode":
        return FalkorDBClusterNode(
            key.split(":")[0],
            key.split(":")[1],
            val["node_id"],
            val["hostname"],
            "master" if "master" in val["flags"] else "slave",
            val["master_id"] if "-" not in val["master_id"] else None,
            val["slots"],
            val["connected"],
        )

    def to_cluster_node(self):
        return ClusterNode(self.hostname, self.port)


class FalkorDBCluster:

    nodes: list[FalkorDBClusterNode] = []

    def __init__(self, host: str, port: int, password: str, ssl: bool):
        self.host = host
        self.port = port
        self.password = password
        self.ssl = ssl

        self.client = RedisCluster(host, port, password=password, ssl=ssl)
        self._refresh()

    def _refresh(self) -> "FalkorDBCluster":
        self.nodes = [
            FalkorDBClusterNode.from_dict(key, val)
            for key, val in self.client.cluster_nodes().items()
        ]
        self.sort()
        print(f"{datetime.now()}: Cluster refreshed: {self}")
        return self

    def is_connected(self) -> bool:
        return all(node.connected for node in self.nodes)

    def sort(self) -> "FalkorDBCluster":
        self.nodes = sorted(self.nodes, key=lambda x: x.idx)
        return self

    def __str__(self):
        text = "FalkorDBCluster:\n"

        for node in self.nodes:
            if node.is_master:
                text += f"Master: {node.idx} - {node.id} - {node.slots}\n"
                text += "Slaves:\n" + "\n".join(
                    f"  {slave.idx} - {slave.id}"
                    for slave in self.nodes
                    if slave.master_id == node.id
                )
                text += "\n----\n"

        return text

    def __repr__(self):
        return f"FalkorDBCluster({self.nodes})"

    def __len__(self):
        return len(self.nodes)

    def __contains__(self, node_id: str) -> bool:
        return any(node.id == node_id for node in self.nodes)

    def get_node_by_id(self, node_id: str) -> FalkorDBClusterNode | None:
        return next((node for node in self.nodes if node.id == node_id), None)

    def get_slaves_with_invalid_masters(self) -> list[FalkorDBClusterNode]:
        return [
            node
            for node in self.nodes
            if node.is_slave and self.get_node_by_id(node.master_id) is None
        ]

    def get_masters(self) -> list[FalkorDBClusterNode]:
        return [node for node in self.nodes if node.is_master]

    def get_slaves_from_master(self, master_id: str) -> list[FalkorDBClusterNode]:
        return [node for node in self.nodes if node.master_id == master_id]

    def groups(self, replicas) -> list[list[FalkorDBClusterNode]]:
        return [
            self.nodes[i : i + replicas + 1]
            for i in range(0, len(self.nodes), replicas + 1)
        ]

    def relocate_master(
        self, old_master_id: str, new_master_id: str, timeout: int = 180
    ):
        """
        Relocate a master to a new node
        """
        # Steps:
        # 1. Make the new master a slave of the old master
        # 2. Perform a failover on the old master

        new_master_node = self.get_node_by_id(new_master_id)
        old_master_node = self.get_node_by_id(old_master_id)

        res = self.client.cluster_replicate(
            new_master_node.to_cluster_node(), old_master_id
        )

        print(
            f"{datetime.now()}: Replicate {new_master_node} to {old_master_node} at: {res}"
        )

        self._wait_for_condition(
            lambda: self.get_node_by_id(new_master_id).is_slave
            and self.get_node_by_id(new_master_id).master_id == old_master_id,
            timeout,
            "Timed out waiting for the new master to become a slave",
        )

        sleep(10)

        res = self.client.cluster_failover(new_master_node.to_cluster_node())

        print(f"{datetime.now()}: Failover {old_master_node}: {res}")

        self._wait_for_condition(
            lambda: self.get_node_by_id(old_master_id).is_slave
            and self.get_node_by_id(old_master_id).master_id == new_master_id,
            timeout,
            "Timed out waiting for the old master to become a slave",
        )

    def _wait_for_condition(
        self,
        condition,
        timeout: int = 180,
        error_message: str = "Timed out waiting for condition",
    ):
        now = time()
        while not condition():
            if time() - now > timeout:
                raise TimeoutError(error_message)
            sleep(5)
            self._refresh()

    def relocate_slave(self, slave_id: str, new_master_id: str):
        """
        Relocate a slave to a new node
        """
        slave_node = self.get_node_by_id(slave_id)
        new_master_node = self.get_node_by_id(new_master_id)

        res = self.client.cluster_replicate(slave_node.to_cluster_node(), new_master_id)

        print(f"{datetime.now()}: Replicate {slave_node} to {new_master_node}: {res}")

        self._wait_for_condition(
            lambda: self.get_node_by_id(slave_id).is_slave
            and self.get_node_by_id(slave_id).master_id == new_master_id,
            180,
            "Timed out waiting for the slave to become a slave",
        )

    def rebalance_slots(self, new_node: FalkorDBClusterNode, shards: int):

        slot_count = 16384 // shards

        print(f"Reshard to {slot_count} slots. New node: {new_node.id}")
        res = subprocess.call(
            [
                "redis-cli",
                "-h",
                self.host,
                "-p",
                f"{self.port}",
                "--cluster",
                "reshard",
                f"{new_node.hostname}",
                f"{new_node.port}",
                "--cluster-from",
                "all",
                "--cluster-to",
                new_node.id,
                "--cluster-slots",
                f"{slot_count}",
                "--cluster-yes",
            ]
        )

        print(f"Reshard result: {res}")

        self._wait_for_condition(
            lambda: all(len(node.slots) > 0 for node in self.get_masters()),
            180,
            "Timed out waiting for all nodes to have the correct number of slots",
        )
