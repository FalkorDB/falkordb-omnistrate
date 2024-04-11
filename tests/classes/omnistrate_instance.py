"""Define a class for an Omnistrate instance, with useful methods to deploy, trigger failover, get the access endpoints, and delete the instance."""

import json
import requests
import base64
import jwt
import time

DEFAULT_API_URL = "https://api.omnistrate.cloud/"


class OmnistrateInstance:

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        api_path: str = "",
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
        assert len(api_failover_path) > 0, "api_failover_path must be provided"
        assert len(api_sign_in_path) > 0, "api_sign_in_path must be provided"
        assert len(omnistrate_user) > 0, "omnistrate_user must be provided"
        assert len(omnistrate_password) > 0, "omnistrate_password must be provided"

        self.api_url = api_url
        self.api_path = api_path
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
            self.api_url + self.api_sign_in_path, headers=headers, timeout=5
        )

        if response.status_code >= 300 or response.status_code < 200:
            print(response.text)
            raise Exception("Failed to get token")

        self._token = response.json()["token"]
        print("Token received")
        return self._token

    def create(
        self,
        wait_for_ready: bool,
        deployment_cloud_provider: str,
        deployment_region: str,
        name: str,
        description: str,
        falkordb_user: str,
        falkordb_password: str,
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
        }

        print(f"Creating instance {name}")

        response = requests.post(
            self.api_url + self.api_path + self.subscription_id_query,
            headers=headers,
            data=json.dumps(data),
            timeout=5,
        )

        if response.status_code >= 300 or response.status_code < 200:
            print(f"Failed to create instance {name}: {response.text}")
            raise Exception(f"Failed to create instance {name}")

        self.instance_id = response.json()["id"]

        print(f"Instance {name} created: {self.instance_id}")

        if not wait_for_ready:
            return

        timeout_timer = time.time() + self.deployment_create_timeout_seconds
        while True:
            if time.time() > timeout_timer:
                print("Timeout reached")
                raise Exception("Timeout reached")

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
            timeout=5,
        )

        if response.status_code >= 300 or response.status_code < 200:
            print(f"Failed to delete instance {self.instance_id}: {response.text}")
            raise Exception(f"Failed to delete instance {self.instance_id}")

        if not wait_for_delete:
            return

        timeout_timer = time.time() + self.deployment_delete_timeout_seconds

        while True:

            if time.time() > timeout_timer:
                print("Timeout reached")
                raise Exception("Timeout reached")
            try:
                state = self._get_instance_state()
                if state == "FAILED":
                    print("Instance is in error state")
                    raise Exception("Instance is in error state")
                else:
                    print("Instance is in " + state + " state")
                    time.sleep(5)
            except:
                print("Instance is deleted")
                break

    def trigger_failover(
        self,
        replica_id: str,
        wait_for_ready: bool,
    ):
        """Trigger failover for the instance. Optionally wait for the instance to be ready."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._get_token(),
        }

        data = {
            "failedReplicaID": replica_id,
            "failedReplicaAction": "FAILOVER_AND_RECREATE",
        }

        response = requests.post(
            self.api_url
            + self.api_failover_path
            + "/"
            + self.instance_id
            + "/failover"
            + self.subscription_id_query,
            headers=headers,
            data=json.dumps(data),
            timeout=5,
        )

        if response.status_code >= 300 or response.status_code < 200:
            print(
                f"Failed to trigger failover for instance {self.instance_id}: {response.text}"
            )
            raise Exception(
                f"Failed to trigger failover for instance {self.instance_id}"
            )

        if not wait_for_ready:
            return

        timeout_timer = time.time() + self.deployment_failover_timeout_seconds

        while True:
            if time.time() > timeout_timer:
                print("Timeout reached")
                raise Exception("Timeout reached")

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

    def get_connection_endpoints(self):
        """Get the connection endpoints for the instance."""
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
            timeout=5,
        )

        if response.status_code >= 300 or response.status_code < 200:
            print(f"Failed to get instance connection data: {response.text}")
            raise Exception("Failed to get instance connection data")

        resources = response.json()["detailedNetworkTopology"]

        resources_keys = resources.keys()

        resource = None
        for key in resources_keys:
            if "nodes" in resources[key] and len(resources[key]["nodes"]) > 0:
                resource = resources[key]
                break

        if resource is None:
            print("No resource with nodes found")
            return

        endpoints = []

        for node in resource["nodes"]:
            endpoints.append(
                {
                    "id": node["id"],
                    "endpoint": node["endpoint"],
                    "port": node["ports"][0],
                }
            )

        return endpoints

    def _get_instance_state(self):
        """Get the state of the instance."""
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
            timeout=5,
        )

        if response.status_code >= 300 or response.status_code < 200:
            print(f"Failed to get instance state {response.text}")
            raise Exception("Failed to get instance state")

        return response.json()["status"]
