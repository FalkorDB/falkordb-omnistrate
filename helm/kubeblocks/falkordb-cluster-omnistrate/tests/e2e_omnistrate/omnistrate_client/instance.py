"""
Omnistrate Fleet Instance management.
"""

import json
import logging
import socket
import time
from falkordb import FalkorDB
from redis.exceptions import (
    ReadOnlyError,
    ResponseError,
    ClusterError,
    RedisClusterException,
    ClusterDownError,
)
from redis import retry, backoff, exceptions as redis_exceptions
from requests import exceptions

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(message)s")


class OmnistrateFleetInstance:
    """Manages Omnistrate Fleet instance lifecycle and connections."""

    def __init__(
        self,
        fleet_api,
        cfg: dict,
    ):
        """
        Initialize instance manager.
        
        Args:
            fleet_api: OmnistrateFleetAPI instance
            cfg: Configuration dictionary with instance parameters
        """
        self._fleet_api = fleet_api
        self._cfg = cfg
        
        self.instance_id = None
        self._network_topology = None
        self._connection = None
        self._network_type = cfg.get("network_type", "PUBLIC")
        self.falkordb_password = None

    def create(
        self,
        wait_for_ready: bool,
        deployment_cloud_provider: str,
        deployment_region: str,
        name: str,
        description: str,
        falkordb_user: str,
        falkordb_password: str,
        network_type: str,
        product_tier_version: str = None,
        custom_network_id: str = None,
        **kwargs,
    ) -> str:
        """
        Create an instance with the specified parameters.
        
        Args:
            wait_for_ready: Wait for instance to be ready
            deployment_cloud_provider: Cloud provider (aws, gcp, azure)
            deployment_region: Region name
            name: Instance name
            description: Instance description
            falkordb_user: FalkorDB username
            falkordb_password: FalkorDB password
            network_type: PUBLIC or INTERNAL
            product_tier_version: Optional tier version
            custom_network_id: Optional custom network ID for INTERNAL type
            **kwargs: Additional instance parameters
            
        Returns:
            Instance ID
        """
        self.falkordb_password = falkordb_password
        self._network_type = network_type

        data = {
            "cloud_provider": deployment_cloud_provider,
            "region": deployment_region,
            "network_type": network_type,
            "requestParams": {
                "name": name,
                "description": description,
                "falkordbUser": falkordb_user,
                "falkordbPassword": falkordb_password,
                **kwargs,
            },
            "productTierVersion": product_tier_version,
        }

        if custom_network_id:
            data["custom_network_id"] = custom_network_id

        logging.info(f"Creating instance {name}")

        cfg = self._cfg
        url = (
            f"{self._fleet_api.base_url}/fleet/resource-instance/"
            f"{cfg['service_provider_id']}/{cfg['service_key']}/"
            f"{cfg['service_api_version']}/{cfg['service_environment_key']}/"
            f"{cfg['service_model_key']}/{cfg['product_tier_key']}/"
            f"{cfg['resource_key']}?subscriptionId={cfg['subscription_id']}"
        )

        response = self._fleet_api.client().post(
            url,
            data=json.dumps(data),
            timeout=60,
        )

        self._fleet_api.handle_response(response, f"Failed to create instance {name}")

        self.instance_id = response.json()["id"]

        logging.info(f"Instance {name} created: {self.instance_id}")

        if wait_for_ready:
            try:
                self.wait_for_instance_status(
                    timeout_seconds=cfg["ready_timeout"]
                )
            except Exception:
                raise Exception(f"Failed to create instance {name}")

        return self.instance_id

    def wait_for_instance_status(
        self,
        timeout_seconds: int,
        requested_status="RUNNING",
    ):
        """
        Wait for the instance to reach requested status.
        
        Args:
            timeout_seconds: Timeout in seconds
            requested_status: Desired status (RUNNING, STOPPED, etc.)
        """
        logging.info(f"Waiting for instance to be {requested_status}. Timeout: {timeout_seconds}s")
        timeout_timer = time.time() + int(timeout_seconds)

        while True:
            if time.time() > timeout_timer:
                raise Exception("Timeout")

            status = self.get_instance_details()["status"]
            if status == requested_status:
                logging.info(f"Instance is {requested_status}")
                break
            elif status == "FAILED":
                logging.info("Instance is in error state")
                raise Exception("Instance is in error state")
            else:
                logging.info(f"Instance is in {status} state")
                time.sleep(5)

    def get_instance_details(self, retries=5):
        """
        Get instance details from API.
        
        Args:
            retries: Number of retries on timeout
            
        Returns:
            Instance details dictionary
        """
        cfg = self._cfg

        while retries > 0:
            try:
                response = self._fleet_api.client().get(
                    f"{self._fleet_api.base_url}/fleet/service/{cfg['service_id']}/"
                    f"environment/{cfg['service_environment_id']}/instance/{self.instance_id}",
                    timeout=60,
                )
            except exceptions.ReadTimeout:
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

    def get_resource_id(self, resource_key: str = None) -> str:
        """
        Get the resource ID for a specific resource key.
        
        Args:
            resource_key: Optional resource key (uses default if not provided)
            
        Returns:
            Resource ID
        """
        network_topology = self.get_network_topology()

        target_key = resource_key or self._cfg["resource_key"]

        for key in network_topology.keys():
            if network_topology[key]["resourceKey"] == target_key:
                return key

        return None

    def delete(self, wait_for_delete: bool):
        """
        Delete the instance.
        
        Args:
            wait_for_delete: Wait for deletion to complete
        """
        if not self.instance_id:
            return

        resource_id = self.get_resource_id()

        if resource_id is None:
            raise Exception(f"Resource ID not found for instance {self.instance_id}")

        cfg = self._cfg

        response = self._fleet_api.client().delete(
            f"{self._fleet_api.base_url}/fleet/service/{cfg['service_id']}/"
            f"environment/{cfg['service_environment_id']}/instance/{self.instance_id}",
            timeout=60,
            data=json.dumps({"resourceId": resource_id}),
        )

        self._fleet_api.handle_response(
            response, f"Failed to delete instance {self.instance_id}"
        )

        if not wait_for_delete:
            return

        try:
            self.wait_for_instance_status(
                timeout_seconds=cfg["stop_timeout"]
            )
        except Exception as e:
            if e.args[0] == "Timeout":
                raise Exception(f"Failed to delete instance {self.instance_id}")

    def stop(self, wait_for_ready: bool, retry=10):
        """
        Stop the instance.
        
        Args:
            wait_for_ready: Wait for stop to complete
            retry: Number of retries if operation in progress
        """
        cfg = self._cfg

        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/fleet/service/{cfg['service_id']}/"
            f"environment/{cfg['service_environment_id']}/instance/{self.instance_id}/stop",
            timeout=60,
            data=json.dumps({"resourceId": self.get_resource_id()}),
        )

        if "operation is already in progress" in response.text and retry > 0:
            time.sleep(90)
            return self.stop(wait_for_ready, retry - 1)

        self._fleet_api.handle_response(
            response, f"Failed to stop instance {self.instance_id}"
        )

        if wait_for_ready:
            self.wait_for_instance_status(
                requested_status="STOPPED",
                timeout_seconds=cfg["stop_timeout"],
            )

    def start(self, wait_for_ready: bool, retry=10):
        """
        Start the instance.
        
        Args:
            wait_for_ready: Wait for start to complete
            retry: Number of retries if operation in progress
        """
        cfg = self._cfg

        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/fleet/service/{cfg['service_id']}/"
            f"environment/{cfg['service_environment_id']}/instance/{self.instance_id}/start",
            timeout=60,
            data=json.dumps({"resourceId": self.get_resource_id()}),
        )

        if "operation is already in progress" in response.text and retry > 0:
            time.sleep(90)
            return self.start(wait_for_ready, retry - 1)

        self._fleet_api.handle_response(
            response, f"Failed to start instance {self.instance_id}"
        )

        if wait_for_ready:
            self.wait_for_instance_status(
                timeout_seconds=cfg["ready_timeout"]
            )

    def trigger_failover(
        self, replica_id: str, wait_for_ready: bool, resource_id: str = None, retry=10
    ):
        """
        Trigger failover for a replica.
        
        Args:
            replica_id: Replica ID to failover
            wait_for_ready: Wait for failover to complete
            resource_id: Optional resource ID
            retry: Number of retries if operation in progress
        """
        logging.info(f"Triggering failover for instance {self.instance_id}")

        cfg = self._cfg

        data = {
            "failedReplicaID": replica_id,
            "failedReplicaAction": "FAILOVER_AND_RESTART",
            "resourceId": resource_id or self.get_resource_id(),
        }

        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/fleet/service/{cfg['service_id']}/"
            f"environment/{cfg['service_environment_id']}/instance/{self.instance_id}/failover",
            data=json.dumps(data),
            timeout=60,
        )

        if "operation is already in progress" in response.text and retry > 0:
            time.sleep(90)
            return self.trigger_failover(
                replica_id, wait_for_ready, resource_id, retry - 1
            )

        self._fleet_api.handle_response(
            response, f"Failed to trigger failover for instance {self.instance_id}"
        )

        if wait_for_ready:
            self.wait_for_instance_status(
                timeout_seconds=cfg["ready_timeout"]
            )

    def update_instance_type(
        self, new_instance_type: str, wait_until_ready: bool = True, retry=5
    ):
        """
        Update the instance type (vertical scaling).
        
        Args:
            new_instance_type: New instance type
            wait_until_ready: Wait for update to complete
            retry: Number of retries if operation in progress
        """
        data = {
            "nodeInstanceType": new_instance_type,
        }

        return self.update_params(wait_until_ready, retry, **data)

    def update_params(self, wait_until_ready: bool = True, retry=10, **kwargs):
        """
        Update instance parameters.
        
        Args:
            wait_until_ready: Wait for update to complete
            retry: Number of retries if operation in progress
            **kwargs: Parameters to update
        """
        cfg = self._cfg

        self.wait_for_instance_status(timeout_seconds=cfg["ready_timeout"])

        data = {
            "network_type": (
                kwargs.get("network_type", self._network_type)
            ),
            "requestParams": kwargs,
        }

        response = self._fleet_api.client().patch(
            f"{self._fleet_api.base_url}/resource-instance/"
            f"{cfg['service_provider_id']}/{cfg['service_key']}/"
            f"{cfg['service_api_version']}/{cfg['service_environment_key']}/"
            f"{cfg['service_model_key']}/{cfg['product_tier_key']}/"
            f"{cfg['resource_key']}/{self.instance_id}",
            data=json.dumps(data),
            timeout=60,
        )

        if "operation is already in progress" in response.text and retry > 0:
            time.sleep(90)
            return self.update_params(wait_until_ready, retry - 1, **kwargs)

        self._fleet_api.handle_response(
            response, f"Failed to update instance {self.instance_id}"
        )

        if wait_until_ready:
            self.wait_for_instance_status(
                timeout_seconds=cfg["update_timeout"]
            )

    def get_network_topology(self, force_refresh=False):
        """
        Get network topology (cached).
        
        Args:
            force_refresh: Force refresh from API
            
        Returns:
            Network topology dictionary
        """
        if self._network_topology is not None and not force_refresh:
            return self._network_topology

        self._network_topology = self.get_instance_details()["detailedNetworkTopology"]

        return self._network_topology

    def get_connection_endpoints(self):
        """
        Get all connection endpoints for the instance.
        
        Returns:
            List of endpoint dictionaries with id, endpoint, and ports
        """
        resources = self.get_network_topology()

        endpoints = []
        for key in resources.keys():
            resource = resources[key]
            additionalEndpoints = resource.get("additionalEndpoints", {})
            is_sentinel = "sentinel" in additionalEndpoints
            if is_sentinel:
                endpoints.append(
                    {
                        "id": "sentinel",
                        "endpoint": additionalEndpoints["sentinel"]["endpoint"],
                        "ports": additionalEndpoints["sentinel"]["openPorts"],
                    }
                )
            endpoints.append(
                {
                    "id": "node",
                    "endpoint": additionalEndpoints["node"]["endpoint"],
                    "ports": additionalEndpoints["node"]["openPorts"],
                }
            )

        if len(endpoints) == 0:
            raise Exception("No endpoints found")

        return endpoints

    def get_cluster_endpoint(self, network_type="PUBLIC"):
        """
        Get cluster endpoint for the specified network type.
        
        Args:
            network_type: PUBLIC or INTERNAL
            
        Returns:
            Dictionary with endpoint and ports
        """
        resources = self.get_network_topology()

        for key in resources.keys():
            resource = resources[key]
            additionalEndpoints = resource.get("additionalEndpoints", {})
            is_sentinel = "sentinel" in additionalEndpoints
            endpoint = additionalEndpoints.get("sentinel" if is_sentinel else "node", None)
            if (
                endpoint is not None
                and resource["networkingType"] == network_type
            ):
                return {
                    "endpoint": endpoint["endpoint"],
                    "ports": endpoint["openPorts"],
                }

        return None

    def create_connection(
        self,
        ssl: bool = False,
        force_reconnect: bool = False,
        retries=5,
        network_type="PUBLIC",
    ):
        """
        Create FalkorDB connection.
        
        Args:
            ssl: Use SSL/TLS
            force_reconnect: Force new connection
            retries: Number of connection retries
            network_type: PUBLIC or INTERNAL
            
        Returns:
            FalkorDB connection
        """
        if self._connection is not None and not force_reconnect:
            return self._connection

        endpoint = self.get_cluster_endpoint(network_type=network_type)

        if not endpoint:
            raise Exception(f"No cluster endpoint found for network type {network_type}")

        # Connect to the cluster endpoint
        while retries > 0:
            try:
                logging.info(
                    f"Connecting to {endpoint['endpoint']}:{endpoint['ports'][0]}"
                )
                self._connection = FalkorDB(
                    host=endpoint["endpoint"],
                    port=endpoint["ports"][0],
                    username="falkordb",
                    password=self.falkordb_password,
                    ssl=ssl,
                    retry_on_error=[
                        ConnectionError,
                        ConnectionRefusedError,
                        TimeoutError,
                        socket.timeout,
                        redis_exceptions.ConnectionError,
                        ResponseError,
                        ReadOnlyError,
                    ],
                    cluster_error_retry_attempts=20,
                    retry=retry.Retry(
                        retries=3,
                        backoff=backoff.ExponentialBackoff(base=1, cap=10),
                        supported_errors=(
                            ConnectionError,
                            ConnectionRefusedError,
                            TimeoutError,
                            socket.timeout,
                            redis_exceptions.ConnectionError,
                            ResponseError,
                            ReadOnlyError,
                            ClusterError,
                            RedisClusterException,
                            ClusterDownError,
                        ),
                    ),
                )
                break
            except Exception as e:
                logging.error(f"Failed to connect: {e}")
                retries -= 1
                time.sleep(60)

        if self._connection is None:
            raise Exception("Failed to connect to the instance")

        return self._connection
