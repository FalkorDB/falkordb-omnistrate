"""
Automatically generate the GitHub Actions test matrix for upgrade tests.

Instead of requiring manual version inputs, this script:
1. Calls the Omnistrate API to discover tier versions:
   - new_version  = the PREFERRED tier version (latest released)
   - old_versions = all ACTIVE (non-preferred) versions that have at least one
                    RUNNING instance on a resource_key that we test
2. For Pro and Enterprise tiers, filters resource_keys by whether any RUNNING
   customer instance actually uses that resource_key+version combination.
   Free and Startup only have one resource_key each so no filtering is needed.
3. Writes the resulting matrix JSON to GITHUB_OUTPUT (or prints to stdout locally).

Required environment variables
-------------------------------
OMNISTRATE_USERNAME   : Omnistrate account e-mail
OMNISTRATE_PASSWORD   : Omnistrate account password
OMNISTRATE_SERVICE_ID : Service ID (falls back to OMNISTRATE_INTERNAL_SERVICE_ID)
OMNISTRATE_ENV_ID     : Environment ID (falls back to OMNISTRATE_INTERNAL_PROD_ENVIRONMENT)
GITHUB_RUN_ID         : Injected by GitHub Actions; used in instance names to prevent
                        cross-run collisions.
"""

import json
import logging
import os
import sys

import requests
from requests.adapters import HTTPAdapter, Retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("OMNISTRATE_BASE_URL", "https://api.omnistrate.cloud/2022-09-01-00")
USERNAME = os.getenv("OMNISTRATE_USERNAME", "")
PASSWORD = os.getenv("OMNISTRATE_PASSWORD", "")
SERVICE_ID = os.getenv("OMNISTRATE_SERVICE_ID") or os.getenv("OMNISTRATE_INTERNAL_SERVICE_ID", "")
ENV_ID = os.getenv("OMNISTRATE_ENV_ID") or os.getenv("OMNISTRATE_INTERNAL_PROD_ENVIRONMENT", "")
RUN_ID = os.getenv("GITHUB_RUN_ID", "local")

# Optional TLS override: if set, applies to all tiers instead of per-tier default.
_tls_env = os.getenv("TLS", "").strip().lower()
TLS_OVERRIDE = True if _tls_env == "true" else (False if _tls_env == "false" else None)

# ---------------------------------------------------------------------------
# Per-tier static config
# (ref_name, resource_key, instance_name_prefix, instance_type,
#  host_count, cluster_replicas, custom_network, tls)
# ---------------------------------------------------------------------------
TIER_CONFIG = {
    "FalkorDB Free": [
        ("free",  "free-upgradeTest",  "t2.medium", "", "",  "",                False),
    ],
    "FalkorDB Startup": [
        ("standalone", "startup-upgradeTest", "t2.medium", "", "",  "",                True),
    ],
    "FalkorDB Pro": [
        ("standalone",  "pro-standalone-upgradeTest",  "m6i.large", "", "",  "",  True),
        ("single-Zone", "pro-single-zone-upgradeTest",  "m6i.large", "", "",  "",  True),
        ("multi-Zone",  "pro-multi-zone-upgradeTest",   "m6i.large", "", "",  "",  True),
    ],
    "FalkorDB Enterprise": [
        ("standalone",          "enterprise-standalone-upgradeTest",  "t2.medium", "",  "",  "aws-network-main", True),
        ("single-Zone",         "enterprise-single-zone-upgradeTest", "t2.medium", "",  "",  "aws-network-main", True),
        ("multi-Zone",          "enterprise-multi-zone-upgradeTest",  "t2.medium", "",  "",  "aws-network-main", True),
        ("cluster-Single-Zone", "enterprise-cluster-sz-upgradeTest",  "t2.medium", "6", "1", "aws-network-main", True),
        ("cluster-Multi-Zone",  "enterprise-cluster-mz-upgradeTest",  "t2.medium", "6", "1", "aws-network-main", True),
    ],
}

# Tiers where we always test all resource_keys regardless of active instances
ALWAYS_TEST_ALL_KEYS = {"FalkorDB Free", "FalkorDB Startup"}


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

def _make_session(token: str) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    return session


def get_token() -> str:
    response = requests.post(
        f"{BASE_URL}/signin",
        json={"email": USERNAME, "password": PASSWORD},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["jwtToken"]


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def get_product_tiers(session: requests.Session) -> dict[str, dict]:
    """Return {tier_name: {productTierId, productTierKey, ...}} for the service."""
    r = session.get(
        f"{BASE_URL}/service/{SERVICE_ID}/environment/{ENV_ID}/service-plan",
        timeout=60,
    )
    r.raise_for_status()
    return {t["productTierName"]: t for t in r.json().get("servicePlans", [])}


def get_tier_versions(session: requests.Session, tier_id: str) -> list[dict]:
    """Return raw tierVersionSets list for a product tier."""
    r = session.get(
        f"{BASE_URL}/service/{SERVICE_ID}/productTier/{tier_id}/version-set",
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("tierVersionSets", [])


def list_instances(session: requests.Session) -> list[dict]:
    """Return all fleet instances for the service+environment."""
    r = session.get(
        f"{BASE_URL}/fleet/service/{SERVICE_ID}/environment/{ENV_ID}/instances",
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("resourceInstances", [])


# ---------------------------------------------------------------------------
# Logic helpers
# ---------------------------------------------------------------------------

def resource_keys_with_running_instances(
    instances: list[dict], tier_name: str, old_version: str
) -> set[str]:
    """
    Return the set of resource_keys that have at least one RUNNING instance
    for the given tier_name + old_version combination.

    The instance list item shape:
      {
        "productTierName": "FalkorDB Pro",
        "productTierVersion": "73.0",          # version the instance is on
        "consumptionResourceInstanceResult": {
          "status": "RUNNING",
          "detailedNetworkTopology": {
            "<resourceId>": {"resourceKey": "multi-Zone", ...},
            ...
          }
        }
      }
    """
    found: set[str] = set()
    for inst in instances:
        if inst.get("productTierName") != tier_name:
            continue
        inst_version = inst.get("productTierVersion", "")
        if inst_version != old_version:
            continue
        result = inst.get("consumptionResourceInstanceResult", {})
        if result.get("status") != "RUNNING":
            continue
        topology = result.get("detailedNetworkTopology", {})
        for resource_data in topology.values():
            rk = resource_data.get("resourceKey")
            if rk:
                found.add(rk)
    return found


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_matrix() -> list[dict]:
    if not USERNAME or not PASSWORD:
        log.error("OMNISTRATE_USERNAME and OMNISTRATE_PASSWORD must be set")
        sys.exit(1)
    if not SERVICE_ID:
        log.error("OMNISTRATE_SERVICE_ID or OMNISTRATE_INTERNAL_SERVICE_ID must be set")
        sys.exit(1)
    if not ENV_ID:
        log.error("OMNISTRATE_ENV_ID or OMNISTRATE_INTERNAL_PROD_ENVIRONMENT must be set")
        sys.exit(1)

    log.info("Authenticating with Omnistrate API...")
    token = get_token()
    session = _make_session(token)

    log.info("Fetching product tiers...")
    tiers = get_product_tiers(session)

    log.info("Fetching all fleet instances...")
    all_instances = list_instances(session)

    entries: list[dict] = []

    for tier_name, resource_configs in TIER_CONFIG.items():
        if tier_name not in tiers:
            log.warning(f"Tier '{tier_name}' not found in service plans, skipping")
            continue

        tier_id = tiers[tier_name]["productTierId"]
        log.info(f"Fetching versions for '{tier_name}' (id={tier_id})...")
        versions = get_tier_versions(session, tier_id)

        preferred = next((v for v in versions if v["status"] == "Preferred"), None)
        active = [v for v in versions if v["status"] == "Active"]

        if not preferred:
            log.warning(f"No preferred version found for '{tier_name}', skipping")
            continue
        if not active:
            log.info(f"No active (old) versions found for '{tier_name}', nothing to upgrade from")
            continue

        new_version = preferred["version"]
        log.info(f"  preferred (new): {new_version}")
        log.info(f"  active (old candidates): {[v['version'] for v in active]}")

        for old_ver in active:
            old_version = old_ver["version"]

            if tier_name in ALWAYS_TEST_ALL_KEYS:
                # Free / Startup: single resource_key, always test
                active_resource_keys = {resource_configs[0][0]}
            else:
                # Pro / Enterprise: only test resource_keys with real RUNNING instances
                active_resource_keys = resource_keys_with_running_instances(
                    all_instances, tier_name, old_version
                )
                if not active_resource_keys:
                    log.info(f"  [{tier_name}] no RUNNING instances on version {old_version}, skipping")
                    continue
                log.info(f"  [{tier_name}] version {old_version} → active resource_keys: {active_resource_keys}")

            for (rkey, iname_prefix, itype, hcount, creplicas, cnet, tls) in resource_configs:
                if rkey not in active_resource_keys:
                    log.info(f"  [{tier_name}/{rkey}] no instances on {old_version}, skipping")
                    continue

                versioned_iname = f"{iname_prefix}-{old_version.replace('.', '-')}-{RUN_ID}"
                effective_tls = tls if TLS_OVERRIDE is None else TLS_OVERRIDE
                entries.append({
                    "name":             f"{tier_name}/{rkey} — {old_version} → {new_version}",
                    "old_version":      old_version,
                    "new_version":      new_version,
                    "ref_name":         tier_name,
                    "resource_key":     rkey,
                    "instance_name":    versioned_iname,
                    "instance_type":    itype,
                    "host_count":       hcount,
                    "cluster_replicas": creplicas,
                    "custom_network":   cnet,
                    "tls":              effective_tls,
                })
                log.info(f"  ✓ Added: {tier_name}/{rkey} {old_version} → {new_version}")

    return entries


def main():
    entries = build_matrix()

    if not entries:
        log.error(
            "No test matrix entries generated — no active versions with RUNNING instances found."
        )
        sys.exit(1)

    matrix = json.dumps({"include": entries})
    log.info(f"Matrix: {len(entries)} test(s) generated")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"matrix={matrix}\n")
    else:
        print(matrix)


if __name__ == "__main__":
    main()
