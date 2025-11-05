"""
Utilities for Helm template rendering and manifest processing.
"""

import os
import subprocess
import tempfile
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional


def render_helm_template(
    chart_path: Path,
    values: Dict[str, Any],
    release_name: str = "test",
    namespace: str = "default"
) -> List[Dict[str, Any]]:
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


def find_manifest_by_kind(manifests: List[Dict[str, Any]], kind: str) -> Optional[Dict[str, Any]]:
    """
    Find the first manifest of a specific kind.
    
    Args:
        manifests: List of Kubernetes manifests
        kind: Kubernetes resource kind to find
        
    Returns:
        First manifest matching the kind, or None if not found
    """
    return next((m for m in manifests if m.get("kind") == kind), None)


def find_manifests_by_kind(manifests: List[Dict[str, Any]], kind: str) -> List[Dict[str, Any]]:
    """
    Find all manifests of a specific kind.
    
    Args:
        manifests: List of Kubernetes manifests
        kind: Kubernetes resource kind to find
        
    Returns:
        List of all manifests matching the kind
    """
    return [m for m in manifests if m.get("kind") == kind]


def find_manifest_by_name(manifests: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """
    Find manifest by name.
    
    Args:
        manifests: List of Kubernetes manifests
        name: Name of the resource to find
        
    Returns:
        First manifest matching the name, or None if not found
    """
    return next((m for m in manifests if m.get("metadata", {}).get("name") == name), None)


def get_cluster_component_spec(cluster_manifest: Dict[str, Any], component_name: str = "falkordb") -> Optional[Dict[str, Any]]:
    """
    Get component spec from a Cluster manifest.
    Handles both componentSpecs (standalone/replication) and shardings (cluster) structures.
    
    Args:
        cluster_manifest: Cluster manifest dictionary
        component_name: Name of the component (default: "falkordb")
        
    Returns:
        Component spec dictionary or None if not found
    """
    spec = cluster_manifest.get("spec", {})
    
    # Try componentSpecs first (standalone/replication mode)
    component_specs = spec.get("componentSpecs", [])
    for component in component_specs:
        if component.get("name") == component_name:
            return component
    
    # Try shardings structure (cluster mode)
    shardings = spec.get("shardings", [])
    for sharding in shardings:
        template = sharding.get("template", {})
        if template.get("name") == component_name:
            return template
    
    return None


def get_environment_variables(component_spec: Dict[str, Any]) -> Dict[str, str]:
    """
    Extract environment variables from a component spec.
    
    Args:
        component_spec: Component specification dictionary
        
    Returns:
        Dictionary mapping environment variable names to values
    """
    env_vars = {}
    for env in component_spec.get("env", []):
        if "name" in env and "value" in env:
            env_vars[env["name"]] = env["value"]
    return env_vars


def get_resource_limits(component_spec: Dict[str, Any]) -> Dict[str, str]:
    """
    Extract resource limits from a component spec.
    
    Args:
        component_spec: Component specification dictionary
        
    Returns:
        Dictionary with 'cpu' and 'memory' limits
    """
    resources = component_spec.get("resources", {})
    limits = resources.get("limits", {})
    return {
        "cpu": limits.get("cpu", ""),
        "memory": limits.get("memory", "")
    }


def get_resource_requests(component_spec: Dict[str, Any]) -> Dict[str, str]:
    """
    Extract resource requests from a component spec.
    
    Args:
        component_spec: Component specification dictionary
        
    Returns:
        Dictionary with 'cpu' and 'memory' requests
    """
    resources = component_spec.get("resources", {})
    requests = resources.get("requests", {})
    return {
        "cpu": requests.get("cpu", ""),
        "memory": requests.get("memory", "")
    }


def get_volume_claim_template(component_spec: Dict[str, Any], name: str = "data") -> Optional[Dict[str, Any]]:
    """
    Get volume claim template from component spec.
    
    Args:
        component_spec: Component specification dictionary
        name: Name of the volume claim template
        
    Returns:
        Volume claim template dictionary or None if not found
    """
    templates = component_spec.get("volumeClaimTemplates", [])
    for template in templates:
        if template.get("name") == name:
            return template
    return None