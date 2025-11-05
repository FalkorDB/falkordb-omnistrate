# FalkorDB Integration Tests

This directory contains comprehensive integration tests for FalkorDB deployments on Kubernetes using KubeBlocks.

## Overview

The integration tests verify real-world deployment scenarios for FalkorDB in different modes:

1. **Replication Mode**: Master-slave replication with Redis Sentinel
2. **Cluster Mode**: Sharded cluster deployment for horizontal scaling

## Test Structure

```
tests/
├── integration/
│   ├── cluster/                    # Cluster (sharding) mode tests
│   │   └── test_cluster_integration.py
│   ├── replication/                # Replication mode tests
│   │   └── test_replication_integration.py
│   └── standalone/                 # Standalone mode tests
│       └── test_standalone_integration.py
├── utils/                          # Shared utilities
│   ├── kubernetes.py              # Kubernetes helper functions
│   └── validation.py              # Validation utilities
└── run_tests.sh                   # Main test runner script
```

## Prerequisites

### Required Tools
- `kubectl` - Kubernetes CLI tool
- `helm` - Helm package manager  
- `python` 3.8+ - Python interpreter
- Access to a Kubernetes cluster with KubeBlocks installed

### Python Dependencies
- `pytest` - Test framework
- `kubernetes` - Kubernetes Python client
- `falkordb` - FalkorDB Python client
- `pyyaml` - YAML processing

### Kubernetes Requirements
- Kubernetes cluster (version 1.20+)
- KubeBlocks operator installed
- Sufficient resources for test deployments
- Storage classes available for persistent volumes

## Running Integration Tests

### Method 1: Using the Test Runner Script
```bash
# Run all integration tests
./tests/run_tests.sh

# Run only replication tests
pytest -v -m integration tests/integration/replication/

# Run only cluster tests  
pytest -v -m integration tests/integration/cluster/

# Run with custom namespace
pytest -v -m integration tests/integration/ --namespace=my-test-ns
```

### Method 2: Using the Python Runner
```bash
# Run all integration tests
./run_integration_tests.py all

# Run only replication tests
./run_integration_tests.py replication

# Run only cluster tests
./run_integration_tests.py cluster --verbose

# Skip cleanup for debugging
./run_integration_tests.py replication --skip-cleanup
```

### Method 3: Manual pytest Execution
```bash
cd helm/kubeblocks/falkordb-cluster-omnistrate

# Install dependencies
pip install pytest kubernetes falkordb pyyaml

# Run specific test
pytest -v tests/integration/replication/test_replication_integration.py::TestReplicationIntegration::test_replication_deployment_basic
```

## Test Coverage

### Replication Mode Tests

#### `test_replication_deployment_basic`
- Deploys a 3-node replication setup with Redis Sentinel
- Validates connections to master and replica nodes
- Verifies replication status

#### `test_replication_data_persistence` 
- Creates test data on master node
- Validates data replication to replica nodes
- Uses FalkorDB graph operations for testing

#### `test_replication_failover`
- Simulates master node failure
- Validates automatic failover behavior
- Ensures data persistence through failover

#### `test_sentinel_monitoring`
- Validates Redis Sentinel deployment
- Checks sentinel monitoring functionality
- Verifies sentinel log output

### Cluster Mode Tests

#### `test_cluster_deployment_basic`
- Deploys a 6-node sharded cluster
- Validates connections to all cluster nodes
- Verifies cluster status and sharding

#### `test_cluster_data_distribution`
- Tests data distribution across shards
- Validates cluster topology
- Ensures proper data partitioning

#### `test_cluster_node_failure_resilience`
- Simulates node failures
- Tests cluster resilience and recovery
- Validates data availability during failures

#### `test_cluster_scaling_capability`
- Tests cluster scaling operations
- Validates scaling behavior
- Ensures data consistency during scaling

#### `test_cluster_performance_basic`
- Basic performance validation
- Concurrent operation testing
- Resource utilization checks

## Test Configuration

### Environment Variables
- `NAMESPACE` - Kubernetes namespace (default: `default`)  
- `CLUSTER_NAME` - Test cluster name (default: `test-cluster`)
- `SKIP_CLEANUP` - Skip resource cleanup (default: `false`)

### Pytest Fixtures
- `helm_render` - Renders Helm templates for testing
- `namespace` - Provides test namespace
- `cluster_name` - Provides test cluster name
- `skip_cleanup` - Controls cleanup behavior

## Utilities

### Kubernetes Utilities (`tests/utils/kubernetes.py`)
- `KubernetesHelper` - Main helper class for K8s operations
- `wait_for_deployment_ready()` - Wait for cluster deployment
- `wait_for_pods_ready()` - Wait for pods to be ready
- `port_forward_pod()` - Port forwarding context manager
- `kubectl_apply_manifest()` - Apply K8s manifests
- `cleanup_test_resources()` - Clean up test resources
- `get_cluster_pods()` - Get cluster pod names
- `get_pod_logs()` - Retrieve pod logs

### Validation Utilities (`tests/utils/validation.py`)
- `validate_falkordb_connection()` - Test FalkorDB connectivity
- `validate_replication_status()` - Check replication status
- `validate_cluster_status()` - Validate cluster topology
- `validate_basic_cluster_properties()` - Manifest validation
- Various manifest validation functions

## Troubleshooting

### Common Issues

**Connection Errors**
```
ConnectionResetError: Connection reset by peer
```
- Ensure Kubernetes cluster is accessible
- Verify `kubectl cluster-info` works
- Check if KubeBlocks is installed

**Resource Constraints**
```
Insufficient resources for deployment
```
- Check cluster resources: `kubectl top nodes`
- Ensure storage classes are available
- Verify resource quotas

**Timeout Issues**
```
Timeout waiting for pods to be ready
```
- Increase timeout values in test configuration
- Check pod logs: `kubectl logs <pod-name>`
- Verify storage provisioning

### Debugging

**Enable Verbose Logging**
```bash
pytest -v -s tests/integration/ --log-cli-level=DEBUG
```

**Skip Cleanup for Investigation**
```bash
pytest tests/integration/ --skip-cleanup
```

**Check Resources After Tests**
```bash
kubectl get all -l app.kubernetes.io/instance=test-cluster
kubectl get pvc -l app.kubernetes.io/instance=test-cluster
kubectl describe cluster test-cluster
```

## Development

### Adding New Tests
1. Create test functions in appropriate test files
2. Use existing fixtures and utilities
3. Follow naming convention: `test_<functionality>_<aspect>`
4. Add proper assertions and logging
5. Ensure cleanup in finally blocks

### Extending Utilities
1. Add functions to `kubernetes.py` or `validation.py`
2. Update imports in test files
3. Add appropriate error handling
4. Document function parameters and return values

### Best Practices
- Use descriptive test names
- Add comprehensive logging
- Handle exceptions gracefully
- Clean up resources properly
- Use appropriate timeouts
- Validate all critical functionality

## CI/CD Integration

The tests can be integrated into CI/CD pipelines:

```yaml
# Example GitHub Actions step
- name: Run Integration Tests
  run: |
    # Setup K8s cluster (minikube, kind, etc.)
    # Install KubeBlocks
    ./tests/run_tests.sh
  env:
    NAMESPACE: ci-test
    SKIP_SETUP: "false"
```

## Contributing

When contributing to integration tests:

1. Ensure tests are idempotent
2. Add appropriate documentation
3. Test on multiple Kubernetes versions
4. Consider resource usage and cleanup
5. Add validation for edge cases
6. Update this README for new functionality