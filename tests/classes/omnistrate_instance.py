"""Define a class for an Omnistrate instance, with useful methods to deploy, trigger failover, get the access endpoints, and delete the instance."""

import json
import requests
import base64
import jwt
import time
import random
import string
from falkordb import FalkorDB

DEFAULT_API_URL = "https://api.omnistrate.cloud/"


class OmnistrateInstance:

    _network_topology = None
    _connection: FalkorDB = None

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        api_path: str = "",
        api_create_instance_path: str = "",
        api_failover_path: str = "",
        api_sign_in_path: str = "",
        subscription_id: str = "",
        omnistrate_user: str = "",
        omnistrate_password: str = "",
        deployment_create_timeout_seconds: int = 1200,
        deployment_delete_timeout_seconds: int = 1200,
        deployment_failover_timeout_seconds: int = 1500,
    ) -> None:

        assert len(api_path) > 0, "api_path must be provided"
        assert len(api_sign_in_path) > 0, "api_sign_in_path must be provided"
        assert len(omnistrate_user) > 0, "omnistrate_user must be provided"
        assert len(omnistrate_password) > 0, "omnistrate_password must be provided"

        self.api_url = api_url
        self.api_path = api_path
        self.api_create_instance_path = api_create_instance_path or api_path
        self.api_failover_path = api_failover_path
        self.api_sign_in_path = api_sign_in_path
        self.subscription_id = subscription_id
        self._omnistrate_user = omnistrate_user
        self._omnistrate_password = omnistrate_password
        self.subscription_id_query = f"?subscriptionId={subscription_id}"

        self.deployment_create_timeout_seconds = deployment_create_timeout_seconds
        self.deployment_delete_timeout_seconds = deployment_delete_timeout_seconds
        self.deployment_failover_timeout_seconds = deployment_failover_timeout_seconds

        self._token = None
        self.instance_id = None

    def _get_token(self):
        """Get a token to authenticate with the API."""
        # Check if token is valid
        if (
            self._token is not None
            and jwt.decode(
                self._token, options={"verify_signature": False}, algorithms=["EdDSA"]
            ).get("exp")
            > time.time()
        ):
            return self._token

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Basic "
            + base64.b64encode(
                (self._omnistrate_user + ":" + self._omnistrate_password).encode(
                    "utf-8"
                )
            ).decode("utf-8"),
        }
        print("Getting token")
        response = requests.post(
            self.api_url + self.api_sign_in_path, headers=headers, timeout=15
        )

        self._handle_response(response, "Failed to get token")

        self._token = response.json()["token"]
        print("Token received")
        return self._token

    def _handle_response(self, response, message):
        if response.status_code >= 300 or response.status_code < 200:
            print(f"{message}: {response.text}")
            raise Exception(f"{message}")

    def create(
        self,
        wait_for_ready: bool,
        deployment_cloud_provider: str,
        deployment_region: str,
        name: str,
        description: str,
        falkordb_user: str,
        falkordb_password: str,
        product_tier_version: str | None = None,
        **kwargs,
    ) -> str:
        """Create an instance with the specified parameters. Optionally wait for the instance to be ready."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        data = {
            "cloud_provider": deployment_cloud_provider,
            "region": deployment_region,
            "requestParams": {
                "name": name,
                "description": description,
                "falkordbUser": falkordb_user,
                "falkordbPassword": falkordb_password,
                **kwargs,
            },
            "productTierVersion": product_tier_version,
        }

        print(f"Creating instance {name}" + f" with parameters: {data}")

        response = requests.post(
            self.api_url + self.api_create_instance_path + self.subscription_id_query,
            headers=headers,
            data=json.dumps(data),
            timeout=15,
        )

        self._handle_response(response, f"Failed to create instance {name}")

        self.instance_id = response.json()["id"]

        print(f"Instance {name} created: {self.instance_id}")

        if not wait_for_ready:
            return

        try:
            self.wait_for_ready(timeout_seconds=self.deployment_create_timeout_seconds)
        except Exception:
            raise Exception(f"Failed to create instance {name}")

    def delete(self, wait_for_delete: bool):
        """Delete the instance. Optionally wait for the instance to be deleted."""

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        response = requests.delete(
            self.api_url
            + self.api_path
            + "/"
            + self.instance_id
            + self.subscription_id_query,
            headers=headers,
            timeout=15,
        )

        self._handle_response(response, f"Failed to delete instance {self.instance_id}")

        if not wait_for_delete:
            return

        try:
            self.wait_for_ready(timeout_seconds=self.deployment_delete_timeout_seconds)
        except Exception as e:
            if e.args[0] == "Timeout":
                raise Exception(f"Failed to delete instance {self.instance_id}")

    def trigger_failover(
        self, replica_id: str, wait_for_ready: bool, resource_id: str = None
    ):
        """Trigger failover for the instance. Optionally wait for the instance to be ready."""
        assert len(self.api_failover_path) > 0, "api_failover_path must be provided"
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        data = {
            "failedReplicaID": replica_id,
            "failedReplicaAction": "FAILOVER_AND_RESTART",
        }

        url = (
            self.api_url
            + self.api_failover_path
            + "/"
            + (f"{resource_id}/" if resource_id is not None else "")
            + self.instance_id
            + "/failover"
            + self.subscription_id_query
        )

        print(f"Calling URL {url}")

        response = requests.post(
            url,
            headers=headers,
            data=json.dumps(data),
            timeout=15,
        )

        self._handle_response(
            response, f"Failed to trigger failover for instance {self.instance_id}"
        )

        if not wait_for_ready:
            return

        self.wait_for_ready(timeout_seconds=self.deployment_failover_timeout_seconds)

    def update_instance_type(
        self, new_instance_type: str, wait_until_ready: bool = True, retry=5
    ):
        """Update the instance type."""

        self.wait_for_ready()

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        data = {
            "nodeInstanceType": new_instance_type,
        }

        response = requests.put(
            self.api_url
            + self.api_path
            + "/"
            + self.instance_id
            + self.subscription_id_query,
            headers=headers,
            data=json.dumps(data),
            timeout=15,
        )

        if "another operation is already in progress" in str(response.text):
            if retry == 0:
                raise Exception(
                    f"Failed to update instance type {self.instance_id} after {retry} retries"
                )
            time.sleep(60)
            return self.update_instance_type(
                new_instance_type, wait_until_ready, retry - 1
            )

        self._handle_response(
            response, f"Failed to update instance type {self.instance_id}"
        )

        if not wait_until_ready:
            return

        self.wait_for_ready(timeout_seconds=self.deployment_failover_timeout_seconds)

    def _get_network_topology(self):

        if self._network_topology is not None:
            return self._network_topology

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        response = requests.get(
            self.api_url
            + self.api_path
            + "/"
            + self.instance_id
            + self.subscription_id_query,
            headers=headers,
            timeout=15,
        )

        self._handle_response(
            response, f"Failed to get instance connection data {self.instance_id}"
        )

        self._network_topology = response.json()["detailedNetworkTopology"]

        return self._network_topology

    def get_connection_endpoints(self):
        """Get the connection endpoints for the instance."""

        resources = self._get_network_topology()

        resources_keys = resources.keys()

        endpoints = []
        for key in resources_keys:
            if "nodes" in resources[key] and len(resources[key]["nodes"]) > 0:
                for node in resources[key]["nodes"]:
                    endpoints.append(
                        {
                            "id": node["id"],
                            "endpoint": node["endpoint"],
                            "ports": node["ports"],
                        }
                    )

        if len(endpoints) == 0:
            raise Exception("No endpoints found")

        return endpoints

    def get_cluster_endpoint(self):
        resources = self._get_network_topology()

        resources_keys = resources.keys()

        for key in resources_keys:
            if (
                "clusterEndpoint" in resources[key]
                and len(resources[key]["clusterEndpoint"]) > 0
                and "@streamer" not in resources[key]["clusterEndpoint"]
            ):
                return {
                    "endpoint": resources[key]["clusterEndpoint"],
                    "ports": resources[key]["clusterPorts"],
                }

    def wait_for_ready(self, timeout_seconds: int = 1200):
        """Wait for the instance to be ready."""
        timeout_timer = time.time() + timeout_seconds

        while True:
            if time.time() > timeout_timer:
                raise Exception("Timeout")

            state = self._get_instance_state()
            if state == "RUNNING":
                print("Instance is ready")
                break
            elif state == "FAILED":
                print("Instance is in error state")
                raise Exception("Instance is in error state")
            else:
                print("Instance is in " + state + " state")
                time.sleep(5)

    def _get_instance_state(self, retries=5):
        """Get the state of the instance."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        while retries > 0:

            response = requests.get(
                self.api_url
                + self.api_path
                + "/"
                + self.instance_id
                + self.subscription_id_query,
                headers=headers,
                timeout=15,
            )

            if response.status_code >= 500:
                retries -= 1
                time.sleep(3)
                continue
            else:
                break

        self._handle_response(
            response, f"Failed to get instance state {self.instance_id}"
        )

        return response.json()["status"]

    def create_connection(
        self, ssl: bool = False, force_reconnect: bool = False, retries=5
    ):

        if self._connection is not None and not force_reconnect:
            return self._connection

        endpoint = self.get_cluster_endpoint()

        # Connect to the master node
        while retries > 0:
            try:
                self._connection = FalkorDB(
                    host=endpoint["endpoint"],
                    port=endpoint["ports"][0],
                    username="falkordb",
                    password="falkordb",
                    ssl=ssl,
                )
                break
            except Exception as e:
                print(f"Failed to connect to the master node: {e}")
                retries -= 1
                time.sleep(30)

        if self._connection is None:
            raise Exception("Failed to connect to the master node")

        return self._connection

    def generate_data(self, graph_count: int):
        """Generate data for the instance."""

        db = self.create_connection()

        for i in range(0, graph_count):
            print(f"creating graph {i} out of {graph_count}")

            name = rand_string()
            g = db.select_graph(name)

            node_count = rand_range(2000, 1000000)
            node_count = rand_range(200, 1000)
            g.query(
                """UNWIND range (0, $node_count) as x
                    CREATE (a:L {v:x})-[:R]->(b:X {v: tostring(x)}), (a)-[:Z]->(:Y {v:tostring(x)})""",
                {"node_count": node_count},
            )


def rand_range(a, b):
    return random.randint(a, b)


def rand_string(l=12):
    letters = string.ascii_letters
    return "".join(random.choice(letters) for _ in range(l))
