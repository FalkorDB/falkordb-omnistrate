#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default values
NAMESPACE="${NAMESPACE:-falkordb-test}"
CLUSTER_NAME="${CLUSTER_NAME:-test-cluster}"
SKIP_SETUP="${SKIP_SETUP:-false}"
TEST_MANIFEST_ONLY="${TEST_MANIFEST_ONLY:-false}"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-setup)
            SKIP_SETUP=true
            shift
            ;;
        --manifest-only)
            TEST_MANIFEST_ONLY=true
            shift
            ;;
        --namespace)
            NAMESPACE="$2"
            shift 2
            ;;
        --cluster-name)
            CLUSTER_NAME="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-setup       Skip Kind cluster setup"
            echo "  --skip-cleanup     Skip cluster cleanup after tests"
            echo "  --manifest-only    Only run manifest rendering tests"
            echo "  --namespace NAME   Kubernetes namespace (default: falkordb-test)"
            echo "  --cluster-name NAME  Cluster name (default: test-falkordb)"
            echo "  -h, --help         Show this help message"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

echo -e "${GREEN}FalkorDB Test Runner${NC}"
echo "===================="
echo "Namespace: $NAMESPACE"
echo "Cluster Name: $CLUSTER_NAME"
echo "Skip Setup: $SKIP_SETUP"
echo "Manifest Only: $TEST_MANIFEST_ONLY"
echo ""

# Setup Kind cluster if needed
if [ "$SKIP_SETUP" != "true" ] && [ "$TEST_MANIFEST_ONLY" != "true" ]; then
    echo -e "${GREEN}Running setup...${NC}"
    bash "${SCRIPT_DIR}/setup_kind_tests.sh"
fi

# Setup Python virtual environment
VENV_DIR="${SCRIPT_DIR}/../../../../.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${GREEN}Creating Python virtual environment...${NC}"
    python3 -m venv "$VENV_DIR"
fi

# Activate virtual environment
echo -e "${GREEN}Activating virtual environment...${NC}"
source "${VENV_DIR}/bin/activate"

# Install requirements
echo -e "${GREEN}Installing Python dependencies...${NC}"
pip install -q --upgrade pip
poetry install -q

# Cleanup any existing test clusters before running tests
if [ "$TEST_MANIFEST_ONLY" != "true" ]; then
    echo -e "${YELLOW}Cleaning up any existing test clusters...${NC}"
    
    # Check if test-cluster helm release exists (including failed releases)
    if helm list -n "$NAMESPACE" --all 2>/dev/null | grep -q "test-cluster"; then
        echo -e "${YELLOW}Uninstalling existing test-cluster helm release from namespace ${NAMESPACE}...${NC}"
        helm uninstall test-cluster -n "$NAMESPACE" 2>/dev/null || true
        
        # Wait a bit for resources to be cleaned up
        sleep 3
    fi
    
    # Cleanup any leftover PVCs
    if kubectl get pvc -n "$NAMESPACE" 2>/dev/null | grep -q "test-cluster"; then
        echo -e "${YELLOW}Cleaning up leftover PVCs from namespace ${NAMESPACE}...${NC}"
        kubectl delete pvc -n "$NAMESPACE" -l app.kubernetes.io/instance=test-cluster 2>/dev/null || true
    fi
fi

# Run tests
echo -e "${GREEN}Running tests...${NC}"
cd "$SCRIPT_DIR"

# Build pytest arguments
PYTEST_ARGS="-v -n 3 --dist=loadfile --tb=short"
PYTEST_ARGS="$PYTEST_ARGS --cluster-name=$CLUSTER_NAME"
PYTEST_ARGS="$PYTEST_ARGS --namespace=$NAMESPACE"

if [ "$TEST_MANIFEST_ONLY" = "true" ]; then
    # Only run unit tests (manifest rendering)
    echo -e "${GREEN}Running unit tests (manifest rendering) only...${NC}"
    pytest $PYTEST_ARGS unit/
else
    # Check what tests to run
    echo -e "${GREEN}Available test categories:${NC}"
    echo "  - unit/common     : Common unit tests"
    echo "  - unit/standalone : Standalone mode unit tests"
    echo "  - unit/replication: Replication mode unit tests"
    echo "  - unit/cluster    : Cluster mode unit tests"
    echo "  - integration/    : Integration tests (requires K8s cluster)"
    echo ""
    
    # Run unit tests first
    echo -e "${GREEN}Running unit tests...${NC}"
    pytest $PYTEST_ARGS unit/ || {
        echo -e "${YELLOW}Unit tests failed, skipping integration tests${NC}"
        TEST_EXIT_CODE=1
    }
    
    # Run integration tests if unit tests passed and we're not in manifest-only mode
    if [ ${TEST_EXIT_CODE:-0} -eq 0 ]; then
        echo -e "${GREEN}Running integration tests...${NC}"
        
        # Install FalkorDB Python client if not available
        pip install -q falkordb 2>/dev/null || echo -e "${YELLOW}Warning: Could not install FalkorDB client${NC}"
        
        # Run integration tests with proper timeout and setup
        pytest $PYTEST_ARGS -m integration integration/ --timeout=1800 || TEST_EXIT_CODE=1
    fi
fi

# Capture exit code
TEST_EXIT_CODE=$?

# Deactivate virtual environment
deactivate

# Print results
echo ""
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ All tests passed!${NC}"
else
    echo -e "${RED}✗ Some tests failed${NC}"
fi

exit $TEST_EXIT_CODE
