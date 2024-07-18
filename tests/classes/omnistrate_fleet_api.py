import requests
import json
import time
import os
from falkordb import FalkorDB
import random
import string


class Service:

    def __init__(
        self,
        id: str,
        name: str,
        key: str,
        service_provider_id: str,
        environments: list["Environment"],
    ):
        self.id = id
        self.name = name
        self.key = key
        self.service_provider_id = service_provider_id
        self.environments = environments

    def get_environment(self, environment_id: str):
        return next(env for env in self.environments if env.id == environment_id)

    @staticmethod
    def from_json(json: dict):
        return Service(
            json["id"],
            json["name"],
            json["key"],
            json["serviceProviderID"],
            [Environment.from_json(env) for env in json["serviceEnvironments"]],
        )


class ServiceModel:

    def __init__(self, id: str, name: str, key: str):
        self.id = id
        self.name = name
        self.key = key

    @staticmethod
    def from_json(json: dict):
        return ServiceModel(json["id"], json["name"], json["key"])


class Environment:

    def __init__(self, id: str, name: str, key: str):
        self.id = id
        self.name = name
        self.key = key.lower()

    @staticmethod
    def from_json(json: dict):
        return Environment(json["id"], json["name"], json["name"])


class OmnistrateFleetInstance:

    instance_id: str = None
    _network_topology = None
    _connection: FalkorDB = None

    def __init__(
        self,
        fleet_api: "OmnistrateFleetAPI",
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

        print(f"Creating instance {name}" + f" with parameters: {data}")

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
            self.wait_for_instance_ready(
                timeout_seconds=self.deployment_create_timeout_seconds
            )
        except Exception:
            raise Exception(f"Failed to create instance {name}")

    def wait_for_instance_ready(self, timeout_seconds: int = 1200):
        """Wait for the instance to be ready."""
        timeout_timer = time.time() + int(timeout_seconds or 1200)

        while True:
            if time.time() > timeout_timer:
                raise Exception("Timeout")

            status = self._get_instance_details()["status"]
            if status == "RUNNING":
                print("Instance is ready")
                break
            elif status == "FAILED":
                print("Instance is in error state")
                raise Exception("Instance is in error state")
            else:
                print("Instance is in " + status + " state")
                time.sleep(5)

    def _get_instance_details(self, retries=5):
        """Get the details of the instance."""

        while retries > 0:

            response = self._fleet_api.client().get(
                f"{self._fleet_api.base_url}/fleet/service/{self.service_id}/environment/{self.service_environment_id}/instance/{self.instance_id}",
                timeout=15,
            )

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

    def _get_resource_id(self):
        """Get the resource ID of the instance."""

        network_topology = self._get_network_topology()

        # find key for object with the correct resourceKey

        for key in network_topology.keys():
            if network_topology[key]["resourceKey"] == self.resource_key:
                return key

    def delete(self, wait_for_delete: bool):
        """Delete the instance. Optionally wait for the instance to be deleted."""

        resource_id = self._get_resource_id()

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
            self.wait_for_instance_ready(
                timeout_seconds=self.deployment_delete_timeout_seconds
            )
        except Exception as e:
            if e.args[0] == "Timeout":
                raise Exception(f"Failed to delete instance {self.instance_id}")

    def trigger_failover(
        self, replica_id: str, wait_for_ready: bool, resource_id: str = None
    ):
        """Trigger failover for the instance. Optionally wait for the instance to be ready."""

        data = {
            "failedReplicaID": replica_id,
            "failedReplicaAction": "FAILOVER_AND_RESTART",
            "resourceId": resource_id,
        }

        response = requests.post(
            f"{self._fleet_api.base_url}/fleet/service/{self.service_id}/environment/{self.service_environment_id}/instance/{self.instance_id}/failover",
            data=json.dumps(data),
            timeout=15,
        )

        self._fleet_api.handle_response(
            response, f"Failed to trigger failover for instance {self.instance_id}"
        )

        if not wait_for_ready:
            return

        self.wait_for_instance_ready(
            timeout_seconds=self.deployment_failover_timeout_seconds
        )

    def update_instance_type(
        self, new_instance_type: str, wait_until_ready: bool = True, retry=5
    ):
        """Update the instance type."""

        self.wait_for_instance_ready()

        data = {
            "nodeInstanceType": new_instance_type,
        }

        response = self._fleet_api.client().put(
            f"{self._fleet_api.base_url}/fleet/service/{self.service_id}/environment/{self.service_environment_id}/instance/{self.instance_id}",
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

        self._fleet_api.handle_response(
            response, f"Failed to update instance type {self.instance_id}"
        )

        if not wait_until_ready:
            return

        self.wait_for_instance_ready(
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
        check_failover: bool = False,
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

    def _get_network_topology(self):

        if self._network_topology is not None:
            return self._network_topology

        self._network_topology = self._get_instance_details()["detailedNetworkTopology"]

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


def rand_range(a, b):
    return random.randint(a, b)


def rand_string(l=12):
    letters = string.ascii_letters
    return "".join(random.choice(letters) for _ in range(l))


class ProductTier:

    def __init__(
        self,
        product_tier_id: str,
        product_tier_name: str,
        product_tier_key: str,
        latest_major_version: str,
        service_model_id: str,
        service_model_name: str,
        service_environment_id: str,
        service_api_id: str,
    ):
        self.product_tier_id = product_tier_id
        self.product_tier_name = product_tier_name
        self.product_tier_key = product_tier_key
        self.latest_major_version = latest_major_version
        self.service_model_id = service_model_id
        self.service_model_name = service_model_name
        self.service_environment_id = service_environment_id
        self.service_api_id = service_api_id

    @staticmethod
    def from_json(json: dict):
        return ProductTier(
            json["productTierId"],
            json["productTierName"],
            json["productTierKey"],
            json["latestMajorVersion"],
            json["serviceModelId"],
            json["serviceModelName"],
            json["serviceEnvironmentId"],
            json["serviceApiId"],
        )


class TierVersionStatus:
    PREFERRED = "Preferred"
    ACTIVE = "Active"
    DEPRECATED = "Deprecated"

    @staticmethod
    def from_string(status: str):
        if status == "Preferred":
            return TierVersionStatus.PREFERRED
        if status == "Active":
            return TierVersionStatus.ACTIVE
        if status == "Deprecated":
            return TierVersionStatus.DEPRECATED
        raise ValueError(f"Invalid status: {status}")


class OmnistrateTierVersion:

    def __init__(
        self,
        version: str,
        service_id: str,
        product_tier_id: str,
        status: TierVersionStatus,
    ):
        self.version = version
        self.service_id = service_id
        self.product_tier_id = product_tier_id
        self.status = status

    @staticmethod
    def from_json(json: dict):
        return OmnistrateTierVersion(
            json["version"],
            json["serviceId"],
            json["productTierId"],
            TierVersionStatus.from_string(json["status"]),
        )


class OmnistrateFleetAPI:

    base_url = "https://api.omnistrate.cloud/2022-09-01-00"
    _token = None

    def __init__(self, email: str, password: str):
        self._email = email
        self._password = password

    def handle_response(self, response, message):
        if response.status_code >= 300 or response.status_code < 200:
            print(f"{message}: {response.text}")
            raise Exception(f"{message}")

    def get_token(self):

        if self._token is not None:
            return self._token

        url = self.base_url + "/signin"
        response = requests.post(
            url, json={"email": self._email, "password": self._password}, timeout=10
        )
        self.handle_response(response, "Failed to get token")

        self._token = response.json()["jwtToken"]

        return self._token

    def client(self):
        session = requests.session()

        session.headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.get_token(),
            }
        )

        return session

    def get_service(self, service_id: str) -> "Service":
        """Get the service by ID."""

        response = self.client().get(
            f"{self.base_url}/service",
            timeout=15,
        )

        self.handle_response(response, "Failed to get service")

        return next(
            Service.from_json(service)
            for service in response.json()["services"]
            if service["id"] == service_id
        )

    def get_service_model(self, service_id: str, service_model_id: str):
        """Get the service model by ID."""

        response = self.client().get(
            f"{self.base_url}/service/{service_id}/model/{service_model_id}",
            timeout=15,
        )

        self.handle_response(response, "Failed to get service model")

        return ServiceModel.from_json(response.json())

    def get_product_tier(
        self, service_id: str, environment_id: str, tier_name: str
    ) -> "ProductTier":
        """Get the product tier by name."""

        response = self.client().get(
            f"{self.base_url}/service/{service_id}/environment/{environment_id}/service-plan",
            timeout=15,
        )

        self.handle_response(response, "Failed to get product tier ID")

        data = response.json()["servicePlans"]

        return next(
            (
                ProductTier.from_json(tier)
                for tier in data
                if tier["productTierName"] == tier_name
            ),
            None,
        )

    def list_tier_versions(
        self, service_id: str, tier_id: str
    ) -> list[OmnistrateTierVersion]:
        """List all versions of a tier."""

        response = self.client().get(
            f"{self.base_url}/service/{service_id}/productTier/{tier_id}/version-set",
            timeout=15,
        )

        self.handle_response(response, "Failed to list tier versions")

        data = response.json()["tierVersionSets"]

        return [OmnistrateTierVersion.from_json(version) for version in data]

    def instance(
        self,
        service_id: str = None,
        service_provider_id: str = None,
        service_key: str = None,
        service_api_version: str = None,
        service_environment_key: str = None,
        service_environment_id: str = None,
        service_model_key: str = None,
        product_tier_key: str = None,
        resource_key: str = None,
        subscription_id: str = None,
        deployment_create_timeout_seconds: int = None,
        deployment_delete_timeout_seconds: int = None,
        deployment_failover_timeout_seconds: int = None,
    ):
        return OmnistrateFleetInstance(
            self,
            service_id,
            service_provider_id,
            service_key,
            service_api_version,
            service_environment_key,
            service_environment_id,
            service_model_key,
            product_tier_key,
            resource_key,
            subscription_id,
            deployment_create_timeout_seconds,
            deployment_delete_timeout_seconds,
            deployment_failover_timeout_seconds,
        )
