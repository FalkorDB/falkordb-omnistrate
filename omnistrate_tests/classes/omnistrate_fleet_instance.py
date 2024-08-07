from falkordb import FalkorDB
import json
import os
import time
import random
import string
from requests import exceptions
import omnistrate_tests.classes.omnistrate_fleet_api


def rand_range(a, b):
    return random.randint(a, b)


def rand_string(l=12):
    letters = string.ascii_letters
    return "".join(random.choice(letters) for _ in range(l))


class OmnistrateFleetInstance:

    instance_id: str = None
    _network_topology = None
    _connection: FalkorDB = None

    def __init__(
        self,
        fleet_api: omnistrate_tests.classes.omnistrate_fleet_api.OmnistrateFleetAPI,
        service_id: str = os.getenv("SERVICE_ID"),
        service_provider_id: str = os.getenv("SERVICE_PROVIDER_ID"),
        service_key: str = os.getenv("SERVICE_KEY"),
        service_api_version: str = os.getenv("SERVICE_API_VERSION"),
        service_environment_key: str = os.getenv("SERVICE_ENVIRONMENT_KEY"),
        service_environment_id: str = os.getenv("SERVICE_ENVIRONMENT_ID"),
        service_model_key: str = os.getenv("SERVICE_MODEL_KEY"),
        product_tier_key: str = os.getenv("PRODUCT_TIER_KEY"),
        resource_key: str = os.getenv("RESOURCE_KEY"),
        subscription_id: str = os.getenv("SUBSCRIPTION_ID"),
        deployment_create_timeout_seconds: int = int(
            os.getenv("DEPLOYMENT_CREATE_TIMEOUT_SECONDS", "1200")
        ),
        deployment_delete_timeout_seconds: int = int(
            os.getenv("DEPLOYMENT_DELETE_TIMEOUT_SECONDS", "1200")
        ),
        deployment_failover_timeout_seconds: int = int(
            os.getenv("DEPLOYMENT_FAILOVER_TIMEOUT_SECONDS", "1500")
        ),
    ):
        assert (
            service_provider_id is not None
        ), "Missing service_provider_id or SERVICE_PROVIDER_ID environment variable"
        assert (
            service_key is not None
        ), "Missing service_key or SERVICE_KEY environment variable"
        assert (
            service_api_version is not None
        ), "Missing service_api_version or SERVICE_API_VERSION environment variable"
        assert (
            service_environment_key is not None
        ), "Missing service_environment_key or SERVICE_ENVIRONMENT_KEY environment variable"
        assert (
            service_environment_id is not None
        ), "Missing service_environment_id or SERVICE_ENVIRONMENT_ID environment variable"
        assert (
            service_model_key is not None
        ), "Missing service_model_key or SERVICE_MODEL_KEY environment variable"
        assert (
            product_tier_key is not None
        ), "Missing product_tier_key or PRODUCT_TIER_KEY environment variable"
        assert (
            resource_key is not None
        ), "Missing resource_key or RESOURCE_KEY environment variable"

        self._fleet_api = fleet_api
        self.service_id = service_id
        self.service_provider_id = service_provider_id
        self.service_key = service_key
        self.service_api_version = service_api_version
        self.service_environment_key = service_environment_key
        self.service_environment_id = service_environment_id
        self.service_model_key = service_model_key
        self.product_tier_key = product_tier_key
        self.resource_key = resource_key
        self.subscription_id = subscription_id

        self.deployment_create_timeout_seconds = deployment_create_timeout_seconds
        self.deployment_delete_timeout_seconds = deployment_delete_timeout_seconds
        self.deployment_failover_timeout_seconds = deployment_failover_timeout_seconds

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

        print(f"Creating instance {name}")

        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/fleet/resource-instance/{self.service_provider_id}/{self.service_key}/{self.service_api_version}/{self.service_environment_key}/{self.service_model_key}/{self.product_tier_key}/{self.resource_key}?subscriptionId={self.subscription_id}",
            data=json.dumps(data),
            timeout=15,
        )

        self._fleet_api.handle_response(response, f"Failed to create instance {name}")

        self.instance_id = response.json()["id"]

        print(f"Instance {name} created: {self.instance_id}")

        if not wait_for_ready:
            return

        try:
            self.wait_for_instance_status(
                timeout_seconds=self.deployment_create_timeout_seconds
            )
        except Exception:
            raise Exception(f"Failed to create instance {name}")

    def wait_for_instance_status(
        self, requested_status="RUNNING", timeout_seconds: int = 1200
    ):
        """Wait for the instance to be ready."""
        timeout_timer = time.time() + int(timeout_seconds or 1200)

        while True:
            if time.time() > timeout_timer:
                raise Exception("Timeout")

            status = self.get_instance_details()["status"]
            if status == requested_status:
                print(f"Instance is {requested_status}")
                break
            elif status == "FAILED":
                print("Instance is in error state")
                raise Exception("Instance is in error state")
            else:
                print("Instance is in " + status + " state")
                time.sleep(5)

    def get_instance_details(self, retries=5):
        """Get the details of the instance."""

        while retries > 0:

            try:

                response = self._fleet_api.client().get(
                    f"{self._fleet_api.base_url}/fleet/service/{self.service_id}/environment/{self.service_environment_id}/instance/{self.instance_id}",
                    timeout=15,
                )

            except exceptions.ReadTimeout as e:
                retries -= 1
                time.sleep(3)
                continue

            if response.status_code >= 500:
                retries -= 1
                time.sleep(3)
                continue
            else:
                break

        self._fleet_api.handle_response(
            response, f"Failed to get instance state {self.instance_id}"
        )

        return response.json()["consumptionResourceInstanceResult"]

    def get_resource_id(self, resource_key: str = None) -> str | None:
        """Get the resource ID of the instance."""

        network_topology = self.get_network_topology()

        # find key for object with the correct resourceKey

        for key in network_topology.keys():
            if network_topology[key]["resourceKey"] == (
                resource_key or self.resource_key
            ):
                return key

    def delete(self, wait_for_delete: bool):
        """Delete the instance. Optionally wait for the instance to be deleted."""

        resource_id = self.get_resource_id()

        if resource_id is None:
            raise Exception(f"Resource ID not found for instance {self.instance_id}")

        response = self._fleet_api.client().delete(
            f"{self._fleet_api.base_url}/fleet/service/{self.service_id}/environment/{self.service_environment_id}/instance/{self.instance_id}",
            timeout=15,
            data=json.dumps({"resourceId": resource_id}),
        )

        self._fleet_api.handle_response(
            response, f"Failed to delete instance {self.instance_id}"
        )

        if not wait_for_delete:
            return

        try:
            self.wait_for_instance_status(
                timeout_seconds=self.deployment_delete_timeout_seconds
            )
        except Exception as e:
            if e.args[0] == "Timeout":
                raise Exception(f"Failed to delete instance {self.instance_id}")

    def stop(self, wait_for_ready: bool, retry=5):
        """Stop the instance. Optionally wait for the instance to be ready."""

        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/fleet/service/{self.service_id}/environment/{self.service_environment_id}/instance/{self.instance_id}/stop",
            timeout=15,
            data=json.dumps({"resourceId": self.get_resource_id()}),
        )

        if "another operation is already in progress" in response.text and retry > 0:
            time.sleep(60)
            return self.stop(wait_for_ready, retry - 1)

        self._fleet_api.handle_response(
            response, f"Failed to stop instance {self.instance_id}"
        )

        if not wait_for_ready:
            return

        self.wait_for_instance_status(
            requested_status="STOPPED",
            timeout_seconds=self.deployment_failover_timeout_seconds,
        )

    def start(self, wait_for_ready: bool, retry=5):
        """Start the instance. Optionally wait for the instance to be ready."""

        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/fleet/service/{self.service_id}/environment/{self.service_environment_id}/instance/{self.instance_id}/start",
            timeout=15,
            data=json.dumps({"resourceId": self.get_resource_id()}),
        )

        if "another operation is already in progress" in response.text and retry > 0:
            time.sleep(60)
            return self.start(wait_for_ready, retry - 1)

        self._fleet_api.handle_response(
            response, f"Failed to start instance {self.instance_id}"
        )

        if not wait_for_ready:
            return

        self.wait_for_instance_status(
            timeout_seconds=self.deployment_failover_timeout_seconds
        )

    def trigger_failover(
        self, replica_id: str, wait_for_ready: bool, resource_id: str = None, retry=5
    ):
        """Trigger failover for the instance. Optionally wait for the instance to be ready."""
        print(f"Triggering failover for instance {self.instance_id}")

        data = {
            "failedReplicaID": replica_id,
            "failedReplicaAction": "FAILOVER_AND_RESTART",
            "resourceId": resource_id or self.get_resource_id(),
        }

        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/fleet/service/{self.service_id}/environment/{self.service_environment_id}/instance/{self.instance_id}/failover",
            data=json.dumps(data),
            timeout=15,
        )

        if "another operation is already in progress" in response.text and retry > 0:
            time.sleep(60)
            return self.trigger_failover(
                replica_id, wait_for_ready, resource_id, retry - 1
            )

        self._fleet_api.handle_response(
            response, f"Failed to trigger failover for instance {self.instance_id}"
        )

        if not wait_for_ready:
            return

        self.wait_for_instance_status(
            timeout_seconds=self.deployment_failover_timeout_seconds
        )

    def update_instance_type(
        self, new_instance_type: str, wait_until_ready: bool = True, retry=5
    ):
        """Update the instance type."""

        data = {
            "nodeInstanceType": new_instance_type,
        }

        return self.update_params(wait_until_ready, retry, **data)

    def update_params(self, wait_until_ready: bool = True, retry=5, **kwargs):
        """Update the instance parameters."""

        self.wait_for_instance_status()

        data = kwargs

        response = self._fleet_api.client().patch(
            f"{self._fleet_api.base_url}/resource-instance/{self.service_provider_id}/{self.service_key}/{self.service_api_version}/{self.service_environment_key}/{self.service_model_key}/{self.product_tier_key}/{self.resource_key}/{self.instance_id}",
            data=json.dumps(data),
            timeout=15,
        )

        if "another operation is already in progress" in str(response.text):
            if retry == 0:
                raise Exception(
                    f"Failed to update instance type {self.instance_id} after {retry} retries"
                )
            time.sleep(60)
            return self.update_params(wait_until_ready, retry - 1, **kwargs)

        self._fleet_api.handle_response(
            response, f"Failed to update instance type {self.instance_id}"
        )

        if not wait_until_ready:
            return

        self.wait_for_instance_status(
            timeout_seconds=self.deployment_failover_timeout_seconds
        )

    def upgrade(
        self,
        service_id: str,
        product_tier_id: str,
        source_version: str,
        target_version: str,
        wait_until_ready: bool = False,
        upgrade_timeout: int = 1200,
    ):

        data = {
            "sourceVersion": source_version,
            "targetVersion": target_version,
            "upgradeFilters": {"INSTANCE_IDS": [self.instance_id]},
        }

        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/fleet/service/{service_id}/productTier/{product_tier_id}/upgrade-path",
            json=data,
            timeout=15,
        )

        self._fleet_api.handle_response(response, "Failed to upgrade instance")

        upgrade_id = response.json()["upgradePathId"]

        if wait_until_ready:
            self._wait_until_upgrade_ready(
                service_id, product_tier_id, upgrade_id, upgrade_timeout
            )

    def _wait_until_upgrade_ready(
        self,
        service_id: str,
        product_tier_id: str,
        upgrade_id: str,
        upgrade_timeout: int = 1200,
    ):
        right_now = time.time()
        while True:
            response = self._fleet_api.client().get(
                f"{self._fleet_api.base_url}/fleet/service/{service_id}/productTier/{product_tier_id}/upgrade-path/{upgrade_id}",
                timeout=15,
            )

            self._fleet_api.handle_response(response, "Failed to get upgrade status")

            status = response.json()["status"]

            if status == "IN_PROGRESS":
                print("Upgrade in progress")
                time.sleep(10)
                print("Waiting for instance to be ready")
            elif status == "COMPLETE":
                print("Upgrade completed")
                break
            else:
                raise Exception(f"Upgrade failed: {status}")

            if time.time() - right_now > upgrade_timeout:
                raise Exception("Upgrade timed out")

    def get_network_topology(self):

        if self._network_topology is not None:
            return self._network_topology

        self._network_topology = self.get_instance_details()["detailedNetworkTopology"]

        return self._network_topology

    def get_connection_endpoints(self):
        """Get the connection endpoints for the instance."""

        resources = self.get_network_topology()

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
        resources = self.get_network_topology()

        resources_keys = resources.keys()

        for key in resources_keys:
            if (
                "clusterEndpoint" in resources[key]
                and len(resources[key]["clusterEndpoint"]) > 0
                and "streamer." not in resources[key]["clusterEndpoint"]
            ):
                return {
                    "endpoint": resources[key]["clusterEndpoint"],
                    "ports": resources[key]["clusterPorts"],
                }

    def create_connection(
        self, ssl: bool = False, force_reconnect: bool = False, retries=5
    ):

        if self._connection is not None and not force_reconnect:
            return self._connection

        endpoint = self.get_cluster_endpoint()

        # Connect to the master node
        while retries > 0:
            try:
                print(f"Connecting to {endpoint['endpoint']}:{endpoint['ports'][0]}")
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
                time.sleep(60)

        if self._connection is None:
            raise Exception("Failed to connect to the master node")

        return self._connection

    def generate_data(self, graph_count: int):
        """Generate data for the instance."""

        db = self.create_connection()

        print("Generating data")
        for i in range(0, graph_count):

            name = rand_string()
            g = db.select_graph(name)

            node_count = rand_range(2000, 1000000)
            node_count = rand_range(200, 1000)
            g.query(
                """UNWIND range (0, $node_count) as x
                    CREATE (a:L {v:x})-[:R]->(b:X {v: tostring(x)}), (a)-[:Z]->(:Y {v:tostring(x)})""",
                {"node_count": node_count},
            )
        print("Data generated")
