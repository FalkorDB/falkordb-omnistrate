"""Cluster integration test configuration."""

import pytest


@pytest.fixture
def cluster_integration_values():
    """Return default values for cluster integration tests."""
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
def cluster_test_timeout():
    """Return timeout for cluster tests (longer due to complexity)."""
    return 900  # 15 minutes