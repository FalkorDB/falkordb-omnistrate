"""
Unit tests for basic cluster rendering that apply to all deployment modes.
"""

import pytest
from ...utils.rendering import find_manifest_by_kind, get_cluster_component_spec
from ...utils.validation import validate_basic_cluster_properties


class TestBasicRendering:
    """Test basic cluster rendering common to all deployment modes."""

    @pytest.mark.parametrize("mode,values_fixture", [
        ("standalone", "standalone_values"),
        ("replication", "replication_values"), 
        ("cluster", "cluster_values")
    ])
    def test_cluster_manifest_exists(self, helm_render, request, mode, values_fixture):
        """Test that Cluster manifest is rendered for all modes."""
        values = request.getfixturevalue(values_fixture)
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, f"Cluster manifest not found for {mode} mode"
        
        errors = validate_basic_cluster_properties(cluster, mode)
        assert not errors, f"Cluster validation failed for {mode}: {errors}"

    @pytest.mark.parametrize("mode,values_fixture", [
        ("standalone", "standalone_values"),
        ("replication", "replication_values"),
        ("cluster", "cluster_values")
    ])
    def test_cluster_basic_structure(self, helm_render, request, mode, values_fixture):
        """Test basic cluster structure for all modes."""
        values = request.getfixturevalue(values_fixture)
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster["apiVersion"] == "apps.kubeblocks.io/v1"
        assert cluster["spec"]["topology"] == mode
        assert cluster["spec"]["terminationPolicy"] == "Delete"

    def test_service_manifest_exists(self, helm_render, standalone_values):
        """Test that Service manifest is rendered."""
        manifests = helm_render(standalone_values)
        
        # Should have at least one service
        services = [m for m in manifests if m.get("kind") == "Service"]
        assert len(services) > 0, "No Service manifest found"

    @pytest.mark.skip(reason="User creation now happens at startup via environment variables, not via Job")
    def test_rbac_resources_for_job(self, helm_render, user_config_sample):
        """Test that RBAC resources are created when user config is provided."""
        values = {
            "mode": "standalone",
            "replicas": 1,
            "falkordbUser": user_config_sample
        }
        manifests = helm_render(values)
        
        # User creation is now handled at startup via environment variables,
        # so RBAC resources for Job are no longer needed
        resource_kinds = {m.get("kind") for m in manifests}
        assert "ServiceAccount" in resource_kinds, "ServiceAccount not found"
        assert "Role" in resource_kinds, "Role not found"
        assert "RoleBinding" in resource_kinds, "RoleBinding not found"