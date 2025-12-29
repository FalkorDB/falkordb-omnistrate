"""Unit tests for enableTLS flag in Helm chart."""

import pytest

def test_enable_tls_true(helm_render, base_values):
    """Test that enableTLS=true is rendered correctly in templates."""
    values = base_values.copy()
    values["enableTLS"] = True
    manifests = helm_render(values)
    # Check that enableTLS is set in the rendered manifests (example: look for a secret or annotation)
    found = any(
        ("tls" in m.get("metadata", {}).get("name", "") or "tls" in str(m))
        for m in manifests
    )
    assert found, "TLS-related resources should be present when enableTLS is true."

def test_enable_tls_false(helm_render, base_values):
    """Test that enableTLS=false does not render TLS resources."""
    values = base_values.copy()
    values["enableTLS"] = False
    manifests = helm_render(values)
    # Check that no TLS-related resources are present
    found = any(
        ("tls" in m.get("metadata", {}).get("name", "") or "tls" in str(m))
        for m in manifests
    )
    assert not found, "No TLS-related resources should be present when enableTLS is false."
