"""
Omnistrate Fleet Network management.
"""

import logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(message)s")


class OmnistrateFleetNetwork:
    """Manages Omnistrate custom networks."""

    def __init__(self, fleet_api, network_name: str):
        """
        Initialize network manager.
        
        Args:
            fleet_api: OmnistrateFleetAPI instance
            network_name: Name of the custom network
        """
        self._fleet_api = fleet_api
        self.network_name = network_name
        self.network_id = None

        self._get_network_id(network_name)

    def _get_network_id(self, network_name: str) -> None:
        """
        Get network ID by name.
        
        Args:
            network_name: Network name to look up
        """
        response = self._fleet_api.client().get(
            f"{self._fleet_api.base_url}/resource-instance/custom-network",
            timeout=60,
        )
        self._fleet_api.handle_response(response, "Failed to get network")

        networks = response.json()["customNetworks"]

        for network in networks:
            if network["name"] == network_name:
                self.network_id = network["id"]
                return

        # Network not found
        self.network_id = None
