"""Replication-specific test configuration."""

import pytest


@pytest.fixture
def replication_values():
    """Return default values for replication mode tests."""
    return {
        "mode": "replication",
        "replicas": 3,
        "sentinel": {
            "enabled": True,
            "replicas": 3
        },
        "instanceType": "e2-medium",
        "storage": 20,
        "falkordbUser": {
            "username": "testuser",
            "password": "testpass123"
        },
    }


@pytest.fixture
def replication_cluster_name():
    """Return a cluster name for replication tests."""
    return "test-replication-cluster"