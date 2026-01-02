"""
Unit tests specific to standalone deployment mode.
"""

import pytest
from ...utils.rendering import (
    find_manifest_by_kind, 
    get_cluster_component_spec, 
    get_environment_variables
)
from ...utils.validation import (
    validate_falkordb_args, 
    validate_job_configuration,
    validate_replicas_configuration
)


class TestStandaloneManifests:
    """Test Helm chart manifest generation for standalone mode."""

    def test_standalone_cluster_topology(self, helm_render, standalone_values):
        """Test standalone cluster has correct topology."""
        manifests = helm_render(standalone_values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster["spec"]["topology"] == "standalone"

    def test_standalone_replicas_configuration(self, helm_render, standalone_values):
        """Test standalone mode has exactly 1 replica."""
        manifests = helm_render(standalone_values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        errors = validate_replicas_configuration(cluster, 1)
        assert not errors, f"Replicas validation failed: {errors}"

    def test_standalone_sentinel_disabled(self, helm_render, standalone_values):
        """Test that sentinel is disabled in standalone mode."""
        manifests = helm_render(standalone_values)
        
        # Should not have sentinel component
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component_specs = cluster.get("spec", {}).get("componentSpecs", [])
        
        sentinel_components = [c for c in component_specs if "sentinel" in c.get("name", "").lower()]
        assert len(sentinel_components) == 0, "Sentinel component should not exist in standalone mode"

    def test_standalone_falkordb_args_generation(self, helm_render, standalone_values, falkordb_config_sample):
        """Test FALKORDB_ARGS environment variable generation for standalone."""
        values = {
            **standalone_values,
            "falkordbConfig": falkordb_config_sample
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        env_vars = get_environment_variables(component)
        
        expected_config = {
            "CACHE_SIZE": "50",
            "NODE_CREATION_BUFFER": "32768",
            "MAX_QUEUED_QUERIES": "100",
            "TIMEOUT_MAX": "1000",
            "TIMEOUT_DEFAULT": "500",
            "RESULTSET_SIZE": "20000",
            "QUERY_MEM_CAPACITY": "1000000"
        }
        
        errors = validate_falkordb_args(env_vars, expected_config)
        assert not errors, f"FALKORDB_ARGS validation failed: {errors}"

    def test_standalone_user_creation_job(self, helm_render, standalone_values, user_config_sample):
        """Test that user creation happens at startup via environment variables, not via Job."""
        values = {
            **standalone_values,
            "falkordbUser": user_config_sample
        }
        manifests = helm_render(values)
        
        # User creation now happens at startup via environment variables
        # Verify no Job is created for user creation anymore
        job = find_manifest_by_kind(manifests, "Job")
        assert job is None, "User creation Job should not be created (moved to startup scripts)"
        
        # Verify cluster manifest exists (user creation is handled by startup scripts)
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, "Cluster manifest not found"

    def test_standalone_no_job_when_no_user_config(self, helm_render, standalone_values):
        """Test that no Job is created (user creation now happens at startup)."""
        values = {
            **standalone_values,
            "falkordbUser": {
                "username": "",
                "password": ""
            }
        }
        manifests = helm_render(values)
        
        # User creation jobs are no longer created - this functionality moved to startup scripts
        job = find_manifest_by_kind(manifests, "Job")
        assert job is None, "Job should not be created (user creation moved to startup scripts)"

    def test_standalone_custom_secret_configuration(self, helm_render, standalone_values):
        """Test standalone with custom secret configuration."""
        values = {
            **standalone_values,
            "customSecretName": "my-custom-secret",
            "customSecretNamespace": "my-namespace"
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, "Cluster should render with custom secret config"

    def test_standalone_termination_policy(self, helm_render, standalone_values):
        """Test termination policy configuration."""
        test_policies = ["Delete", "WipeOut", "DoNotTerminate"]
        
        for policy in test_policies:
            values = {
                **standalone_values,
                "extra": {
                    "terminationPolicy": policy
                }
            }
            manifests = helm_render(values)
            
            cluster = find_manifest_by_kind(manifests, "Cluster")
            assert cluster["spec"]["terminationPolicy"] == policy, \
                f"Expected terminationPolicy '{policy}', got '{cluster['spec']['terminationPolicy']}'"

    def test_standalone_with_pod_anti_affinity(self, helm_render, standalone_values):
        """Test standalone with pod anti-affinity enabled."""
        values = {
            **standalone_values,
            "podAntiAffinityEnabled": True
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, "Cluster should render with pod anti-affinity"
        
        # Pod anti-affinity configuration would be in the component spec
        component = get_cluster_component_spec(cluster)
        assert component is not None, "Component spec not found"

    def test_standalone_with_exporter_disabled(self, helm_render, standalone_values):
        """Test standalone with exporter disabled."""
        values = {
            **standalone_values,
            "extra": {
                "disableExporter": True
            }
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, "Cluster should render with exporter disabled"

    def test_standalone_minimal_configuration(self, helm_render):
        """Test standalone with minimal required configuration."""
        minimal_values = {
            "mode": "standalone",
            "replicas": 1
        }
        manifests = helm_render(minimal_values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, "Cluster should render with minimal config"
        assert cluster["spec"]["topology"] == "standalone"

    def test_standalone_component_name_consistency(self, helm_render, standalone_values):
        """Test that component names are consistent across manifests."""
        manifests = helm_render(standalone_values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component_specs = cluster.get("spec", {}).get("componentSpecs", [])
        
        assert len(component_specs) > 0, "No component specs found"
        
        # Main component should be named 'falkordb'
        main_component = component_specs[0]
        assert main_component.get("name") == "falkordb", \
            f"Expected component name 'falkordb', got '{main_component.get('name')}'"

    def test_standalone_announce_hostname_override_env(self, helm_render, standalone_values):
        """Ensure ANNOUNCE_HOSTNAME_OVERRIDE is rendered with pod name for standalone."""
        values = {
            **standalone_values,
            "hostname": "node.cluster.local",
        }

        manifests = helm_render(values)
        cluster = find_manifest_by_kind(manifests, "Cluster")
        component = get_cluster_component_spec(cluster)
        env_vars = component.get("env", [])

        pod_env = next((e for e in env_vars if e.get("name") == "POD_NAME"), None)
        assert pod_env is not None
        assert pod_env.get("valueFrom", {}).get("fieldRef", {}).get("fieldPath") == "metadata.name"

        announce_env = next((e for e in env_vars if e.get("name") == "ANNOUNCE_HOSTNAME_OVERRIDE"), None)
        assert announce_env is not None
        assert announce_env.get("value") == "$(POD_NAME).node.cluster.local"