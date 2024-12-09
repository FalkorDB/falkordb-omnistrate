import requests
import time
import os
TESTING_ENVIRONMENT_ID = os.environ['OMNISTRATE_INTERNAL_DEV_ENVIRONMENT']

base_url = "https://api.omnistrate.cloud/2022-09-01-00"

SERVICE_ID = os.environ['OMNISTRATE_INTERNAL_SERVICE_ID']

get_instances_url = f"{base_url}/fleet/service/{SERVICE_ID}/environment/{TESTING_ENVIRONMENT_ID}/instances"

delete_instance_url = f"{base_url}/fleet/service/{SERVICE_ID}/environment/{TESTING_ENVIRONMENT_ID}/instance"

payload = {
    "email": f"{os.environ['OMNISTRATE_USERNAME']}",
    "password": f"{os.environ['OMNISTRATE_PASSWORD']}"
}

response = requests.post(url=f"{base_url}/signin",json=payload)      
if response.status_code == 200:
    token = response.json().get('jwtToken')
else:
    raise ConnectionError(f"Error while getting token status code:{response.status_code}")


bearer = token
headers = {
    "Authorization": f"Bearer {bearer}",
    "Content-Type": "application/json",
}

# Get all instances
response = requests.get(get_instances_url, headers=headers)
instances = response.json()["resourceInstances"]

# Delete all instances
for instance in (
    instances
    for instances in instances
    #if instances["productTierId"] == TESTING_FALKORDDB_PLAN_ID
):
    time.sleep(5)
    instance_id = instance["consumptionResourceInstanceResult"]["id"]
    print(instance_id)
    resources_map = instance["consumptionResourceInstanceResult"][
        "detailedNetworkTopology"
    ]
    resource_id = None
    for key, value in resources_map.items():
        if value["main"]:
            resource_id = key
            break
    response = requests.delete(
        f"{delete_instance_url}/{instance_id}",
        headers=headers,
        json={"resourceId": resource_id},
    )
    if response.status_code == 200:
        print(f"Instance {instance_id} deleted: {response.status_code}")
    else:
        print(
            f"Failed to delete instance {instance_id}: {response.status_code}: {response.text}"
        )