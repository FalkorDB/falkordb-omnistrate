import sys
import signal
from random import randbytes
from pathlib import Path  # if you haven't already done so
import socket

file = Path(__file__).resolve()
parent, root = file.parent, file.parents[1]
sys.path.append(str(root))

# Additionally remove the current file's directory from sys.path
from contextlib import suppress

with suppress(ValueError):
    sys.path.remove(str(parent))

import logging

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(message)s")

import time
import os
from omnistrate_tests.classes.omnistrate_fleet_instance import OmnistrateFleetInstance
from omnistrate_tests.classes.omnistrate_fleet_api import OmnistrateFleetAPI
import argparse
from redis.exceptions import ResponseError, AuthenticationError
parser = argparse.ArgumentParser()
parser.add_argument("omnistrate_user")
parser.add_argument("omnistrate_password")
parser.add_argument("cloud_provider", choices=["aws", "gcp"])
parser.add_argument("region")

parser.add_argument(
    "--subscription-id", required=False, default=os.getenv("SUBSCRIPTION_ID")
)
parser.add_argument("--ref-name", required=False, default=os.getenv("REF_NAME"))
parser.add_argument("--service-id", required=True)
parser.add_argument("--environment-id", required=True)
parser.add_argument("--resource-key", required=True)
parser.add_argument("--replica-id", required=True)


parser.add_argument("--instance-name", required=True)
parser.add_argument("--instance-description", required=False, default="test-standalone")
parser.add_argument("--instance-type", required=True)
parser.add_argument("--storage-size", required=False, default="30")
parser.add_argument("--tls", action="store_true")
parser.add_argument("--rdb-config", required=False, default="medium")
parser.add_argument("--aof-config", required=False, default="always")
parser.add_argument("--persist-instance-on-fail",action="store_true")
parser.add_argument("--custom-network", required=False)
parser.add_argument("--network-type", required=False, default="PUBLIC")

parser.set_defaults(tls=False)
args = parser.parse_args()

instance: OmnistrateFleetInstance = None


# Intercept exit signals so we can delete the instance before exiting
def signal_handler(sig, frame):
    if instance:
        instance.delete(False)
    sys.exit(0)


if not args.persist_instance_on_fail:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


def test_graph_acl():
    global instance

    omnistrate = OmnistrateFleetAPI(
        email=args.omnistrate_user,
        password=args.omnistrate_password,
    )

    service = omnistrate.get_service(args.service_id)
    product_tier = omnistrate.get_product_tier(
        service_id=args.service_id,
        environment_id=args.environment_id,
        tier_name=args.ref_name,
    )
    service_model = omnistrate.get_service_model(
        args.service_id, product_tier.service_model_id
    )

    logging.info(f"Product tier id: {product_tier.product_tier_id} for {args.ref_name}")

    network = None
    if args.custom_network:
        network = omnistrate.network(args.custom_network)

    instance = omnistrate.instance(
        service_id=args.service_id,
        service_provider_id=service.service_provider_id,
        service_key=service.key,
        service_environment_id=args.environment_id,
        service_environment_key=service.get_environment(args.environment_id).key,
        service_model_key=service_model.key,
        service_api_version="v1",
        product_tier_key=product_tier.product_tier_key,
        resource_key=args.resource_key,
        subscription_id=args.subscription_id,
        deployment_create_timeout_seconds=2400,
        deployment_delete_timeout_seconds=2400,
        deployment_failover_timeout_seconds=2400,
    )

    try:
        instance.create(
            wait_for_ready=True,
            deployment_cloud_provider=args.cloud_provider,
            network_type=args.network_type,
            deployment_region=args.region,
            name=args.instance_name,
            description=args.instance_description,
            falkordb_user="falkordb",
            falkordb_password=randbytes(16).hex(),
            nodeInstanceType=args.instance_type,
            storageSize=args.storage_size,
            enableTLS=args.tls,
            RDBPersistenceConfig=args.rdb_config,
            AOFPersistenceConfig=args.aof_config,
            custom_network_id=network.network_id if network else None,
        )
        #create a user
        create_user(instance)
        #fail to update global admin permissions
        fail_to_update_global_admin_permissions(instance)
        #fail to get global admin permissions
        fail_to_get_global_admin_permissions(instance)
        #fail to give unauthorized permissions
        fail_to_give_unauthorized_permissions(instance)
        #fail to give unauthorized permissions with pipes
        fail_to_give_unauthorized_permissions_with_pipes(instance)
        #set user off
        set_user_off(instance)
        #wrong password call
        wrong_password_call(instance)
        #add password
        add_password(instance)
        try:
            ip = resolve_hostname(instance=instance)
            logging.info(f"Instance endpoint {instance.get_cluster_endpoint()['endpoint']} resolved to {ip}")
        except TimeoutError as e:
            logging.error(f"DNS resolution failed: {e}")
            raise Exception("Instance endpoint not ready: DNS resolution failed") from e

    except Exception as e:
        logging.exception(e)
        if not args.persist_instance_on_fail:
            instance.delete(network is not None)
        raise e

    # Delete instance
    instance.delete(network is not None)

    logging.info("Test passed")





def get_user_commands(self, user_details):
    """
    Extracts and returns the list of commands from Redis ACL GETUSER response.

    Args:
        user_details (list): Raw response from Redis ACL GETUSER command

    Returns:
        list: Commands with their permissions (e.g., ['-@all', '+info', ...])
    """

    # Convert the flat list to a dictionary
    user_dict = dict(zip(user_details[::2], user_details[1::2]))

    # Get the commands string and split into individual permissions
    commands_str = user_dict.get('commands', '')
    return commands_str.split()

def create_user(instance: OmnistrateFleetInstance):
    """Test create a user using graph.acl"""
    db = instance.create_connection(ssl=args.tls)
    try:
        response= db.connection.execute_command("GRAPH.ACL", "SETUSER", "testuser", "on", ">pass", "+@graph-user")
        response= db.connection.execute_command("GRAPH.ACL", "SETUSER", "testuser2", "on", ">pass", "+@graph-readonly-user")
        if response == "OK":
            logging.info("User created successfully")
    except Exception as e:
        logging.error(f"Failed to create user: {e}")
        raise e



def fail_to_update_global_admin_permissions(instance: OmnistrateFleetInstance):
    """Test fail to update global admin permissions"""
    db = instance.create_connection(ssl=args.tls)
    try:
        response = db.connection.execute_command("GRAPH.ACL", "SETUSER", "default", "-@all")
    except Exception as e:
        if isinstance(e, ResponseError):
            logging.info("Failed to update global admin password as expected")
            return
        logging.error(f"Unexpected error while updating global admin permissions: {e}")
        raise e
    
def fail_to_get_global_admin_permissions(instance: OmnistrateFleetInstance):
    """Test fail to get global admin permissions"""
    db = instance.create_connection(ssl=args.tls)
    try:
        response = db.connection.execute_command("GRAPH.ACL", "GETUSER", "default")
    except Exception as e:
        if isinstance(e, ResponseError):
            logging.info("Failed to get global admin permissions as expected")
            return
        logging.error(f"Unexpected error while getting global admin permissions: {e}")
        raise e


def fail_to_give_unauthorized_permissions(instance: OmnistrateFleetInstance):
    """Test fail to give unauthorized permissions for the Admin user"""
    db = instance.create_connection(ssl=args.tls)
    try:
        response = db.connection.execute_command("GRAPH.ACL", "SETUSER", "falkordb", "+ACL")
        if response == "OK":
            info = db.connection.execute_command("GRAPH.ACL", "GETUSER", "falkordb")
            commands = get_user_commands(info)
            if not '+acl' in commands:
                logging.info("Failed to give unauthorized permissions as expected")
                return
            else:
                raise Exception("Was able to give unauthorized permissions,not expected")
    except Exception as e:
        logging.error(f"Unexpected error while giving unauthorized permissions: {e}")
        raise e


def fail_to_give_unauthorized_permissions_with_pipes(instance: OmnistrateFleetInstance):
    """Test fail to give unauthorized permissions for the Admin user with pipes"""
    db = instance.create_connection(ssl=args.tls)
    try:
        response = db.connection.execute_command("GRAPH.ACL", "SETUSER", "testuser", "+COMMAND|LIST")
        if response == "OK":
            info = db.connection.execute_command("GRAPH.ACL", "GETUSER", "testuser")
            commands = get_user_commands(info)
            if not '+command|list' in commands:
                logging.info("Failed to give unauthorized permissions with pipes as expected")
                return
            else:
                raise Exception("Was able to give unauthorized permissions with pipes,not expected")
    except Exception as e:
        logging.error(f"Unexpected error while giving unauthorized permissions with pipes: {e}")
        raise e

def set_user_off(instance: OmnistrateFleetInstance):
    """Test set user off"""
    db = instance.create_connection(ssl=args.tls)
    try:
        response = db.execute_command("GRAPH.ACL", "SETUSER", "testuser2", "off")
        if response == "OK":
            info = db.execute_command("GRAPH.ACL", "GETUSER", "testuser2")
            user_dict = dict(zip(info[::2], info[1::2]))
            flags= user_dict.get('flags', '')
            if not 'off' in flags:
                logging.info("User set to off successfully")
                return
            else:
                raise Exception("Failed to set user off, not expected")
    except Exception as e:
        logging.error(f"Failed to set user off: {e}")
        raise e
def wrong_password_call(instance: OmnistrateFleetInstance):
    """Test wrong password call"""
    db = instance.create_connection(ssl=args.tls)
    try:
        response = db.connection.execute_command("GRAPH.PASSWORD", "ADD")
    except Exception as e:
        if isinstance(e, ResponseError):
            logging.error(f"Threw an unknown command error as expected: {e}")
        else:
            raise e
        

    try:
        response = db.connection.execute_command("GRAPH.PASSWORD", "FOO", "BAR")
    except Exception as e:
        if isinstance(e, ResponseError):
            logging.error(f"Threw an unknown command error as expected: {e}")
        else:
            raise e    
        

def add_password(instance: OmnistrateFleetInstance):
    db = instance.create_connection(ssl=args.tls)
    try:
        response = db.connection.execute_command("GRAPH.PASSWORD", "ADD", "testpass")
        if response == "OK":
            logging.info("Password added successfully")
    except Exception as e:
        logging.error(f"Failed to add password: {e}")
        raise e
    
    try:
        response = db.execute_command("AUTH", "falkordb", "testpass")
        if response == "OK":
            logging.info("Authenticated with new password successfully")
    except Exception as e:
        logging.error(f"Failed to authenticate with new password: {e}")
        raise e
    try:
        response = db.execute_command("GRAPH.PASSWORD", "REMOVE", "testpass")
        if response == "OK":
            logging.info("Password removed successfully")
    except Exception as e:
        logging.error(f"Failed to remove password: {e}")
        raise e
    try:
        response = db.execute_command("AUTH", "falkordb", "testpass")
        if response == "OK":
            logging.info("Authenticated with removed password successfully")
            raise Exception("Was able to authenticate with removed password, not expected")
    except Exception as e:
        if isinstance(e, AuthenticationError):
            logging.info("Failed to authenticate with removed password as expected")
            return
        else:
            logging.error(f"Unexpected error while authenticating with removed password: {e}")
            raise e
def resolve_hostname(instance: OmnistrateFleetInstance,timeout=300, interval=1):
    """Check if the instance's main endpoint is resolvable.
    Args:
        instance: The OmnistrateFleetInstance to check
        timeout: Maximum time in seconds to wait for resolution (default: 30)
        interval: Time in seconds between retry attempts (default: 1)
    
    Returns:
        str: The resolved IP address

    Raises:
        ValueError: If interval or timeout are invalid
        KeyError: If endpoint information is missing
        TimeoutError: If hostname cannot be resolved within timeout
    """
    if interval <= 0 or timeout <= 0:
        raise ValueError("Interval and timeout must be positive")
    
    cluster_endpoint = instance.get_cluster_endpoint()

    if not cluster_endpoint or 'endpoint' not in cluster_endpoint:
        raise KeyError("Missing endpoint information in cluster configuration")

    hostname = cluster_endpoint['endpoint']
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            ip = socket.gethostbyname(hostname)
            return ip
        except (socket.gaierror, socket.error) as e:
            logging.debug(f"DNS resolution attempt failed: {e}")
            time.sleep(interval)
     
    raise TimeoutError(f"Unable to resolve hostname '{hostname}' within {timeout} seconds.")

if __name__ == "__main__":
    test_graph_acl()
