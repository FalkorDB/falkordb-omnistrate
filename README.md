# FalkorDB Omnistrate Repository

This repository contains the necessary files and configurations for managing and deploying FalkorDB in Omnistrate. Below is a breakdown of the folder structure and the purpose of each directory:

## Folder Structure

### `compose/`
Contains Omnistrate YAML configuration files for different deployment scenarios of FalkorDB:
- `omnistrate.enterprise.byoa.yaml`: Configuration for enterprise deployments with Bring Your Own Account.
- `omnistrate.enterprise.yaml`: Configuration for standard enterprise deployments.
- `omnistrate.free.yaml`: Configuration for free-tier deployments.
- `omnistrate.pro.yaml`: Configuration for professional-tier deployments.
- `omnistrate.startup.yaml`: Configuration for startup-tier deployments.

### `scripts/`
Contains utility scripts for managing and debugging FalkorDB instances:
- `cleanup-qa-instances-script.py`: Script to clean up QA instances.
- `download-debug-so.sh`: Script to download debug shared objects.

### `src/`
Contains the source code for various components of FalkorDB:
- `falkordb-cluster/`: Code and configurations for the FalkorDB cluster.
  - `cluster-entrypoint.sh`: Entrypoint script for the cluster.
  - `Dockerfile`: Dockerfile for building the cluster image.
  - `node.conf`: Configuration file for cluster nodes.
- `falkordb-cluster-rebalance/`: Code for rebalancing FalkorDB clusters.
  - `Dockerfile`: Dockerfile for building the rebalance image.
  - `src/`: Python source code for rebalancing logic.
    - `falkordb_cluster.py`: Core logic for cluster rebalancing.
    - `main.py`: Main entry point for the rebalance application.
    - `requirements.txt`: Python dependencies for the rebalance application.
- `falkordb-node/`: Code and configurations for individual FalkorDB nodes.
  - `Dockerfile`: Dockerfile for building the node image.
  - `node-entrypoint.sh`: Entrypoint script for the node.
  - `node.conf`: Configuration file for nodes.
  - `sentinel.conf`: Configuration file for Sentinel.
- `healthcheck_rs/`: Rust-based health check service for FalkorDB.
  - `Cargo.toml`: Rust project configuration.
  - `src/main.rs`: Main source file for the health check service.

### `tests/`
Contains test scripts for validating FalkorDB functionality:
- `failover_sentinel.py`: Tests for Sentinel failover.
- `failover_standalone.py`: Tests for standalone failover.
- `test_cluster.py`: Tests for cluster functionality.
- `test_replication.py`: Tests for replication functionality.
- `test_standalone.py`: Tests for standalone functionality.
- `utils.py`: Utility functions for tests.
