"""
Common fixtures for integration tests.
"""

import pytest
import logging
from kubernetes import client, config
from ..utils.kubernetes import KubernetesHelper

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def k8s_client():
    """Load Kubernetes config and return API client."""
    config.load_kube_config()
    return client.CoreV1Api()


@pytest.fixture(scope="session")
def k8s_apps_client():
    """Return Kubernetes Apps API client."""
    config.load_kube_config()
    return client.AppsV1Api()


@pytest.fixture(scope="session")
def k8s_custom_client():
    """Return Kubernetes Custom Objects API client for CRDs."""
    config.load_kube_config()
    return client.CustomObjectsApi()


@pytest.fixture
def k8s_helper(namespace):
    """Return Kubernetes helper instance."""
    return KubernetesHelper(namespace)


@pytest.fixture
def cluster_name(request):
    """Return the cluster name from command line or default."""
    return request.config.getoption("--cluster-name")


@pytest.fixture(scope="module") 
def namespace(request):
    """Return the namespace from command line or default."""
    return request.config.getoption("--namespace")


@pytest.fixture(scope="module")
def skip_cleanup(request):
    """Return whether to skip cleanup."""
    return request.config.getoption("--skip-cleanup")


@pytest.fixture
def integration_values():
    """Return default integration test values."""
    return {
        "version": "4.12.5",
        "mode": "standalone",
        "replicas": 1,
        "instanceType": "e2-medium",
        "storage": 20,
        "hostNetworkEnabled": False,
        "nodePortEnabled": False,
        "fixedPodIPEnabled": False,
        "loadBalancerEnabled": False,
        "podAntiAffinityEnabled": False,
        "sentinel": {"enabled": False},
        "extra": {"disableExporter": False, "terminationPolicy": "Delete"},
        "falkordbConfig": {
            "cacheSize": "25",
            "nodeCreationBuffer": "16384",
            "maxQueuedQueries": "50",
            "timeoutMax": "0",
            "timeoutDefault": "0",
            "resultSetSize": "10000",
            "queryMemCapacity": "0",
        },
        "persistence": {"rdbConfig": "low", "aofConfig": "everysec"},
        "falkordbUser": {"username": "testuser", "password": "testpass123"},
    }


# pytest_addoption is inherited from parent conftest.py
# No need to duplicate the command line options here