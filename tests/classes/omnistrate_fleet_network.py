from falkordb import FalkorDB
import json
import os
import logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(message)s")

import time
import random
import string
from requests import exceptions
import tests.classes.omnistrate_fleet_api


class OmnistrateFleetNetwork:

    network_id: str = None

    def __init__(
        self,
        fleet_api: tests.classes.omnistrate_fleet_api.OmnistrateFleetAPI,
        network_name: str,
    ):
        self._fleet_api = fleet_api
        self.network_name = network_name

        self._get_network_id(network_name)

    def _get_network_id(self, network_name: str) -> None:
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

        return
