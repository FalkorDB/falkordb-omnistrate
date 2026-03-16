"""
Automatically generate the GitHub Actions test matrix for upgrade tests.

Calls the Omnistrate API to discover:
  - new_version  = the PREFERRED tier version (the one new customers get)
  - old_versions = ACTIVE versions that have at least one RUNNING instance
                   on a resource_key covered by this test suite

For Pro and Enterprise the resource_key is further filtered: only
resource_keys (standalone / single-Zone / multi-Zone / cluster-*) that have
at least one RUNNING customer instance on that specific old_version are tested.

Execution order in the matrix: Free → Startup → Pro → Enterprise
(GitHub Actions matrix jobs run in parallel but are listed in this order.)

Required environment variables
-------------------------------
OMNISTRATE_USERNAME   : Omnistrate account e-mail
OMNISTRATE_PASSWORD   : Omnistrate account password
OMNISTRATE_SERVICE_ID : Service ID (falls back to OMNISTRATE_INTERNAL_SERVICE_ID)
OMNISTRATE_ENV_ID     : Environment ID (falls back to OMNISTRATE_INTERNAL_PROD_ENVIRONMENT)
GITHUB_RUN_ID         : Injected by GitHub Actions; appended to instance names.

Optional
--------
TLS          : "true"/"false" — override per-tier TLS default for all tiers
DRY_RUN      : "true" — print the planned upgrades to stdout and exit without
               writing GITHUB_OUTPUT; useful for debugging / previewing.
"""

import json
import logging
import os
import sys
import time

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
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() == "true"

# Optional TLS override: if set, applies to all tiers instead of per-tier default.
_tls_env = os.getenv("TLS", "").strip().lower()
TLS_OVERRIDE = True if _tls_env == "true" else (False if _tls_env == "false" else None)

# ---------------------------------------------------------------------------
# Per-tier static config — ORDER MATTERS: Free → Startup → Pro → Enterprise
# Tuple: (resource_key, instance_name_prefix, instance_type,
#         host_count, cluster_replicas, custom_network, tls)
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


# ---------------------------------------------------------------------------
# HTTP client with 429 retry (Retry-After aware)
# ---------------------------------------------------------------------------

def _make_session(token: str) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=8,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=True,
        allowed_methods=["GET", "POST"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    return session


def _get(session: requests.Session, url: str) -> dict:
    """GET with explicit 429 back-off on top of the adapter-level retry."""
    for attempt in range(5):
        r = session.get(url, timeout=60)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", 10)) + attempt * 5
            log.warning(f"429 rate-limited on {url}, waiting {wait}s before retry {attempt+1}/5")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()  # raise after all retries exhausted
    return r.json()


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
    """Return {tier_name: raw_tier_dict} for the service."""
    data = _get(session, f"{BASE_URL}/service/{SERVICE_ID}/environment/{ENV_ID}/service-plan")
    return {t["productTierName"]: t for t in data.get("servicePlans", [])}


def get_tier_versions(session: requests.Session, tier_id: str) -> list[dict]:
    """Return raw tierVersionSets list for a product tier."""
    data = _get(session, f"{BASE_URL}/service/{SERVICE_ID}/productTier/{tier_id}/version-set")
    return data.get("tierVersionSets", [])


def list_instances(session: requests.Session) -> list[dict]:
    """Return all fleet instances for the service+environment."""
    data = _get(session, f"{BASE_URL}/fleet/service/{SERVICE_ID}/environment/{ENV_ID}/instances")
    return data.get("resourceInstances", [])


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def running_resource_keys_for_version(
    instances: list[dict], tier_name: str, version: str
) -> set[str]:
    """
    Return the set of resource_keys that have at least one RUNNING instance
    for the given (tier_name, version) pair.

    Instance list item shape (top-level keys logged on first run):
      {
        "productTierName": "FalkorDB Pro",
        "productTierVersion": "73.0",   ← or tierVersion, or nested
        "consumptionResourceInstanceResult": {
          "status": "RUNNING",
          "detailedNetworkTopology": {
            "<resourceId>": {"resourceKey": "multi-Zone", ...},
          }
        }
      }
    """
    found: set[str] = set()
    for inst in instances:
        if inst.get("productTierName") != tier_name:
            continue

        result = inst.get("consumptionResourceInstanceResult", {})

        # Version field name is not officially documented — check all known paths.
        inst_version = (
            inst.get("productTierVersion")
            or inst.get("tierVersion")
            or result.get("productTierVersion")
            or result.get("tierVersion")
            or ""
        )
        if inst_version != version:
            continue

        if result.get("status") != "RUNNING":
            continue

        topology = result.get("detailedNetworkTopology", {})
        for resource_data in topology.values():
            rk = resource_data.get("resourceKey")
            if rk:
                found.add(rk)
    return found


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
    log.info(f"  total instances fetched: {len(all_instances)}")
    if all_instances:
        log.info(f"  instance top-level fields (sample): {list(all_instances[0].keys())}")

    # Ordered: Free → Startup → Pro → Enterprise
    entries: list[dict] = []

    for tier_name, resource_configs in TIER_CONFIG.items():
        if tier_name not in tiers:
            log.warning(f"Tier '{tier_name}' not found in service plans, skipping")
            continue

        tier_id = tiers[tier_name]["productTierId"]
        log.info(f"\n[{tier_name}] Fetching versions (id={tier_id})...")
        versions = get_tier_versions(session, tier_id)

        # Only consider non-deprecated versions.
        valid = [v for v in versions if v["status"] != "Deprecated"]

        if not valid:
            log.warning(f"[{tier_name}] No valid versions found, skipping")
            continue

        # Target = the highest version number regardless of Preferred/Active status.
        latest = max(valid, key=lambda v: [int(x) for x in v["version"].split(".")])
        new_version = latest["version"]

        # Old candidates = every other valid version (anything that's not the latest).
        old_candidates = [v for v in valid if v["version"] != new_version]

        if not old_candidates:
            log.info(f"[{tier_name}] Only one version exists — nothing to upgrade from")
            continue

        log.info(f"  latest (target):   {new_version}")
        log.info(f"  old candidates:    {[v['version'] for v in old_candidates]}")

        for old_ver in old_candidates:
            old_version = old_ver["version"]

            # For ALL tiers: only test versions that have at least one RUNNING instance.
            # For Free/Startup this still checks, there's just only one resource_key possible.
            active_rkeys = running_resource_keys_for_version(all_instances, tier_name, old_version)

            if not active_rkeys:
                log.info(f"  [{old_version}] no RUNNING instances — skipping")
                continue

            log.info(f"  [{old_version}] RUNNING resource_keys: {active_rkeys}")

            for (rkey, iname_prefix, itype, hcount, creplicas, cnet, tls) in resource_configs:
                if rkey not in active_rkeys:
                    log.info(f"    {rkey}: no instances — skipping")
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
                log.info(f"    ✓ {rkey}: {old_version} → {new_version}")

    return entries


def main():
    entries = build_matrix()

    if not entries:
        log.error("No test matrix entries — no active versions with RUNNING instances found.")
        sys.exit(1)

    log.info(f"\n=== Planned upgrades ({len(entries)} total) ===")
    for e in entries:
        log.info(f"  {e['name']}")

    if DRY_RUN:
        log.info("DRY_RUN=true — exiting without writing GITHUB_OUTPUT")
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a") as fh:
                fh.write("matrix=\n")
        return

    matrix = json.dumps({"include": entries})
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as fh:
            fh.write(f"matrix={matrix}\n")
    else:
        print(matrix)


if __name__ == "__main__":
    main()
