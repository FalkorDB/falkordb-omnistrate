import sys
import requests
import json
import time
from falkordb import FalkorDB
import base64
import os

if len(sys.argv) < 5:
    print(
        "Usage: python create_free.py <omnistrate_user> <omnistrate_password> <deployment_cloud_provider> <deployment_region>"
    )
    sys.exit(1)

OMNISTRATE_USER = sys.argv[1]
OMNISTRATE_PASSWORD = sys.argv[2]
DEPLOYMENT_CLOUD_PROVIDER = sys.argv[3]
DEPLOYMENT_REGION = sys.argv[4]

DEPLOYMENT_CREATE_TIMEOUT_SECONDS = 1200
DEPLOYMENT_DELETE_TIMEOUT_SECONDS = 1200
DEPLOYMENT_FAILOVER_TIMEOUT_SECONDS = 1500

API_VERSION = "2022-09-01-00"
SUBSCRIPTION_ID = os.getenv("SUBSCRIPTION_ID", "sub-BLdnrUpLcT")

API_URL = "https://api.omnistrate.cloud/"
API_PATH = f"{API_VERSION}/resource-instance/sp-JvkxkPhinN/falkordb/v1/prod/falkordb-customer-hosted/falkordb-hosted-tier-falkordb-customer-hosted-model-omnistrate-dedicated-tenancy/free"
API_FAILOVER_PATH = f"{API_VERSION}/resource-instance/sp-JvkxkPhinN/falkordb/v1/prod/falkordb-customer-hosted/falkordb-hosted-tier-falkordb-customer-hosted-model-omnistrate-dedicated-tenancy/node-f"
API_SIGN_IN_PATH = f"{API_VERSION}/resource-instance/user/signin"
SUBSCRIPTION_ID_QUERY = f"?subscriptionId={SUBSCRIPTION_ID}"
# SUBSCRIPTION_ID_QUERY = ""


def _get_token():
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Basic "
        + base64.b64encode(
            (OMNISTRATE_USER + ":" + OMNISTRATE_PASSWORD).encode("utf-8")
        ).decode("utf-8"),
    }
    print("Getting token")
    response = requests.post(API_URL + API_SIGN_IN_PATH, headers=headers, timeout=5)

    if response.status_code >= 300 or response.status_code < 200:
        print(response.text)
        raise Exception("Failed to get token")

    return response.json()["token"]


def test_free():

    token = _get_token()

    # Create instance
    instance_id = create_free(token)
    if instance_id is None:
        raise Exception("Failed to create free")

    try:
    # Test failover and data loss
        test_failover(token, instance_id)
    except Exception as e:
        delete_free(token, instance_id)
        raise e

    # Delete instance
    delete_free(token, instance_id)

    print("Test passed")


def create_free(token):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }

    data = {
        "cloud_provider": DEPLOYMENT_CLOUD_PROVIDER,
        "region": DEPLOYMENT_REGION,
        "requestParams": {
            "name": "free",
            "description": "free",
            "enableTLS": False,
            "falkordbUser": "falkordb",
            "falkordbPassword": "falkordb",
        },
    }

    print("Creating free", API_URL + API_PATH + SUBSCRIPTION_ID_QUERY)

    response = requests.post(
        API_URL + API_PATH + SUBSCRIPTION_ID_QUERY,
        headers=headers,
        data=json.dumps(data),
        timeout=5,
    )

    if response.status_code >= 300 or response.status_code < 200:
        print("Failed to create free")
        print(response.text)
        return

    print("Free created", response.json())

    # Wait until instance is ready

    instance_id = response.json()["id"]

    timeout_timer = time.time() + DEPLOYMENT_CREATE_TIMEOUT_SECONDS
    while True:

        if time.time() > timeout_timer:
            print("Timeout reached")
            raise Exception("Timeout reached")

        state = _get_instance_state(token, instance_id)
        if state == "RUNNING":
            print("Instance is ready")
            break
        elif state == "FAILED":
            print("Instance is in error state")
            raise Exception("Instance is in error state")
        else:
            print("Instance is in " + state + " state")
            time.sleep(5)

    return instance_id


def delete_free(token, instance_id):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }

    response = requests.delete(
        API_URL + API_PATH + "/" + instance_id + SUBSCRIPTION_ID_QUERY,
        headers=headers,
        timeout=5,
    )

    if response.status_code >= 300 or response.status_code < 200:
        print("Failed to delete free")
        print(response.text)
        return

    timeout_timer = time.time() + DEPLOYMENT_DELETE_TIMEOUT_SECONDS

    while True:

        if time.time() > timeout_timer:
            print("Timeout reached")
            raise Exception("Timeout reached")
        try:
            state = _get_instance_state(token, instance_id)
            if state == "FAILED":
                print("Instance is in error state")
                raise Exception("Instance is in error state")
            else:
                print("Instance is in " + state + " state")
                time.sleep(5)
        except:
            print("Instance is deleted")
            break


def test_failover(token, instance_id):
    """This function should retrieve the instance host and port for connection, write some data to the DB, then trigger a failover. After X seconds, the instance should be back online and data should have persisted"""

    # Get instance host and port
    (host, port) = _get_instance_connection_data(token, instance_id)

    print("Connection data: {}:{}".format(host, port))
    db = FalkorDB(host=host, port=port, username="falkordb", password="falkordb")

    graph = db.select_graph("test")

    # Write some data to the DB
    graph.query("CREATE (n:Person {name: 'Alice'})")

    # Trigger failover
    _trigger_failover(token, instance_id)

    # Wait for failover to complete
    timeout_timer = time.time() + DEPLOYMENT_FAILOVER_TIMEOUT_SECONDS

    while True:
        if time.time() > timeout_timer:
            print("Timeout reached")
            raise Exception("Timeout reached")

        state = _get_instance_state(token, instance_id)
        if state == "RUNNING":
            print("Failover completed")
            break
        elif state == "FAILED":
            print("Instance is in error state")
            raise Exception("Instance is in error state")
        else:
            print("Instance is in " + state + " state")
            time.sleep(5)

    # Check if data is still there

    graph = db.select_graph("test")

    result = graph.query("MATCH (n:Person) RETURN n")

    if len(result.result_set) == 0:
        raise Exception("Data lost after failover")

    print("Data persisted after failover")


def _get_instance_connection_data(token, instance_id):

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }

    response = requests.get(
        API_URL + API_PATH + "/" + instance_id + SUBSCRIPTION_ID_QUERY,
        headers=headers,
        timeout=5,
    )

    if response.status_code >= 300 or response.status_code < 200:
        print("Failed to get instance connection data")
        print(response.text)
        return

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

    endpoint = resource["nodes"][0]["endpoint"]
    port = resource["nodes"][0]["ports"][0]

    return (endpoint, port)


def _trigger_failover(token, instance_id):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }

    data = {
        "failedReplicaID": "node-f-0",
        "failedReplicaAction": "FAILOVER_AND_RECREATE",
    }

    response = requests.post(
        API_URL
        + API_FAILOVER_PATH
        + "/"
        + instance_id
        + "/failover"
        + SUBSCRIPTION_ID_QUERY,
        headers=headers,
        data=json.dumps(data),
        timeout=5,
    )

    if response.status_code >= 300 or response.status_code < 200:
        print("Failed to trigger failover")
        print(response.text)
        return

    print("Failover triggered")


def _get_instance_state(token, instance_id):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }

    response = requests.get(
        API_URL + API_PATH + "/" + instance_id + SUBSCRIPTION_ID_QUERY,
        headers=headers,
        timeout=5,
    )

    if response.status_code >= 300 or response.status_code < 200:
        print("Failed to get instance state")
        print(response.text)
        return

    return response.json()["status"]


if __name__ == "__main__":
    test_free()
