# FalkorDB Helm Chart Tests

This directory contains comprehensive tests for the FalkorDB Kubeblocks Helm chart, organized by deployment mode and test type.

## Test Structure

The tests are organized into the following structure:

```
tests/
├── unit/                       # Unit tests (template rendering)
│   ├── common/                 # Tests common to all deployment modes
│   ├── standalone/             # Standalone mode specific tests
│   ├── replication/            # Replication mode specific tests
│   └── cluster/                # Cluster mode specific tests
├── integration/                # Integration tests (real K8s deployment)
│   ├── common/                 # Common integration tests
│   ├── standalone/             # Standalone integration tests
│   ├── replication/            # Replication integration tests
│   └── cluster/                # Cluster integration tests
├── utils/                      # Test utilities and helpers
│   ├── rendering.py            # Helm template rendering utilities
│   ├── kubernetes.py           # Kubernetes API utilities
│   └── validation.py           # Validation utilities
├── conftest.py                 # Global test configuration
├── requirements.txt            # Python test dependencies
└── run_tests.sh               # Test runner script
```

## Deployment Modes

The FalkorDB Helm chart supports three deployment modes:

### 1. Standalone Mode
- Single FalkorDB instance
- No replication
- Basic deployment for development/testing
- Tests: `unit/standalone/`, `integration/standalone/`

### 2. Replication Mode
- Master-replica setup with Redis Sentinel
- High availability through automatic failover
- Read scaling through replicas
- Tests: `unit/replication/`, `integration/replication/`

### 3. Cluster Mode
- Redis cluster mode for horizontal scaling
- Data sharding across multiple nodes
- High availability and scalability
- Tests: `unit/cluster/`, `integration/cluster/`

## Test Types

### Unit Tests
Unit tests focus on Helm template rendering and validation without requiring a Kubernetes cluster:

- **Template Rendering**: Verify that Helm templates render correctly with different values
- **Manifest Validation**: Validate generated Kubernetes manifests structure and content
- **Configuration Testing**: Test various configuration combinations
- **Resource Mapping**: Verify correct resource limits and requests mapping

### Integration Tests
Integration tests deploy actual resources to a Kubernetes cluster:

- **Deployment Testing**: Deploy charts and verify successful startup
- **Connectivity Testing**: Test FalkorDB connections and functionality
- **Data Persistence**: Verify data survives pod restarts
- **Failover Testing**: Test high availability scenarios
- **Performance Testing**: Basic performance characteristics

## Running Tests

### Prerequisites

1. **For Unit Tests Only**:
   - Python 3.8+
   - Helm 3.x
   - The dependencies in `requirements.txt`

2. **For Integration Tests**:
   - All unit test prerequisites
   - Kubernetes cluster (local or remote)
   - kubectl configured to access the cluster
   - KubeBlocks operator installed in the cluster

### Test Execution

#### Run All Tests
```bash
./run_tests.sh
```

#### Run Only Unit Tests (No K8s cluster required)
```bash
./run_tests.sh --manifest-only
```

#### Run Specific Test Categories
```bash
# Unit tests only
pytest -v unit/

# Integration tests only
pytest -v -m integration integration/

# Specific deployment mode
pytest -v unit/standalone/
pytest -v integration/replication/

# Specific test file
pytest -v unit/common/test_basic_rendering.py
```

#### Custom Configuration
```bash
# Custom namespace and cluster name
./run_tests.sh --namespace my-test-ns --cluster-name my-test-cluster

# Skip Kind cluster setup (use existing cluster)
./run_tests.sh --skip-setup
```

### Test Markers

Tests use pytest markers for categorization:

- `@pytest.mark.integration`: Integration tests requiring K8s cluster
- `@pytest.mark.unit`: Unit tests (template rendering only)
- `@pytest.mark.slow`: Slow-running tests
- `@pytest.mark.standalone`: Standalone mode specific tests
- `@pytest.mark.replication`: Replication mode specific tests
- `@pytest.mark.cluster`: Cluster mode specific tests

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NAMESPACE` | Kubernetes namespace for tests | `falkordb-test` |
| `CLUSTER_NAME` | Test cluster name | `test-cluster` |
| `SKIP_SETUP` | Skip Kind cluster setup | `false` |
| `TEST_MANIFEST_ONLY` | Run only unit tests | `false` |

## Test Utilities

### Rendering Utilities (`utils/rendering.py`)
- `render_helm_template()`: Render Helm templates with given values
- `parse_manifests()`: Parse YAML manifests from Helm output
- `find_manifest_by_kind()`: Find specific manifest types

### Kubernetes Utilities (`utils/kubernetes.py`)
- `wait_for_deployment_ready()`: Wait for deployment readiness
- `wait_for_pods_ready()`: Wait for pod readiness
- `port_forward_pod()`: Create port forward to pod
- `cleanup_test_resources()`: Clean up test resources

### Validation Utilities (`utils/validation.py`)
- `validate_falkordb_connection()`: Test FalkorDB connectivity
- `validate_replication_status()`: Check replication status
- `validate_cluster_status()`: Check cluster status
- `validate_basic_cluster_properties()`: Validate manifest structure

## Common Test Patterns

### Unit Test Pattern
```python
def test_manifest_rendering(self, helm_render):
    values = {"mode": "standalone", "replicas": 1}
    manifests = helm_render(values)
    
    cluster_manifest = next(
        (m for m in manifests if m["kind"] == "FalkorDBCluster"), None
    )
    assert cluster_manifest is not None
    assert cluster_manifest["spec"]["mode"] == "standalone"
```

### Integration Test Pattern
```python
@pytest.mark.integration
def test_deployment(self, helm_render, namespace, cluster_name, skip_cleanup):
    values = {"mode": "standalone"}
    manifests = helm_render(values, release_name=cluster_name, namespace=namespace)
    
    try:
        # Apply manifests
        # Wait for readiness
        # Test functionality
        pass
    finally:
        if not skip_cleanup:
            cleanup_test_resources(cluster_name, namespace)
```

## Troubleshooting

### Common Issues

1. **Import Errors**: Install dependencies with `pip install -r requirements.txt`

2. **Kubernetes Connection**: Ensure kubectl is configured and cluster is accessible

3. **Permission Errors**: Ensure service account has necessary permissions for test namespace

4. **Resource Conflicts**: Clean up previous test runs with `kubectl delete namespace falkordb-test`

5. **Timeout Issues**: Increase timeout values for slower environments

### Debug Mode

Run tests with verbose output and no capture:
```bash
pytest -v -s --tb=long unit/
```

View logs from failed pods:
```bash
kubectl logs -n falkordb-test test-cluster-falkordb-0
```

## Contributing

When adding new tests:

1. Place unit tests in appropriate `unit/` subdirectory
2. Place integration tests in appropriate `integration/` subdirectory
3. Add shared utilities to `utils/` directory
4. Use appropriate pytest markers
5. Follow existing test patterns and naming conventions
6. Update this README for new test categories or utilities

### Test Naming Conventions

- Test files: `test_*.py`
- Test classes: `Test*`
- Test methods: `test_*`
- Use descriptive names that indicate what is being tested

### Documentation

- Add docstrings to test classes and methods
- Document complex test scenarios
- Update README for new features or changes
