"""Cluster-specific test configuration."""

import pytest


@pytest.fixture
def cluster_values():
    """Return default values for cluster mode tests."""
    return {
        "mode": "cluster",
        "replicas": 6,
        "instanceType": "e2-standard-2",
        "storage": 30,
        "podAntiAffinityEnabled": True,
        "falkordbUser": {
            "username": "testuser",
            "password": "testpass123"
        },
    }


@pytest.fixture
def cluster_cluster_name():
    """Return a cluster name for cluster tests."""
    return "test-cluster-mode"