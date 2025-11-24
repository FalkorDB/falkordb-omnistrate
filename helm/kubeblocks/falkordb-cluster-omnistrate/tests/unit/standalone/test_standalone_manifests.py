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
        """Test user creation Job for standalone mode."""
        values = {
            **standalone_values,
            "falkordbUser": user_config_sample
        }
        manifests = helm_render(values)
        
        job = find_manifest_by_kind(manifests, "Job")
        assert job is not None, "User creation Job not found"
        
        errors = validate_job_configuration(job, user_config_sample["username"])
        assert not errors, f"Job validation failed: {errors}"

    def test_standalone_no_job_when_no_user_config(self, helm_render, standalone_values):
        """Test that no Job is created when user config is empty."""
        values = {
            **standalone_values,
            "falkordbUser": {
                "username": "",
                "password": ""
            }
        }
        manifests = helm_render(values)
        
        job = find_manifest_by_kind(manifests, "Job")
        assert job is None, "Job should not be created when user config is empty"

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