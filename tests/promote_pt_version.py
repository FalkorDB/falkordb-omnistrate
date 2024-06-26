import sys
import requests
import os
import json

if len(sys.argv) < 5:
    print(
        "Usage: python promote_pt_version.py <omnistrate_user> <omnistrate_password> <service_id> <product_tier_id>"
    )
    sys.exit(1)

OMNISTRATE_USER = sys.argv[1]
OMNISTRATE_PASSWORD = sys.argv[2]
SERVICE_ID = sys.argv[3]
PRODUCT_TIER_ID = sys.argv[4]
VERSIONS_STRING = len(sys.argv) > 5 and sys.argv[5] or None


API_URL = "https://api.omnistrate.cloud/"
API_VERSION = "2022-09-01-00"
API_SIGN_IN_PATH = os.getenv("API_SIGN_IN_PATH", f"{API_VERSION}/signin")


def get_token():
    """Get a token to authenticate with the API."""
    headers = {"Content-Type": "application/json"}
    data = {
        "email": OMNISTRATE_USER,
        "password": OMNISTRATE_PASSWORD,
    }

    print("Getting token")
    response = requests.post(
        f"{API_URL}{API_SIGN_IN_PATH}",
        data=json.dumps(data),
        headers=headers,
        timeout=15,
    )

    if response.status_code >= 300 or response.status_code < 200:
        print(response.text)
        raise Exception("Failed to get token")

    token = response.json()["jwtToken"]
    print("Token received")
    return token


def get_last_version():
    """Get the last PT version"""

    headers = {
        "Authorization": "Bearer " + get_token(),
    }

    response = requests.get(
        f"{API_URL}{API_VERSION}/service/{SERVICE_ID}/productTier/{PRODUCT_TIER_ID}/version-set",
        headers=headers,
        timeout=15,
    )

    if response.status_code >= 300 or response.status_code < 200:
        print(response.text)
        raise Exception("Failed to get PT versions")
    
    versions = response.json()["tierVersionSets"]

    if len(versions) == 0:
        return None
    
    return versions[0]["version"]


def promote_pt_version():
    """Promote a PT version"""

    last_version = None

    if VERSIONS_STRING == "" or VERSIONS_STRING is None:
        last_version = get_last_version()
    else:
      versions = VERSIONS_STRING.split(",")
      # Find the biggest version
      for version in versions:
          if last_version is None or version > last_version:
              last_version = version

    if last_version is None:
        print("No version found")
        return

    print(f"Promoting PT version {last_version}")

    headers = {
        "Authorization": "Bearer " + get_token(),
    }

    response = requests.patch(
        f"{API_URL}{API_VERSION}/service/{SERVICE_ID}/productTier/{PRODUCT_TIER_ID}/version-set/{last_version}/promote",
        headers=headers,
        timeout=15,
    )

    if response.status_code >= 300 or response.status_code < 200:
        print(response.text)
        raise Exception("Failed to promote PT version")

    print("PT version promoted")


if __name__ == "__main__":
    promote_pt_version()
