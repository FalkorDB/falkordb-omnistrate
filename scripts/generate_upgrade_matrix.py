"""
Generate the GitHub Actions test matrix for upgrade tests.

Reads tier version inputs from environment variables and writes a JSON matrix
to GITHUB_OUTPUT (or prints to stdout when run locally).

Environment variables
---------------------
FREE_NEW, FREE_OLD           : free tier new/old version(s)
STARTUP_NEW, STARTUP_OLD     : startup tier new/old version(s)
PRO_NEW, PRO_OLD             : pro tier new/old version(s)
ENTERPRISE_NEW, ENTERPRISE_OLD: enterprise tier new/old version(s)
GITHUB_RUN_ID                : injected by GitHub Actions; appended to instance
                               names so concurrent workflow runs don't collide.
"""

import json
import os
import sys


def versions(old_str: str) -> list[str]:
    """Split a comma-separated version string into a list, ignoring blanks."""
    return [v.strip() for v in old_str.split(",") if v.strip()] if old_str.strip() else []


tier_versions = {
    "FalkorDB Free": (
        os.getenv("FREE_NEW", ""),
        versions(os.getenv("FREE_OLD", "")),
    ),
    "FalkorDB Startup": (
        os.getenv("STARTUP_NEW", ""),
        versions(os.getenv("STARTUP_OLD", "")),
    ),
    "FalkorDB Pro": (
        os.getenv("PRO_NEW", ""),
        versions(os.getenv("PRO_OLD", "")),
    ),
    "FalkorDB Enterprise": (
        os.getenv("ENTERPRISE_NEW", ""),
        versions(os.getenv("ENTERPRISE_OLD", "")),
    ),
}

run_id = os.getenv("GITHUB_RUN_ID", "local")

# fmt: off
# (ref_name, resource_key, instance_name, instance_type, host_count, cluster_replicas, custom_network, tls)
tiers = [
    # Free – only one resource key (no TLS)
    ("FalkorDB Free",       "free",                "free-upgradeTest",                   "t2.medium", "",  "",  "",                False),
    # Startup – only standalone
    ("FalkorDB Startup",    "standalone",          "startup-upgradeTest",                "t2.medium", "",  "",  "",                True),
    # Pro – standalone, single-Zone, multi-Zone (m6i.large, no custom network)
    ("FalkorDB Pro",        "standalone",          "pro-standalone-upgradeTest",         "m6i.large", "",  "",  "",                True),
    ("FalkorDB Pro",        "single-Zone",         "pro-single-zone-upgradeTest",        "m6i.large", "",  "",  "",                True),
    ("FalkorDB Pro",        "multi-Zone",          "pro-multi-zone-upgradeTest",         "m6i.large", "",  "",  "",                True),
    # Enterprise – standalone, single-Zone, multi-Zone, cluster variants (t2.medium, custom network)
    ("FalkorDB Enterprise", "standalone",          "enterprise-standalone-upgradeTest",  "t2.medium", "",  "",  "aws-network-main", True),
    ("FalkorDB Enterprise", "single-Zone",         "enterprise-single-zone-upgradeTest", "t2.medium", "",  "",  "aws-network-main", True),
    ("FalkorDB Enterprise", "multi-Zone",          "enterprise-multi-zone-upgradeTest",  "t2.medium", "",  "",  "aws-network-main", True),
    ("FalkorDB Enterprise", "cluster-Single-Zone", "enterprise-cluster-sz-upgradeTest",  "t2.medium", "6", "1", "aws-network-main", True),
    ("FalkorDB Enterprise", "cluster-Multi-Zone",  "enterprise-cluster-mz-upgradeTest",  "t2.medium", "6", "1", "aws-network-main", True),
]
# fmt: on

entries = []
for ref_name, rkey, iname, itype, hcount, creplicas, cnet, tls in tiers:
    new_version, old_version_list = tier_versions[ref_name]
    if not new_version or not old_version_list:
        continue
    for old_version in old_version_list:
        # Append sanitized old version + run ID to avoid collisions between
        # parallel matrix jobs AND concurrent workflow runs (e.g. "58.0" → "58-0-12345678")
        versioned_iname = f"{iname}-{old_version.replace('.', '-')}-{run_id}"
        entries.append(
            {
                "name": f"{ref_name}/{rkey} — {old_version} → {new_version}",
                "old_version": old_version,
                "new_version": new_version,
                "ref_name": ref_name,
                "resource_key": rkey,
                "instance_name": versioned_iname,
                "instance_type": itype,
                "host_count": hcount,
                "cluster_replicas": creplicas,
                "custom_network": cnet,
                "tls": tls,
            }
        )

if not entries:
    print(
        "No test matrix entries generated — check that at least one tier has both "
        "new and old versions set.",
        file=sys.stderr,
    )
    sys.exit(1)

matrix = json.dumps({"include": entries})
github_output = os.environ.get("GITHUB_OUTPUT")
if github_output:
    with open(github_output, "a") as fh:
        fh.write(f"matrix={matrix}\n")
else:
    print(matrix)
