"""
Unit tests for persistence configuration.
"""

import pytest
from ...utils.rendering import find_manifest_by_kind, get_cluster_component_spec
from ...utils.validation import validate_storage_configuration


class TestPersistence:
    """Test persistence and storage configuration."""

    @pytest.mark.parametrize("storage_size", [10, 20, 50, 100, 500, 1000])
    def test_storage_size_mapping(self, helm_render, standalone_values, storage_size):
        """Test that storage size is correctly mapped to PVC."""
        values = {
            **standalone_values,
            "storage": storage_size
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        
        errors = validate_storage_configuration(component, f"{storage_size}Gi")
        assert not errors, f"Storage validation failed for {storage_size}Gi: {errors}"

    def test_volume_claim_template_structure(self, helm_render, standalone_values):
        """Test volume claim template has correct structure."""
        manifests = helm_render(standalone_values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        
        volume_templates = component.get("volumeClaimTemplates", [])
        assert len(volume_templates) > 0, "No volume claim templates found"
        
        data_template = None
        for template in volume_templates:
            if template.get("name") == "data":
                data_template = template
                break
        
        assert data_template is not None, "Data volume claim template not found"
        assert data_template["spec"]["accessModes"] == ["ReadWriteOnce"]

    @pytest.mark.parametrize("rdb_config,aof_config", [
        ("low", "no"),
        ("medium", "everysec"),
        ("high", "always")
    ])
    def test_persistence_configurations(self, helm_render, standalone_values, rdb_config, aof_config):
        """Test different persistence configurations render without errors."""
        values = {
            **standalone_values,
            "persistence": {
                "rdbConfig": rdb_config,
                "aofConfig": aof_config
            }
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, f"Failed to render with persistence config: rdb={rdb_config}, aof={aof_config}"

    def test_storage_class_when_specified(self, helm_render, standalone_values):
        """Test storage class is applied when specified."""
        values = {
            **standalone_values,
            "storageClassName": "fast-ssd"
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        
        volume_templates = component.get("volumeClaimTemplates", [])
        data_template = next(
            (t for t in volume_templates if t.get("metadata", {}).get("name") == "data"), 
            None
        )
        
        if data_template:
            storage_class = data_template.get("spec", {}).get("storageClassName")
            assert storage_class == "fast-ssd", f"Expected storageClassName 'fast-ssd', got '{storage_class}'"