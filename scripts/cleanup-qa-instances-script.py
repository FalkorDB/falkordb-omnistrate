#!/usr/bin/env python3

import os
import time

import requests

BASE_URL = "https://api.omnistrate.cloud/2022-09-01-00"
SUCCESS_STATUS_CODES = (200, 202, 204)
TARGET_DEPLOYMENT_ACCOUNTS = {
    ("aws", "637423310747"),
    ("gcp", "app-plane-dev-f7a2434f"),
}


def get_auth_headers():
    payload = {
        "email": os.environ["OMNISTRATE_USERNAME"],
        "password": os.environ["OMNISTRATE_PASSWORD"],
    }
    response = requests.post(url=f"{BASE_URL}/signin", json=payload, timeout=60)
    if response.status_code != 200:
        raise ConnectionError(
            f"Error while getting token status code:{response.status_code}"
        )
    token = response.json().get("jwtToken")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def cleanup_instances(headers):
    testing_environment_id = os.environ["OMNISTRATE_INTERNAL_DEV_ENVIRONMENT"]
    service_id = os.environ["OMNISTRATE_INTERNAL_SERVICE_ID"]
    get_instances_url = (
        f"{BASE_URL}/fleet/service/{service_id}/environment/{testing_environment_id}/instances"
    )
    delete_instance_url = (
        f"{BASE_URL}/fleet/service/{service_id}/environment/{testing_environment_id}/instance"
    )

    try:
        product_tier_id = os.getenv("OMNISTRATE_PRODUCT_TIER_ID")
        query = f"?ProductTierId={product_tier_id}" if product_tier_id else ""
        response = requests.get(
            f"{get_instances_url}{query}",
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        instances = response.json().get("resourceInstances", [])
    except requests.exceptions.RequestException as e:
        print(f"Failed to retrieve instances: {e}")
        raise
    except KeyError as e:
        print("Unexpected response format: 'resourceInstances' key not found")
        raise KeyError(str(e)) from e

    for instance in instances:
        instance_id = instance["consumptionResourceInstanceResult"]["id"]
        print(instance_id)

        if "detailedNetworkTopology" not in instance["consumptionResourceInstanceResult"]:
            resources_map = instance["consumptionResourceInstanceResult"]
            cloud_account_instance = True
        else:
            cloud_account_instance = False
            resources_map = instance["consumptionResourceInstanceResult"][
                "detailedNetworkTopology"
            ]

        if not cloud_account_instance:
            resource_id = None
            for key, value in resources_map.items():
                if value["main"]:
                    resource_id = key
                    break
        else:
            resource_id = resources_map["resourceID"]
        response = requests.delete(
            f"{delete_instance_url}/{instance_id}",
            headers=headers,
            json={"resourceId": resource_id},
            timeout=60,
        )
        if response.status_code in SUCCESS_STATUS_CODES:
            print(f"Instance {instance_id} deleted: {response.status_code}")
        else:
            print(
                f"Failed to delete instance {instance_id}: {response.status_code}: {response.text}"
            )
        time.sleep(5)


def should_delete_deployment_cell(host_cluster):
    cloud_provider = str(host_cluster.get("cloudProvider", "")).lower()
    account_id = str(host_cluster.get("accountID", ""))
    deployments = host_cluster.get("currentNumberOfDeployments")
    if deployments is None:
        return False
    try:
        deployments_count = int(deployments)
    except (TypeError, ValueError):
        return False
    return (
        (cloud_provider, account_id) in TARGET_DEPLOYMENT_ACCOUNTS
        and deployments_count == 0
    )


def cleanup_deployment_cells(headers):
    response = requests.get(
        f"{BASE_URL}/fleet/host-clusters",
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        host_clusters = payload.get("hostClusters", [])
    else:
        host_clusters = payload

    if not isinstance(host_clusters, list):
        raise ValueError(
            f"Unexpected response format from /fleet/host-clusters: "
            f"expected list, got {type(host_clusters).__name__}"
        )

    for host_cluster in host_clusters:
        if not should_delete_deployment_cell(host_cluster):
            continue

        host_cluster_id = host_cluster.get("id")
        if not host_cluster_id:
            continue

        delete_response = requests.delete(
            f"{BASE_URL}/fleet/host-clusters/{host_cluster_id}",
            headers=headers,
            timeout=60,
        )
        if delete_response.status_code in SUCCESS_STATUS_CODES:
            print(f"Deployment cell {host_cluster_id} deleted")
        else:
            print(
                f"Failed to delete deployment cell {host_cluster_id}: "
                f"{delete_response.status_code}: {delete_response.text}"
            )


def main():
    headers = get_auth_headers()
    cleanup_instances(headers)
    cleanup_deployment_cells(headers)


if __name__ == "__main__":
    main()
