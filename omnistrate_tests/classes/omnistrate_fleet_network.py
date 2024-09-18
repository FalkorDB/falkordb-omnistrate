from falkordb import FalkorDB
import json
import os
import logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(message)s")

import time
import random
import string
from requests import exceptions
import omnistrate_tests.classes.omnistrate_fleet_api


class OmnistrateFleetNetwork:

    network_id: str = None

    def __init__(
        self,
        fleet_api: omnistrate_tests.classes.omnistrate_fleet_api.OmnistrateFleetAPI,
    ):
        self._fleet_api = fleet_api

    def create(
        self,
        name: str,
        cidr: str,
        cloudProviderName: str,
        cloudProviderRegion: str,
    ):
        response = self._fleet_api.client().post(
            f"{self._fleet_api.base_url}/resource-instance/custom-network",
            data=json.dumps(
                {
                    "name": name,
                    "cidr": cidr,
                    "cloudProviderName": cloudProviderName,
                    "cloudProviderRegion": cloudProviderRegion,
                },
            ),
            timeout=15,
        )
        self.network_id = response.json()["id"]

        return self.network_id

    def delete(self):
        self._fleet_api.client().delete(
            f"{self._fleet_api.base_url}/resource-instance/custom-network/{self.network_id}"
        )
        self.network_id = None
