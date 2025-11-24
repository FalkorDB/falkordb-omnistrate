#!/usr/bin/env python3
"""
Script to run FalkorDB integration tests.

This script provides a comprehensive test runner for FalkorDB integration tests,
including replication and sharding (cluster) deployments.
"""

import argparse
import logging
import subprocess
import sys
import os
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def check_prerequisites():
    """Check if required tools are available."""
    required_tools = ['kubectl', 'helm', 'python']
    
    for tool in required_tools:
        try:
            subprocess.run([tool, '--version'], capture_output=True, check=True)
            logger.info(f"✓ {tool} is available")
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.error(f"✗ {tool} is not available or not working")
            return False
    
    # Check if we can connect to Kubernetes
    try:
        result = subprocess.run(['kubectl', 'cluster-info'], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info("✓ Kubernetes cluster is accessible")
        else:
            logger.error("✗ Cannot connect to Kubernetes cluster")
            logger.error(f"kubectl output: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("✗ Timeout connecting to Kubernetes cluster")
        return False
    except Exception as e:
        logger.error(f"✗ Error checking Kubernetes: {e}")
        return False
    
    return True

def install_falkordb_dependency():
    """Install FalkorDB Python client if not available."""
    try:
        import falkordb
        logger.info("✓ FalkorDB Python client is available")
        return True
    except ImportError:
        logger.info("Installing FalkorDB Python client...")
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'falkordb'], check=True)
            logger.info("✓ FalkorDB Python client installed")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"✗ Failed to install FalkorDB Python client: {e}")
            return False

def run_tests(test_type, namespace="default", cluster_name=None, skip_cleanup=False, verbose=False):
    """Run integration tests."""
    
    # Set up test directory
    test_dir = Path(__file__).parent / "tests"
    os.chdir(test_dir.parent)
    
    # Set up environment variables
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path.cwd())
    
    # Build pytest command
    cmd = ['python', '-m', 'pytest', '-v']
    
    if verbose:
        cmd.append('-s')
    
    # Add specific test markers and paths
    if test_type == 'replication':
        cmd.extend([
            'tests/integration/replication/',
            '-m', 'integration'
        ])
    elif test_type == 'cluster':
        cmd.extend([
            'tests/integration/cluster/', 
            '-m', 'integration'
        ])
    elif test_type == 'all':
        cmd.extend([
            'tests/integration/',
            '-m', 'integration'
        ])
    else:
        logger.error(f"Unknown test type: {test_type}")
        return False
    
    # Add fixtures arguments
    if namespace != "default":
        cmd.extend(['--namespace', namespace])
    
    if cluster_name:
        cmd.extend(['--cluster-name', cluster_name])
    
    if skip_cleanup:
        cmd.append('--skip-cleanup')
    
    # Add timeout
    cmd.extend(['--timeout=1800'])  # 30 minute timeout
    
    logger.info(f"Running command: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, env=env, timeout=2000)  # 33 minute overall timeout
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error("Tests timed out")
        return False
    except Exception as e:
        logger.error(f"Error running tests: {e}")
        return False

def main():
    """Main function."""
    parser = argparse.ArgumentParser(description='Run FalkorDB integration tests')
    parser.add_argument('test_type', choices=['replication', 'cluster', 'all'], 
                       help='Type of tests to run')
    parser.add_argument('--namespace', default='default',
                       help='Kubernetes namespace to use (default: default)')
    parser.add_argument('--cluster-name', 
                       help='Cluster name to use (auto-generated if not provided)')
    parser.add_argument('--skip-cleanup', action='store_true',
                       help='Skip cleanup of test resources')
    parser.add_argument('--skip-prerequisites', action='store_true',
                       help='Skip prerequisite checks')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Increase output verbosity')
    
    args = parser.parse_args()
    
    logger.info("Starting FalkorDB Integration Test Runner")
    logger.info(f"Test type: {args.test_type}")
    logger.info(f"Namespace: {args.namespace}")
    
    # Check prerequisites unless skipped
    if not args.skip_prerequisites:
        logger.info("Checking prerequisites...")
        if not check_prerequisites():
            logger.error("Prerequisite check failed")
            return 1
        
        if not install_falkordb_dependency():
            logger.error("Failed to install FalkorDB dependency")
            return 1
    
    # Run tests
    logger.info("Starting integration tests...")
    success = run_tests(
        test_type=args.test_type,
        namespace=args.namespace,
        cluster_name=args.cluster_name,
        skip_cleanup=args.skip_cleanup,
        verbose=args.verbose
    )
    
    if success:
        logger.info("✓ All tests completed successfully")
        return 0
    else:
        logger.error("✗ Some tests failed")
        return 1

if __name__ == '__main__':
    sys.exit(main())