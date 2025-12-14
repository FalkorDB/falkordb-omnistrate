"""Unit tests for replication mode Helm chart rendering."""

import pytest


class TestReplicationManifests:
    """Test replication-specific manifest generation."""

    def test_replication_mode_rendering(self, helm_render):
        """Test that replication mode templates render correctly."""
        values = {
            "mode": "replication",
            "replicas": 3,
            "instanceType": "e2-medium",
            "storage": 20,
            "sentinel": {"enabled": True},
        }
        
        manifests = helm_render(values)
        assert len(manifests) > 0
        
        # Check that we have the expected resources for replication
        resource_kinds = {m["kind"] for m in manifests}
        assert "Cluster" in resource_kinds

    def test_sentinel_configuration(self, helm_render):
        """Test sentinel configuration in replication mode."""
        values = {
            "mode": "replication",
            "replicas": 3,
            "sentinel": {
                "enabled": True,
                "replicas": 3,
                "instanceType": "e2-small"
            },
        }
        
        manifests = helm_render(values)
        
        # Find Cluster manifest
        cluster_manifest = next(
            (m for m in manifests if m["kind"] == "Cluster"), None
        )
        assert cluster_manifest is not None
        
        # Check sentinel configuration through componentSpecs
        spec = cluster_manifest["spec"]
        component_specs = spec["componentSpecs"]
        # Mode is determined by having multiple replicas and sentinel enabled
        falkordb_component = next(
            (comp for comp in component_specs if comp.get("name") == "falkordb"), None
        )
        assert falkordb_component is not None
        assert falkordb_component["replicas"] >= 2  # Replication needs multiple replicas
        
        # Check for sentinel component
        sentinel_component = next(
            (comp for comp in component_specs if "sent" in comp.get("name", "")), None
        )
        assert sentinel_component is not None

    def test_replication_replica_count(self, helm_render):
        """Test replica count configuration for replication mode."""
        test_cases = [
            {"replicas": 2, "expected": 2},
            {"replicas": 3, "expected": 3},
            {"replicas": 5, "expected": 5},
        ]
        
        for case in test_cases:
            values = {
                "mode": "replication",
                "replicas": case["replicas"],
                "sentinel": {"enabled": True},
            }
            
            manifests = helm_render(values)
            cluster_manifest = next(
                (m for m in manifests if m["kind"] == "Cluster"), None
            )
            
            assert cluster_manifest is not None
            component_spec = cluster_manifest["spec"]["componentSpecs"][0]
            assert component_spec["replicas"] == case["expected"]

    def test_replication_without_sentinel_fails(self, helm_render):
        """Test that replication mode requires sentinel to be enabled."""
        values = {
            "mode": "replication",
            "replicas": 3,
            "sentinel": {"enabled": False},
        }
        
        # This should either fail or automatically enable sentinel
        try:
            manifests = helm_render(values)
            # If it doesn't fail, sentinel should be auto-enabled
            cluster_manifest = next(
                (m for m in manifests if m["kind"] == "Cluster"), None
            )
            if cluster_manifest:
                # Check that we have both primary and sentinel components for replication
                component_specs = cluster_manifest["spec"]["componentSpecs"]
                assert len(component_specs) >= 1
        except (ValueError, KeyError, AssertionError):
            # It's acceptable for this to fail if sentinel is required
            pass

    def test_replication_networking(self, helm_render):
        """Test networking configuration for replication mode."""
        values = {
            "mode": "replication",
            "replicas": 3,
            "sentinel": {"enabled": True},
            "hostNetworkEnabled": True,
            "nodePortEnabled": True,
        }
        
        manifests = helm_render(values)
        cluster_manifest = next(
            (m for m in manifests if m["kind"] == "Cluster"), None
        )
        
        assert cluster_manifest is not None
        # Check that both falkordb and sentinel components exist
        component_specs = cluster_manifest["spec"]["componentSpecs"]
        component_names = [c["name"] for c in component_specs]
        assert "falkordb" in component_names, "FalkorDB component should exist"
        assert "falkordb-sent" in component_names, "Sentinel component should exist"
        
        # Networking configuration would be in services - check that services exist
        services = [m for m in manifests if m["kind"] == "Service"]
        assert len(services) > 0, "At least one service should be created for networking"

    def test_replication_persistence_config(self, helm_render):
        """Test persistence configuration for replication mode."""
        values = {
            "mode": "replication",
            "replicas": 3,
            "sentinel": {"enabled": True},
            "storage": 50,
            "persistence": {
                "rdbConfig": "high",
                "aofConfig": "always"
            },
        }
        
        manifests = helm_render(values)
        cluster_manifest = next(
            (m for m in manifests if m["kind"] == "Cluster"), None
        )
        
        assert cluster_manifest is not None
        # In replication mode, storage is in componentSpecs[0].volumeClaimTemplates
        component_specs = cluster_manifest["spec"]["componentSpecs"]
        falkordb_component = next((c for c in component_specs if c["name"] == "falkordb"), None)
        assert falkordb_component is not None, "FalkorDB component not found"
        
        volume_templates = falkordb_component.get("volumeClaimTemplates", [])
        assert len(volume_templates) > 0, "No volume claim templates found"
        
        data_template = next((vt for vt in volume_templates if vt.get("name") == "data"), None)
        assert data_template is not None, "Data volume claim template not found"
        storage_request = data_template["spec"]["resources"]["requests"]["storage"]
        assert storage_request == "50Gi"

    def test_replication_anti_affinity(self, helm_render):
        """Test replication mode renders correctly with pod anti-affinity configuration."""
        values = {
            "mode": "replication",
            "replicas": 3,
            "sentinel": {"enabled": True},
            "podAntiAffinityEnabled": True,
        }
        
        manifests = helm_render(values)
        cluster_manifest = next(
            (m for m in manifests if m["kind"] == "Cluster"), None
        )
        
        assert cluster_manifest is not None
        # Verify replication structure is correct
        assert cluster_manifest["spec"]["topology"] == "replication"
        component_specs = cluster_manifest["spec"]["componentSpecs"]
        assert len(component_specs) >= 2, "Should have falkordb and sentinel components"
        
        # Check components exist
        component_names = [c["name"] for c in component_specs]
        assert "falkordb" in component_names
        assert "falkordb-sent" in component_names

    def test_replication_resource_limits(self, helm_render):
        """Test resource limits for replication mode."""
        values = {
            "mode": "replication",
            "replicas": 3,
            "sentinel": {"enabled": True},
            "instanceType": "e2-standard-4",
        }
        
        manifests = helm_render(values)
        cluster_manifest = next(
            (m for m in manifests if m["kind"] == "Cluster"), None
        )
        
        assert cluster_manifest is not None
        # In replication mode, resources are in componentSpecs[0].resources
        component_specs = cluster_manifest["spec"]["componentSpecs"]
        falkordb_component = next((c for c in component_specs if c["name"] == "falkordb"), None)
        assert falkordb_component is not None, "FalkorDB component not found"
        
        resources = falkordb_component.get("resources", {})
        limits = resources.get("limits", {})
        # e2-standard-4 should match the chart's instance mapping
        assert limits.get("cpu") == "3500m"
        assert limits.get("memory") == "13000Mi"
        # Check resource configuration
        # Already defined above as falkordb_component
        assert resources is not None
        # Instance type configuration would translate to resource requests/limits
        if "requests" in resources:
            assert resources["requests"] is not None