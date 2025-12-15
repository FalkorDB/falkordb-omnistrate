"""
Common fixtures for unit tests.
"""

import pytest
from pathlib import Path
from ..utils.rendering import render_helm_template


@pytest.fixture(scope="session")
def chart_path():
    """Return the path to the Helm chart."""
    return Path(__file__).parent.parent.parent.absolute()


@pytest.fixture
def helm_render(chart_path):
    """Fixture to render Helm templates."""
    def _render(values, release_name="test", namespace="default"):
        return render_helm_template(chart_path, values, release_name, namespace)
    return _render


@pytest.fixture
def base_values():
    """Return base values for testing."""
    return {
        "version": "4.12.5",
        "instanceType": "e2-medium",
        "storage": 20,
        "hostname": "node.cluster.local",
        "port": 6379,
        "hostNetworkEnabled": False,
        "nodePortEnabled": False,
        "fixedPodIPEnabled": False,
        "loadBalancerEnabled": False,
        "podAntiAffinityEnabled": False,
        "extra": {
            "disableExporter": False,
            "terminationPolicy": "Delete"
        }
    }


@pytest.fixture
def standalone_values(base_values):
    """Return values for standalone deployment."""
    return {
        **base_values,
        "mode": "standalone",
        "replicas": 1,
        "sentinel": {"enabled": False}
    }


@pytest.fixture
def replication_values(base_values):
    """Return values for replication deployment."""
    return {
        **base_values,
        "mode": "replication",
        "replicas": 2,
        "sentinel": {
            "enabled": True,
            "cpu": "0.5",
            "memory": "0.5",
            "storage": 20,
            "replicas": 3
        }
    }


@pytest.fixture
def cluster_values(base_values):
    """Return values for cluster deployment."""
    return {
        **base_values,
        "mode": "cluster",
        "replicas": 2,
        "falkordbCluster": {
            "shardCount": 3
        },
        "sentinel": {"enabled": False}
    }


@pytest.fixture
def instance_type_mappings():
    """Return instance type to resource mappings."""
    return {
        "e2-medium": {"cpu": "700m", "memory": "2800Mi"},
        "e2-standard-2": {"cpu": "1500m", "memory": "6000Mi"},
        "e2-standard-4": {"cpu": "3500m", "memory": "13000Mi"},
        "e2-custom-4-8192": {"cpu": "3500m", "memory": "6000Mi"},
        "e2-custom-8-16384": {"cpu": "7500m", "memory": "13000Mi"},
        "e2-custom-16-32768": {"cpu": "15900m", "memory": "27800Mi"},
        "e2-custom-32-65536": {"cpu": "31900m", "memory": "59392Mi"},
        "t2.medium": {"cpu": "1500m", "memory": "2800Mi"},
        "m6i.large": {"cpu": "1500m", "memory": "6000Mi"},
        "m6i.xlarge": {"cpu": "3900m", "memory": "13900Mi"},
        "c6i.xlarge": {"cpu": "3900m", "memory": "6800Mi"},
        "c6i.2xlarge": {"cpu": "7900m", "memory": "13900Mi"},
        "c6i.4xlarge": {"cpu": "15900m", "memory": "27800Mi"},
        "c6i.8xlarge": {"cpu": "31900m", "memory": "59392Mi"}
    }


@pytest.fixture
def falkordb_config_sample():
    """Return sample FalkorDB configuration."""
    return {
        "cacheSize": "50",
        "nodeCreationBuffer": "32768",
        "maxQueuedQueries": "100",
        "timeoutMax": "1000",
        "timeoutDefault": "500",
        "resultSetSize": "20000",
        "queryMemCapacity": "1000000"
    }


@pytest.fixture
def user_config_sample():
    """Return sample user configuration."""
    return {
        "username": "testuser",
        "password": "testpass123"
    }