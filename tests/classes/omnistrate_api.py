import base64
import requests
import jwt
import time
from falkordb import FalkorDB

DEFAULT_API_URL = "https://api.omnistrate.cloud/"
DEFAULT_API_VERSION = "2022-09-01-00"


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


class OmnistrateApi:

    _instance_endpoints = {}

    def __init__(
        self,
        api_sign_in_path: str = "",
        omnistrate_user: str = "",
        omnistrate_password: str = "",
    ):
        self.api_host = DEFAULT_API_URL
        self.api_sign_in_path = api_sign_in_path
        self._omnistrate_user = omnistrate_user
        self._omnistrate_password = omnistrate_password

        self._token = None

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
            self.api_host + self.api_sign_in_path, headers=headers, timeout=15
        )

        self._handle_response(response, "Failed to get token")

        self._token = response.json()["token"]
        print("Token received")
        return self._token

    def _handle_response(self, response, message):
        if response.status_code >= 300 or response.status_code < 200:
            print(f"{message}: {response.text}")
            raise Exception(f"{message}")

    def get_product_tier_id(
        self, service_id: str, environment_id: str, tier_name: str
    ) -> str:
        """Get the ID of a product tier."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        response = requests.get(
            self.api_host
            + f"/{DEFAULT_API_VERSION}/service/{service_id}/environment/{environment_id}/service-plan",
            headers=headers,
            timeout=15,
        )

        self._handle_response(response, "Failed to get product tier ID")

        data = response.json()["servicePlans"]

        return next(
            (
                tier["productTierId"]
                for tier in data
                if tier["productTierName"] == tier_name
            ),
            None,
        )

    def list_tier_versions(
        self, service_id: str, tier_id: str
    ) -> list[OmnistrateTierVersion]:
        """List all versions of a tier."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        response = requests.get(
            self.api_host
            + f"/{DEFAULT_API_VERSION}/service/{service_id}/productTier/{tier_id}/version-set",
            headers=headers,
            timeout=15,
        )

        self._handle_response(response, "Failed to list tier versions")

        data = response.json()["tierVersionSets"]

        return [OmnistrateTierVersion.from_json(version) for version in data]

    def upgrade_instance(
        self,
        service_id: str,
        product_tier_id: str,
        source_version: str,
        target_version: str,
        instance_id: str,
        wait_until_ready: bool = False,
        upgrade_timeout: int = 1200,
        check_failover: bool = False,
    ):

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        data = {
            "sourceVersion": source_version,
            "targetVersion": target_version,
            "upgradeFilters": {"INSTANCE_IDS": [instance_id]},
        }

        response = requests.post(
            self.api_host
            + f"{DEFAULT_API_VERSION}/fleet"
            + f"/service/{service_id}/productTier/{product_tier_id}/upgrade-path",
            headers=headers,
            json=data,
            timeout=15,
        )

        self._handle_response(response, "Failed to upgrade instance")

        upgrade_id = response.json()["upgradePathId"]

        if wait_until_ready:
            self._wait_until_upgrade_ready(
                service_id, product_tier_id, upgrade_id, upgrade_timeout, check_failover
            )

    def _wait_until_upgrade_ready(
        self,
        service_id: str,
        product_tier_id: str,
        upgrade_id: str,
        upgrade_timeout: int = 1200,
        check_failover: bool = False,
    ):
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        right_now = time.time()
        while True:
            response = requests.get(
                self.api_host
                + f"{DEFAULT_API_VERSION}/fleet"
                + f"/service/{service_id}/productTier/{product_tier_id}/upgrade-path/{upgrade_id}",
                headers=headers,
                timeout=15,
            )

            self._handle_response(response, "Failed to get upgrade status")

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

    def _get_instance_endpoint(
        self, service_id: str, environment_id: str, instance_id: str
    ):

        if instance_id in self._instance_endpoints:
            return self._instance_endpoints[instance_id]

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        response = requests.get(
            self.api_host
            + f"/{DEFAULT_API_VERSION}/fleet/service/{service_id}/environment/{environment_id}/instance/{instance_id}",
            headers=headers,
            timeout=15,
        )

        self._handle_response(response, "Failed to get instance endpoint")

        data = response.json()

        if (
            not "consumptionResourceInstanceResult" in data
            or not "detailedNetworkTopology"
            in data["consumptionResourceInstanceResult"]
        ):
            raise Exception("Instance endpoint not found")

        endpoint = next(
            {"endpoint": r["clusterEndpoint"], "port": r["clusterPorts"][0]}
            for r in data["consumptionResourceInstanceResult"][
                "detailedNetworkTopology"
            ]
            if len(r["clusterEndpoint"]) > 0
            and len(r["clusterPorts"]) > 0
            and r["resourceName"] != "Omnistrate Observability"
        )

        self._instance_endpoints[instance_id] = endpoint

        return endpoint

    def _check_failover(
        self,
        service_id: str,
        environment_id: str,
        instance_id: str,
        failover_timeout: int = 10,
    ):

        endpoint = self._get_instance_endpoint(service_id, environment_id, instance_id)

        right_now = time.time()
        while True:
            try:
                right_now = time.time()
                falkordb = FalkorDB(
                    endpoint["endpoint"], endpoint["port"], "falkordb", "falkordb"
                )
                falkordb.list_graphs()
                break
            except Exception as e:
                if time.time() - right_now > failover_timeout:
                    raise Exception("Failover timed out")
                print("Failover in progress")
                time.sleep(1)
