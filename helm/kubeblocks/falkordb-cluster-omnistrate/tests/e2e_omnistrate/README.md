# E2E Tests for FalkorDB Omnistrate Deployments

This directory contains end-to-end (E2E) tests for validating FalkorDB deployments on the Omnistrate platform. These tests create real cloud instances and verify functionality across different topologies.

## Test Coverage

The test suite covers three deployment topologies:

- **Standalone** (`test_omnistrate_standalone.py`): Single-node deployments
- **Replication** (`test_omnistrate_replication.py`): Master-replica deployments with Redis Sentinel
- **Cluster** (`test_omnistrate_cluster.py`): Sharded cluster deployments

Each topology includes tests for:
- Basic connectivity and data operations
- Stop/start operations
- Vertical scaling (instance type changes)
- Horizontal scaling (replicas/shards)
- Failover scenarios
- OOM (Out of Memory) resilience
- Data persistence
- Multi-zone distribution

## Prerequisites

1. **Omnistrate Account**: Valid Omnistrate credentials with API access
2. **Service Configuration**: Pre-deployed FalkorDB service on Omnistrate
3. **Python Environment**: Python 3.8+ with dependencies:
   ```bash
   pip install pytest redis requests
   ```

## Configuration

### Required Environment Variables

```bash
# Omnistrate API Credentials
export OMNISTRATE_USER="your-email@example.com"
export OMNISTRATE_PASSWORD="your-password"

# Service Configuration (get from Omnistrate console)
export OMNISTRATE_SERVICE_ID="service-xxx-xxx"
export OMNISTRATE_ENVIRONMENT_ID="env-xxx-xxx"

# Cloud Provider Configuration
export CLOUD_PROVIDER="aws"  # or "gcp", "azure"
export REGION="us-east-1"    # cloud provider region
```

### Optional Environment Variables

```bash
# Service Configuration
export TIER_NAME="enterprise"                    # Service tier (default: free)
export SERVICE_MODEL_NAME="single-Zone"          # Topology (default: single-Zone)
export DEPLOYMENT_CLOUD_PROVIDER="aws"           # Override cloud provider for deployment

# Instance Configuration
export INSTANCE_TYPE="m5.large"                  # Default instance type
export NEW_INSTANCE_TYPE="m5.xlarge"             # For vertical scaling tests
export STORAGE_SIZE="50"                         # Storage size in GB (default: 20)
export REPLICA_COUNT="2"                         # Number of replicas (default: 2)
export SHARD_COUNT="3"                           # Number of shards for cluster (default: 3)

# Network Configuration
export NETWORK_TYPE="PUBLIC"                     # PUBLIC or INTERNAL (default: PUBLIC)
export CUSTOM_NETWORK_ID="network-xxx"           # For INTERNAL network type
export CUSTOM_NETWORK_NAME="my-vpc"              # Name for custom network

# Security
export TLS="true"                                # Enable TLS/SSL (default: false)

# Timeouts (in seconds)
export READY_TIMEOUT="1800"                      # Instance ready timeout (default: 1800)
export STOP_TIMEOUT="600"                        # Stop operation timeout (default: 600)
export UPDATE_TIMEOUT="1800"                     # Update operation timeout (default: 1800)

# Test Behavior
export PERSIST_ON_FAIL="true"                    # Don't delete instances on test failure (default: false)
export SKIP_TEARDOWN="true"                      # Skip all instance cleanup (default: false)
```

## Running Tests

### Run All Tests (All Topologies)

```bash
cd helm/kubeblocks/falkordb-cluster-omnistrate/tests/e2e_omnistrate
pytest -v
```

### Run Specific Topology

```bash
# Standalone tests only
pytest -v -m standalone test_omnistrate_standalone.py

# Replication tests only
pytest -v -m replication test_omnistrate_replication.py

# Cluster tests only
pytest -v -m cluster test_omnistrate_cluster.py
```

### Run Specific Test

```bash
# Single test method
pytest -v test_omnistrate_standalone.py::TestOmnistrateStandalone::test_standalone_basic_connectivity

# Full suite test
pytest -v test_omnistrate_replication.py::test_replication_full_suite
```

### Run with Specific Steps

Use the `--e2e-steps` flag to control which operations to test:

```bash
# Only test failover operations
pytest -v --e2e-steps failover test_omnistrate_replication.py

# Test multiple specific operations
pytest -v --e2e-steps "failover,stopstart,resize" test_omnistrate_cluster.py

# Run all operations (default)
pytest -v --e2e-steps all test_omnistrate_replication.py
```

Available steps:
- `failover`: Failover tests (replication/cluster)
- `stopstart`: Stop/start operations
- `scale-replicas`: Replica scaling
- `scale-shards`: Shard scaling (cluster only)
- `resize`: Vertical scaling (instance type changes)
- `oom`: Out-of-memory resilience tests
- `storage-expand`: Storage expansion tests
- `persistence`: Persistence configuration tests
- `concurrent`: Concurrent operations tests
- `all`: Run all steps (default)

### Override Configuration via CLI

```bash
# Override cloud provider and region
pytest -v --cloud-provider aws --region us-west-2 test_omnistrate_standalone.py

# Override instance type
pytest -v --instance-type m5.xlarge test_omnistrate_replication.py

# Override network type
pytest -v --network-type INTERNAL --custom-network-id network-123 test_omnistrate_cluster.py

# Override timeouts
pytest -v --ready-timeout 3600 --update-timeout 2400 test_omnistrate_replication.py

# Persist instances on failure for debugging
pytest -v --persist-on-fail test_omnistrate_cluster.py

# Skip teardown entirely (for manual inspection)
pytest -v --skip-teardown test_omnistrate_standalone.py
```

## Test Execution Flow

1. **Setup Phase** (per test):
   - Load configuration from environment and CLI
   - Create Omnistrate API client
   - Fetch service model and tier details
   - Create custom network if INTERNAL network type
   - Generate unique instance name
   - Create and launch instance
   - Wait for instance to be ready
   - Verify DNS resolution

2. **Test Phase**:
   - Execute test-specific operations
   - Verify data persistence
   - Monitor for errors

3. **Teardown Phase** (per test):
   - Delete instance (unless --skip-teardown or --persist-on-fail with failure)
   - Clean up custom network if created
   - Report test results

## Topology-Specific Notes

### Standalone

Simplest topology. Tests basic functionality:
- Single-node instance
- No replication or clustering
- Focus on persistence, resilience, and scaling

Example:
```bash
pytest -v --e2e-steps "stopstart,resize,oom" test_omnistrate_standalone.py
```

### Replication

Master-replica with Redis Sentinel:
- 1 master + N replicas (default: 2)
- Redis Sentinel for high availability
- Tests failover mechanisms
- Supports single-zone and multi-zone

Example:
```bash
# Test failover with 3 replicas
pytest -v \
  --service-model-name single-Zone \
  --replica-count 3 \
  --e2e-steps "failover,scale-replicas" \
  test_omnistrate_replication.py
```

### Cluster

Sharded cluster topology:
- N shards (default: 3)
- M replicas per shard (default: 1)
- Data distributed across shards
- Tests shard rebalancing and cross-shard queries
- Supports single-zone and multi-zone

Example:
```bash
# Test 5-shard cluster with 2 replicas per shard
pytest -v \
  --service-model-name cluster-Single-Zone \
  --shard-count 5 \
  --replica-count 2 \
  --e2e-steps "failover,scale-shards,scale-replicas" \
  test_omnistrate_cluster.py
```

## Multi-Zone Testing

For multi-zone deployments, use the appropriate service model:

```bash
# Multi-zone replication
pytest -v \
  --service-model-name multi-Zone \
  test_omnistrate_replication.py::TestOmnistrateReplication::test_replication_multi_zone_distribution

# Multi-zone cluster
pytest -v \
  --service-model-name cluster-Multi-Zone \
  test_omnistrate_cluster.py::TestOmnistrateCluster::test_cluster_multi_zone_distribution
```

## Debugging Failed Tests

### Persist Instance on Failure

```bash
pytest -v --persist-on-fail test_omnistrate_replication.py
```

This keeps the instance running if a test fails, allowing manual inspection:
1. Check instance status in Omnistrate console
2. Connect to instance using provided endpoint
3. Examine logs and state
4. Manually delete instance when done

### Skip Teardown

```bash
pytest -v --skip-teardown test_omnistrate_cluster.py
```

This skips cleanup for **all** tests, useful when:
- Running tests incrementally
- Need to inspect successful deployments
- Debugging infrastructure issues

⚠️ **Warning**: Remember to manually delete instances to avoid unnecessary charges!

### Verbose Logging

```bash
pytest -v --log-cli-level DEBUG test_omnistrate_standalone.py
```

Shows detailed logs including:
- API requests and responses
- Instance state transitions
- DNS resolution attempts
- Data verification steps

## Common Issues

### Instance Creation Timeout

**Error**: `Instance not ready within timeout`

**Solutions**:
- Increase `--ready-timeout` (default: 1800s)
- Check Omnistrate console for provisioning errors
- Verify cloud provider quotas
- Check region availability

### DNS Resolution Failure

**Error**: `Failed to resolve hostname`

**Solutions**:
- Wait longer for DNS propagation (automatic retries built-in)
- Verify network type configuration
- Check custom network settings for INTERNAL type
- Ensure public DNS for PUBLIC network type

### Authentication Errors

**Error**: `Authentication failed`

**Solutions**:
- Verify `OMNISTRATE_USER` and `OMNISTRATE_PASSWORD`
- Check API access permissions in Omnistrate console
- Ensure account is active and in good standing

### Missing Service Configuration

**Error**: `Service/Tier/Model not found`

**Solutions**:
- Verify `OMNISTRATE_SERVICE_ID` matches deployed service
- Check `TIER_NAME` exists for service (use Omnistrate console)
- Verify `SERVICE_MODEL_NAME` is valid for tier
- Check `OMNISTRATE_ENVIRONMENT_ID` is correct

### Network Type Issues

**Error**: `INTERNAL network requires custom_network_id`

**Solutions**:
- Provide `--custom-network-id` for INTERNAL network type
- Ensure custom network exists in target region
- Verify network peering is configured
- Use PUBLIC network type if VPC not available

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        topology: [standalone, replication, cluster]
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: |
          pip install pytest redis requests
      
      - name: Run E2E Tests
        env:
          OMNISTRATE_USER: ${{ secrets.OMNISTRATE_USER }}
          OMNISTRATE_PASSWORD: ${{ secrets.OMNISTRATE_PASSWORD }}
          OMNISTRATE_SERVICE_ID: ${{ secrets.OMNISTRATE_SERVICE_ID }}
          OMNISTRATE_ENVIRONMENT_ID: ${{ secrets.OMNISTRATE_ENVIRONMENT_ID }}
          CLOUD_PROVIDER: aws
          REGION: us-east-1
        run: |
          cd helm/kubeblocks/falkordb-cluster-omnistrate/tests/e2e_omnistrate
          pytest -v -m ${{ matrix.topology }} test_omnistrate_${{ matrix.topology }}.py
```

### GitLab CI Example

```yaml
e2e-tests:
  stage: test
  image: python:3.10
  parallel:
    matrix:
      - TOPOLOGY: [standalone, replication, cluster]
  before_script:
    - pip install pytest redis requests
  script:
    - cd helm/kubeblocks/falkordb-cluster-omnistrate/tests/e2e_omnistrate
    - pytest -v -m ${TOPOLOGY} test_omnistrate_${TOPOLOGY}.py
  variables:
    OMNISTRATE_USER: ${OMNISTRATE_USER}
    OMNISTRATE_PASSWORD: ${OMNISTRATE_PASSWORD}
    OMNISTRATE_SERVICE_ID: ${OMNISTRATE_SERVICE_ID}
    OMNISTRATE_ENVIRONMENT_ID: ${OMNISTRATE_ENVIRONMENT_ID}
    CLOUD_PROVIDER: aws
    REGION: us-east-1
```

## Test Matrix Recommendations

### Quick Validation (5-10 minutes)
```bash
pytest -v \
  --e2e-steps "connectivity,stopstart" \
  test_omnistrate_standalone.py
```

### Standard Validation (30-60 minutes)
```bash
pytest -v \
  --e2e-steps "stopstart,resize,failover" \
  -m "not slow"
```

### Comprehensive Validation (2-4 hours)
```bash
pytest -v --e2e-steps all
```

### Production Validation (4+ hours)
```bash
# Run full suite for all topologies and zones
pytest -v \
  --e2e-steps all \
  --service-model-name single-Zone
  
pytest -v \
  --e2e-steps all \
  --service-model-name multi-Zone

pytest -v \
  --e2e-steps all \
  --service-model-name cluster-Single-Zone

pytest -v \
  --e2e-steps all \
  --service-model-name cluster-Multi-Zone
```

## Cost Considerations

These tests create real cloud resources and incur costs:

- **Instance runtime**: Charged per hour while running
- **Storage**: Charged for provisioned storage
- **Data transfer**: Egress charges may apply
- **Failed cleanup**: Orphaned instances continue charging

**Best practices**:
- Use `--persist-on-fail` sparingly
- Always verify cleanup after `--skip-teardown`
- Run comprehensive tests in dedicated test environments
- Monitor Omnistrate console for orphaned resources
- Use smaller instance types for development testing
- Consider scheduled cleanup jobs for test environments

## Contributing

When adding new tests:

1. Follow existing patterns in test files
2. Use descriptive test names (e.g., `test_replication_sentinel_failover`)
3. Add appropriate pytest markers (`@pytest.mark.omnistrate`, `@pytest.mark.replication`)
4. Include detailed docstrings
5. Use `_run_step()` helper for optional test steps
6. Add logging statements for debugging
7. Update this README with new configuration options

## Support

For issues or questions:
- Check Omnistrate documentation: https://docs.omnistrate.com
- Review FalkorDB documentation: https://docs.falkordb.com
- Open GitHub issues for test-specific problems
- Contact Omnistrate support for platform issues
