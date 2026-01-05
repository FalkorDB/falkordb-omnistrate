"""
Unit tests for instance type and resource mapping.
"""

import pytest
from ...utils.rendering import find_manifest_by_kind, get_cluster_component_spec
from ...utils.validation import validate_resource_mapping


class TestInstanceTypes:
    """Test instance type to resource mapping."""

    @pytest.mark.parametrize("instance_type", [
        "e2-medium", "e2-standard-2", "e2-standard-4", "e2-custom-4-8192",
        "e2-custom-8-16384", "e2-custom-16-32768", "e2-custom-32-65536",
        "t2.medium", "m6i.large", "m6i.xlarge", "c6i.xlarge", "c6i.2xlarge",
        "c6i.4xlarge", "c6i.8xlarge"
    ])
    def test_instance_type_mapping(self, helm_render, standalone_values, instance_type_mappings, instance_type):
        """Test that each instance type maps to correct CPU and memory."""
        values = {
            **standalone_values,
            "instanceType": instance_type
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        
        expected = instance_type_mappings[instance_type]
        errors = validate_resource_mapping(
            component, 
            expected["cpu"], 
            expected["memory"]
        )
        assert not errors, f"Resource mapping failed for {instance_type}: {errors}"

    def test_fallback_to_cpu_memory_when_no_instance_type(self, helm_render, standalone_values):
        """Test fallback to explicit cpu/memory when instanceType is not provided."""
        values = {
            **standalone_values,
            "instanceType": "",
            "cpu": "2",
            "memory": "8"
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        
        errors = validate_resource_mapping(component, "2", "8")
        assert not errors, f"Fallback resource mapping failed: {errors}"

    def test_resource_requests_match_limits(self, helm_render, standalone_values):
        """Test that resource requests match limits."""
        values = {
            **standalone_values,
            "instanceType": "m6i.xlarge"
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        
        resources = component.get("resources", {})
        limits = resources.get("limits", {})
        requests = resources.get("requests", {})
        
        assert limits.get("cpu") == requests.get("cpu"), "CPU requests should match limits"
        assert limits.get("memory") == requests.get("memory"), "Memory requests should match limits"

    def test_custom_resource_requests(self, helm_render, standalone_values):
        """Test that requests are set to the same values as limits (no custom requests supported)."""
        values = {
            **standalone_values,
            "instanceType": "m6i.xlarge",
            "requests": {
                "cpu": "2",
                "memory": "8Gi"
            }
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        
        resources = component.get("resources", {})
        limits = resources.get("limits", {})
        requests = resources.get("requests", {})
        
        # Both limits and requests should be from instance type (no custom requests supported)
        assert limits.get("cpu") == "3900m"
        assert limits.get("memory") == "13900Mi"
        
        # Requests should match limits (custom requests are not supported)
        assert requests.get("cpu") == "3900m"
        assert requests.get("memory") == "13900Mi"