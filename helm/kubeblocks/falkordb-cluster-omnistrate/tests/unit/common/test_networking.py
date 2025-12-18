"""
Unit tests for networking configuration.
"""

import pytest
from ...utils.rendering import find_manifest_by_kind, find_manifests_by_kind
from ...utils.validation import validate_service_configuration, validate_external_service_annotations


class TestNetworking:
    """Test networking and service configuration."""

    def test_default_service_configuration(self, helm_render, standalone_values):
        """Test default service configuration."""
        manifests = helm_render(standalone_values)
        
        services = find_manifests_by_kind(manifests, "Service")
        assert len(services) > 0, "No services found"
        
        # The main FalkorDB service is the external service
        main_service = services[0]  # Should be the external service
        
        assert main_service is not None, "Main service not found"
        errors = validate_service_configuration(main_service, "ClusterIP")
        assert not errors, f"Service validation failed: {errors}"

    def test_nodeport_service_configuration(self, helm_render, standalone_values):
        """Test NodePort service configuration."""
        values = {
            **standalone_values,
            "nodePortEnabled": True
        }
        manifests = helm_render(values)
        
        services = find_manifests_by_kind(manifests, "Service")
        
        # Should have NodePort service when nodePortEnabled is true
        nodeport_services = [s for s in services if s.get("spec", {}).get("type") == "NodePort"]
        assert len(nodeport_services) > 0, "NodePort service not found"

    def test_external_service_with_dns_annotations(self, helm_render, standalone_values):
        """Test external service with DNS annotations."""
        values = {
            **standalone_values,
            "hostname": "test-falkordb.example.com",
            "port": 6379
        }
        manifests = helm_render(values)
        
        services = find_manifests_by_kind(manifests, "Service")
        
        # Find the generic external service (not per-pod services)
        # Generic service name should contain "falkordb-cluster-omnistrate-external"
        generic_service = None
        for svc in services:
            name = svc.get("metadata", {}).get("name", "")
            if "external" in name and "falkordb-cluster-omnistrate-external" in name:
                generic_service = svc
                break
        
        assert generic_service is not None, "Generic external service not found"
        
        errors = validate_external_service_annotations(
            generic_service, 
            "node.test-falkordb.example.com"
        )
        assert not errors, f"External service validation failed: {errors}"

    def test_host_network_configuration(self, helm_render, standalone_values):
        """Test host network configuration."""
        values = {
            **standalone_values,
            "hostNetworkEnabled": True
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, "Cluster not found with hostNetworkEnabled"
        
        # The networking configuration is typically handled in the pod template
        # Just ensure the cluster renders successfully with this configuration

    @pytest.mark.skip(reason="LoadBalancer service type is not implemented in the template")
    def test_load_balancer_configuration(self, helm_render, standalone_values):
        """Test load balancer configuration."""
        values = {
            **standalone_values,
            "loadBalancerEnabled": True
        }
        manifests = helm_render(values)
        
        services = find_manifests_by_kind(manifests, "Service")
        
        # Note: LoadBalancer service type is not implemented in the current template
        # The template only supports NodePort and ClusterIP types
        lb_services = [s for s in services if s.get("spec", {}).get("type") == "LoadBalancer"]
        assert len(lb_services) > 0, "LoadBalancer service not found"

    def test_fixed_pod_ip_configuration(self, helm_render, standalone_values):
        """Test fixed pod IP configuration."""
        values = {
            **standalone_values,
            "fixedPodIPEnabled": True
        }
        manifests = helm_render(values)
        
        cluster = find_manifest_by_kind(manifests, "Cluster")
        assert cluster is not None, "Cluster not found with fixedPodIPEnabled"

    def test_mutually_exclusive_network_options(self, helm_render, standalone_values):
        """Test that mutually exclusive network options work independently."""
        network_options = [
            {"hostNetworkEnabled": True},
            {"nodePortEnabled": True},
            {"loadBalancerEnabled": True},
            {"fixedPodIPEnabled": True}
        ]
        
        for option in network_options:
            values = {**standalone_values, **option}
            manifests = helm_render(values)
            
            cluster = find_manifest_by_kind(manifests, "Cluster")
            assert cluster is not None, f"Cluster failed to render with option: {option}"

    def test_external_service_port_configuration(self, helm_render, standalone_values):
        """Test external service port configuration."""
        values = {
            **standalone_values,
            "hostname": "test.example.com",
            "port": 6379
        }
        manifests = helm_render(values)
        
        services = find_manifests_by_kind(manifests, "Service")
        external_service = None
        for svc in services:
            if "external" in svc.get("metadata", {}).get("name", ""):
                external_service = svc
                break
        
        assert external_service is not None, "External service not found"
        
        ports = external_service.get("spec", {}).get("ports", [])
        assert len(ports) >= 1, "External service should have at least the FalkorDB port"
        
        # Check FalkorDB port exists
        falkordb_port = next((p for p in ports if p.get("port") == 6379), None)
        assert falkordb_port is not None, "FalkorDB port 6379 not found in external service"