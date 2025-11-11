import logging
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


def pytest_addoption(parser):
    """Add pytest command line options."""
    logging.info("Adding pytest options")
    parser.addoption(
        "--cluster-name",
        action="store",
        default="test-cluster",
        help="Name of the test cluster",
    )
    parser.addoption(
        "--namespace",
        action="store",
        default="default",
        help="Kubernetes namespace for tests",
    )
    parser.addoption(
        "--skip-cleanup",
        action="store_true",
        default=False,
        help="Skip cleanup after tests",
    )


@pytest.fixture(scope="session")
def chart_path():
    """Return the path to the Helm chart."""
    return Path(__file__).parent.parent.absolute()


@pytest.fixture(scope="session")
def test_values():
    """Return default test values for the chart."""
    return {
        "version": "4.12.5",
        "mode": "standalone",
        "replicas": 1,
        "instanceType": "low",
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


@pytest.fixture(scope="module")
def worker_id(request):
    """
    Return the worker ID for pytest-xdist parallel execution.
    Returns 'master' for single-process execution.
    """
    if hasattr(request.config, 'workerinput'):
        return request.config.workerinput['workerid']
    return 'master'


def render_helm_template(chart_path, values, release_name="test", namespace="default"):
    """
    Render Helm templates using helm template command.
    
    Args:
        chart_path: Path to the Helm chart
        values: Dictionary of values to pass to helm
        release_name: Name of the release
        namespace: Kubernetes namespace
    
    Returns:
        List of rendered Kubernetes manifests
    """
    import tempfile
    
    # Write values to temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(values, f)
        values_file = f.name
    
    try:
        # Run helm template
        cmd = [
            "helm", "template", release_name,
            str(chart_path),
            "--namespace", namespace,
            "--values", values_file,
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse YAML documents
        manifests = list(yaml.safe_load_all(result.stdout))
        # Filter out None values (empty documents)
        manifests = [m for m in manifests if m is not None]
        
        return manifests
    
    finally:
        # Clean up temporary file
        os.unlink(values_file)


@pytest.fixture(scope="session")
def helm_render(chart_path):
    """Fixture to render Helm templates."""
    def _render(values, release_name="test", namespace="default"):
        return render_helm_template(chart_path, values, release_name, namespace)
    return _render
