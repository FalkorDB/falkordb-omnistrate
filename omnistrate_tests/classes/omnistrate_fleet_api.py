import requests
from requests.adapters import HTTPAdapter, Retry
import omnistrate_tests.classes
from .omnistrate_types import (
    ProductTier,
    Service,
    ServiceModel,
    OmnistrateTierVersion,
)

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


class OmnistrateFleetAPI:

    base_url = "https://api.omnistrate.cloud/2022-09-01-00"
    _token = None

    _session = None

    def __init__(self, email: str, password: str):
        self._email = email
        self._password = password

    def handle_response(self, response, message):
        if response.status_code >= 300 or response.status_code < 200:
            logging.error(f"{message}: {response.text}")
            raise Exception(f"{message}")

    def get_token(self):

        if self._token is not None:
            return self._token

        url = self.base_url + "/signin"
        response = requests.post(
            url, json={"email": self._email, "password": self._password}, timeout=60
        )
        self.handle_response(response, "Failed to get token")

        self._token = response.json()["jwtToken"]

        return self._token

    def client(self):

        if self._session is not None:
            return self._session
        
        self._session = requests.session()

        retries = Retry(
            total=10,
            backoff_factor=0.1,
            status_forcelist=[403, 429, 500, 502, 503, 504],
        )

        self._session.headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.get_token(),
            }
        )

        self._session.mount("https://", HTTPAdapter(max_retries=retries))

        return self._session

    def get_service(self, service_id: str) -> "Service":
        """Get the service by ID."""

        response = self.client().get(
            f"{self.base_url}/service",
            timeout=60,
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
            timeout=60,
        )

        self.handle_response(response, "Failed to get service model")

        return ServiceModel.from_json(response.json())

    def get_product_tier(
        self, service_id: str, environment_id: str, tier_name: str
    ) -> "ProductTier":
        """Get the product tier by name."""

        response = self.client().get(
            f"{self.base_url}/service/{service_id}/environment/{environment_id}/service-plan",
            timeout=60,
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
            timeout=60,
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
        deployment_update_timeout_seconds: int = None,
    ):
        return omnistrate_tests.OmnistrateFleetInstance(
            self,
            deployment_create_timeout_seconds,
            deployment_delete_timeout_seconds,
            deployment_failover_timeout_seconds,
            deployment_update_timeout_seconds,
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
        )

    def network(
        self,
        network_name: str,
    ):
        return omnistrate_tests.OmnistrateFleetNetwork(self, network_name)
