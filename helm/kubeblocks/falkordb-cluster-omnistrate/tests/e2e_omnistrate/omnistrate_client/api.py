"""
Omnistrate Fleet API client.
"""

import os
import logging
import requests
from requests.adapters import HTTPAdapter, Retry

from .types import (
    ProductTier,
    Service,
    ServiceModel,
    OmnistrateTierVersion,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")


class OmnistrateFleetAPI:
    """Client for Omnistrate Fleet API operations."""

    def __init__(
        self, email: str, password: str, base_url: str = None
    ):
        """
        Initialize Omnistrate Fleet API client.
        
        Args:
            email: Omnistrate account email
            password: Omnistrate account password
            base_url: Optional custom base URL (defaults to production)
        """
        self._email = email
        self._password = password
        self.base_url = base_url or os.getenv(
            "OMNISTRATE_BASE_URL", "https://api.omnistrate.cloud/2022-09-01-00"
        )
        self._token = None
        self._session = None

    def handle_response(self, response, message):
        """Handle API response and raise exception on error."""
        if response.status_code >= 300 or response.status_code < 200:
            logging.error("%s: %s", message, response.text)
            raise requests.RequestException(message)

    def get_token(self):
        """Get authentication token (cached)."""
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
        """Get configured requests session with auth and retries."""
        if self._session is not None:
            return self._session

        self._session = requests.session()

        retries = Retry(
            total=20,
            backoff_factor=2,
            status_forcelist=[403, 429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
        )

        self._session.headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.get_token(),
            }
        )

        self._session.mount("https://", HTTPAdapter(max_retries=retries))

        return self._session

    def get_service(self, service_id: str) -> Service:
        """
        Get service by ID.
        
        Args:
            service_id: Service ID
            
        Returns:
            Service object
        """
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

    def get_service_model(self, service_id: str, service_model_id: str) -> ServiceModel:
        """
        Get service model by ID.
        
        Args:
            service_id: Service ID
            service_model_id: Service model ID
            
        Returns:
            ServiceModel object
        """
        response = self.client().get(
            f"{self.base_url}/service/{service_id}/model/{service_model_id}",
            timeout=60,
        )

        self.handle_response(response, "Failed to get service model")

        return ServiceModel.from_json(response.json())

    def get_product_tier(
        self, service_id: str, environment_id: str, tier_name: str
    ) -> ProductTier:
        """
        Get product tier by name.
        
        Args:
            service_id: Service ID
            environment_id: Environment ID
            tier_name: Tier name
            
        Returns:
            ProductTier object or None if not found
        """
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
        """
        List all versions of a tier.
        
        Args:
            service_id: Service ID
            tier_id: Tier ID
            
        Returns:
            List of OmnistrateTierVersion objects
        """
        response = self.client().get(
            f"{self.base_url}/service/{service_id}/productTier/{tier_id}/version-set",
            timeout=60,
        )

        self.handle_response(response, "Failed to list tier versions")

        data = response.json()["tierVersionSets"]

        return [OmnistrateTierVersion.from_json(version) for version in data]

    def list_instances(self, service_id: str, env_id: str) -> list:
        """
        List all instances for a service and environment.
        
        Args:
            service_id: Service ID
            env_id: Environment ID
            
        Returns:
            List of instance dictionaries
        """
        url = f"{self.base_url}/fleet/service/{service_id}/environment/{env_id}/instances"

        response = self.client().get(url, timeout=60)
        self.handle_response(response, "Failed to list instances")

        return response.json().get("resourceInstances", [])
