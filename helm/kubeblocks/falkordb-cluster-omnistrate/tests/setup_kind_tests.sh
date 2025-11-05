#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Setting up Kind cluster for FalkorDB testing...${NC}"

# Check if kind is installed
if ! command -v kind &> /dev/null; then
    echo -e "${RED}Error: kind is not installed${NC}"
    echo "Install kind from: https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
    exit 1
fi

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl is not installed${NC}"
    echo "Install kubectl from: https://kubernetes.io/docs/tasks/tools/"
    exit 1
fi

# Check if helm is installed
if ! command -v helm &> /dev/null; then
    echo -e "${RED}Error: helm is not installed${NC}"
    echo "Install helm from: https://helm.sh/docs/intro/install/"
    exit 1
fi

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
KIND_CONFIG="${SCRIPT_DIR}/kind-config.yaml"
CHART_DIR="${SCRIPT_DIR}/.."

# Check if cluster already exists
if kind get clusters 2>/dev/null | grep -q "^falkordb-test$"; then
    echo -e "${YELLOW}Kind cluster 'falkordb-test' already exists${NC}"
    read -p "Do you want to delete and recreate it? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Deleting existing cluster...${NC}"
        kind delete cluster --name falkordb-test
    else
        echo -e "${GREEN}Using existing cluster${NC}"
        kubectl cluster-info --context kind-falkordb-test
    fi
fi

# Create Kind cluster if it doesn't exist
if ! kind get clusters 2>/dev/null | grep -q "^falkordb-test$"; then
    echo -e "${GREEN}Creating Kind cluster...${NC}"
    kind create cluster --config "$KIND_CONFIG"
fi

# Wait for cluster to be ready
echo -e "${GREEN}Waiting for cluster to be ready...${NC}"
kubectl wait --for=condition=Ready nodes --all --timeout=300s

# Install KubeBlocks
echo -e "${GREEN}Installing KubeBlocks...${NC}"

# Add KubeBlocks helm repo
helm repo add kubeblocks https://apecloud.github.io/helm-charts
helm repo update

# Check if KubeBlocks is already installed
if helm list -n kb-system 2>/dev/null | grep -q "kubeblocks"; then
    echo -e "${YELLOW}KubeBlocks already installed, upgrading...${NC}"
    helm upgrade --install kubeblocks kubeblocks/kubeblocks \
        --namespace kb-system \
        --create-namespace \
        --set autoInstalledAddons={} \
        --wait \
        --timeout 10m
else
    echo -e "${GREEN}Installing KubeBlocks...${NC}"
    kubectl create -f https://github.com/apecloud/kubeblocks/releases/download/v1.0.1/kubeblocks_crds.yaml

    helm install kubeblocks kubeblocks/kubeblocks \
        --namespace kb-system \
        --version v1.0.1 \
        --create-namespace \
        --set autoInstalledAddons={} \
        --wait \
        --timeout 10m
fi

# Wait for KubeBlocks to be ready
echo -e "${GREEN}Waiting for KubeBlocks pods to be ready...${NC}"
kubectl wait --for=condition=Ready pods --all -n kb-system --timeout=300s

# Install FalkorDB addon
echo -e "${GREEN}Installing FalkorDB addon...${NC}"

# Check if addon repository exists in the parent directories
ADDON_PATH="${SCRIPT_DIR}/../../kubeblocks-addons"
if [ -d "$ADDON_PATH" ]; then
    echo -e "${GREEN}Found FalkorDB addon at ${ADDON_PATH}${NC}"
    helm dependency build "${ADDON_PATH}/addons/falkordb"
    # Apply the addon
    helm upgrade --install falkordb --namespace kb-system "${ADDON_PATH}/addons/falkordb"
else
    echo -e "${YELLOW}Warning: FalkorDB addon not found at ${ADDON_PATH}${NC}"
    echo -e "${YELLOW}You may need to manually install the FalkorDB addon${NC}"
fi

# Wait for addon to be ready
echo -e "${GREEN}Waiting for FalkorDB addon to be enabled...${NC}"
sleep 10

# Verify addon installation
if kubectl -n kb-system get clusterdefinitions falkordb 2>/dev/null; then
    echo -e "${GREEN}FalkorDB addon installed successfully${NC}"
    
    # Wait for addon to be enabled
    timeout=60
    elapsed=0
    while [ $elapsed -lt $timeout ]; do
        status=$(kubectl -n kb-system get clusterdefinitions falkordb -o jsonpath='{.status.phase}' 2>/dev/null || echo "")
        if [ "$status" = "Available" ]; then
            echo -e "${GREEN}FalkorDB addon is enabled${NC}"
            break
        fi
        echo -e "${YELLOW}Waiting for addon to be enabled... (${elapsed}s/${timeout}s)${NC}"
        sleep 5
        elapsed=$((elapsed + 5))
    done
else
    echo -e "${YELLOW}Warning: Could not verify FalkorDB addon installation${NC}"
fi

# Create test namespace
NAMESPACE="${NAMESPACE:-falkordb-test}"
echo -e "${GREEN}Creating test namespace: ${NAMESPACE}${NC}"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Build Helm dependencies
echo -e "${GREEN}Building Helm chart dependencies...${NC}"
cd "$CHART_DIR"
helm dependency build

echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "Cluster info:"
kubectl cluster-info --context kind-falkordb-test
echo ""
echo -e "${GREEN}You can now run tests with:${NC}"
echo "  cd $SCRIPT_DIR"
echo "  ./run_tests.sh"
echo ""
echo -e "${GREEN}Or deploy FalkorDB manually with:${NC}"
echo "  helm install my-falkordb $CHART_DIR -n $NAMESPACE"
