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
try:
    response = requests.get(get_instances_url + (f"?ProductTierId={os.getenv('OMNISTRATE_PRODUCT_TIER_ID')}" if os.getenv("OMNISTRATE_PRODUCT_TIER_ID") else ""), headers=headers)
    response.raise_for_status()
    instances = response.json().get("resourceInstances", [])
except requests.exceptions.RequestException as e:
    print(f"Failed to retrieve instances: {e}")
    # Handle the error or exit the script
    exit(1)
except KeyError:
    print("Unexpected response format: 'resourceInstances' key not found")
    exit(1)

# Delete all instances
for instance in (
    instances
    for instances in instances
):
    time.sleep(5)
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
        resource_id= resources_map["resourceID"]
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