"""Replication integration test configuration."""

import pytest


@pytest.fixture
def replication_integration_values():
    """Return default values for replication integration tests."""
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
def replication_test_timeout():
    """Return timeout for replication tests (longer due to sentinel setup)."""
    return 600  # 10 minutes