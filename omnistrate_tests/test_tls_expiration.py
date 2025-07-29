#!/usr/bin/env python3
"""
TLS Certificate Expiration Monitor for Omni                    tls_instances = []

            for instance in raw_instances:
                instance_id = instance.get('consumptionResourceInstanceResult', {}).get('id')
                if not instance_id:
                    continue          # Get all instances using the filter
            raw_instances = self.omnistrate_api.list_instances(self.instance_filter)

            tls_instances = []e Instances

This script checks TLS certificate expiration dates for all TLS-enabled instances
and sends PagerDuty alerts if any certificates expire within 15 days.
"""

import sys
import ssl
import socket
import logging
import argparse
import requests
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

# Add the parent directory to the path to import omnistrate classes
file = Path(__file__).resolve()
parent, root = file.parent, file.parents[1]
sys.path.append(str(root))

from contextlib import suppress

with suppress(ValueError):
    sys.path.remove(str(parent))

from omnistrate_tests.classes.omnistrate_fleet_api import OmnistrateFleetAPI

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class TLSCertificateMonitor:
    """Monitor TLS certificate expiration for Omnistrate instances."""

    def __init__(
        self,
        omnistrate_user: str,
        omnistrate_password: str,
        pagerduty_routing_key: str,
        service_id: str,
        env_id: str,
        skip_free_tier: bool = True,
    ):
        """
        Initialize the TLS certificate monitor.

        Args:
            omnistrate_user: Omnistrate username/email
            omnistrate_password: Omnistrate password
            pagerduty_routing_key: PagerDuty Events API v2 routing key
            days_threshold: Number of days before expiration to trigger alert
            instance_filter: Filter string like "service:FalkorDB,environment:Prod,status:RUNNING"
            skip_free_tier: Whether to skip free tier instances
        """
        self.omnistrate_api = OmnistrateFleetAPI(omnistrate_user, omnistrate_password)
        self.pagerduty_routing_key = pagerduty_routing_key
        self.service_id = service_id
        self.env_id = env_id
        self.skip_free_tier = skip_free_tier
        self.pagerduty_url = "https://events.pagerduty.com/v2/enqueue"

    def get_tls_enabled_instances(self) -> List[Dict]:
        """
        Retrieve all TLS-enabled instances from Omnistrate.

        Returns:
            List of instance dictionaries with TLS enabled
        """
        logger.info("Fetching TLS-enabled instances from Omnistrate...")

        try:
            # Get all instances using the filter
            raw_instances = self.omnistrate_api.list_instances(
                self.service_id, self.env_id
            )
            tls_instances = []

            filtered_instances = [
                instance for instance in raw_instances
                if instance.get("consumptionResourceInstanceResult", {}).get("status") == "RUNNING"
                and self.skip_free_tier and "free" not in instance.get("productTierName", "").lower()
                and instance.get("input_params", {}).get("enableTLS", "false").lower() in ["true", "1", "yes"]
                and instance.get("consumptionResourceInstanceResult", {}).get("network_type") != "INTERNAL"
            ]

            for instance in filtered_instances:
                instance_id = instance.get("consumptionResourceInstanceResult", {}).get("id")
                if not instance_id:
                    continue

                # Extract endpoints from detailedNetworkTopology
                endpoints = []
                detailed_topology = instance.get(
                    "consumptionResourceInstanceResult", {}
                ).get("detailedNetworkTopology", {})

                for _, resource_data in detailed_topology.items():
                    # Check if resource is publicly accessible and has cluster endpoint
                    if (
                        resource_data.get("publiclyAccessible")
                        and len(resource_data.get("nodes", [])) > 0
                    ):
                        # Add all node endpoints with each port in the resource
                        for node in resource_data.get("nodes", []):
                            node_endpoint = node.get("endpoint")
                            if node_endpoint:
                                for port in node.get("ports", []):
                                    if f"{port}" != "16379":
                                        endpoints.append(f"{node_endpoint}:{port}")

                if endpoints:
                    tls_instance = {
                        "instance_id": instance_id,
                        "endpoints": endpoints,
                        "tls": True,
                        "product_tier_name": instance.get("productTierName", ""),
                        "service_name": instance.get("serviceName", ""),
                        "environment_name": instance.get("serviceEnvName", ""),
                    }
                    tls_instances.append(tls_instance)
                    logger.debug(
                        "Found TLS instance %s with %d endpoints",
                        instance_id,
                        len(endpoints),
                    )

            logger.info("Found %d TLS-enabled instances", len(tls_instances))
            return tls_instances

        except requests.RequestException as e:
            logger.error("Failed to fetch instances: %s", e)
            raise

    def check_certificate_expiration(
        self, hostname: str, port: int, timeout: int = 10
    ) -> Optional[datetime]:
        """
        Check the TLS certificate expiration date for a given hostname and port.

        Args:
            hostname: The hostname to check
            port: The port to connect to
            timeout: Connection timeout in seconds

        Returns:
            Certificate expiration date or None if check failed
        """
        try:
            logger.debug("Checking certificate for %s:%d", hostname, port)

            # Create SSL context
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED

            # Connect and get certificate
            with socket.create_connection((hostname, port), timeout=timeout) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()

            logger.debug("Certificate retrieved for %s:%d %s", hostname, port, cert)

            # Parse expiration date
            not_after = cert["notAfter"]
            expiry_date = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")

            logger.debug(
                "Certificate for %s:%d expires on %s", hostname, port, expiry_date
            )
            return expiry_date

        except socket.timeout:
            logger.warning("Timeout connecting to %s:%d", hostname, port)
            return None
        except ssl.SSLError as e:
            logger.warning("SSL error for %s:%d: %s", hostname, port, e)
            return None
        except (KeyError, ValueError) as e:
            logger.error("Error parsing certificate for %s:%d: %s", hostname, port, e)
            return None

    def check_instance_certificates(self, instance: Dict) -> List[Dict]:
        """
        Check all certificates for a given instance.

        Args:
            instance: Instance dictionary containing endpoints

        Returns:
            List of certificate check results
        """
        results = []
        instance_id = instance.get("instance_id", "unknown")
        endpoints = instance.get("endpoints", [])

        logger.info("Checking certificates for instance %s", instance_id)

        for endpoint in endpoints:
            try:
                # Parse endpoint (format: hostname:port)
                if ":" in endpoint:
                    hostname, port_str = endpoint.rsplit(":", 1)
                    port = int(port_str)
                else:
                    hostname = endpoint
                    port = 6379  # Default Redis port

                expiry_date = self.check_certificate_expiration(hostname, port)

                if expiry_date:
                    days_until_expiry = (expiry_date - datetime.now()).days

                    result = {
                        "instance_id": instance_id,
                        "hostname": hostname,
                        "port": port,
                        "expiry_date": expiry_date,
                        "days_until_expiry": days_until_expiry,
                    }
                    results.append(result)

                    if days_until_expiry <= 15:
                        logger.warning(
                            "Certificate for %s:%d expires in %d days",
                            hostname,
                            port,
                            days_until_expiry,
                        )

            except ValueError as e:
                logger.error("Invalid endpoint format %s: %s", endpoint, e)
            except (socket.error, OSError) as e:
                logger.error("Error processing endpoint %s: %s", endpoint, e)

        return results

    def send_pagerduty_alert_for_instance(
        self, instance_id: str, instance_info: Dict, expiring_certs: List[Dict]
    ) -> bool:
        """
        Send a PagerDuty alert for a specific instance with expiring certificates.

        Args:
            instance_id: The instance ID
            instance_info: Instance information (service name, tier, etc.)
            expiring_certs: List of expiring certificate information for this instance

        Returns:
            True if alert was sent successfully, False otherwise
        """
        if not expiring_certs:
            return True

        # Group certificates by severity
        critical_certs = [
            cert for cert in expiring_certs if cert["days_until_expiry"] <= 7
        ]
        warning_certs = [
            cert for cert in expiring_certs if 7 < cert["days_until_expiry"] <= 15
        ]

        # Determine severity
        severity = "critical" if critical_certs else "warning"

        # Create alert summary
        total_expiring = len(expiring_certs)
        critical_count = len(critical_certs)
        warning_count = len(warning_certs)

        service_name = instance_info.get("service_name", "Unknown")
        environment = instance_info.get("environment_name", "Unknown")
        tier = instance_info.get("product_tier_name", "Unknown")

        summary = (
            f"TLS Certificate Expiration: {total_expiring} certificates expiring "
            f"for instance {instance_id} ({service_name}/{environment}/{tier})"
        )

        if critical_count > 0:
            summary += f" - {critical_count} critical, {warning_count} warning"

        # Create detailed description
        details = []
        for cert in sorted(expiring_certs, key=lambda x: x["days_until_expiry"]):
            details.append(
                f"• {cert['hostname']}:{cert['port']} ({cert['service_type']}) "
                f"expires in {cert['days_until_expiry']} days "
                f"({cert['expiry_date'].strftime('%Y-%m-%d %H:%M:%S')})"
            )

        description = f"{summary}\\n\\nCertificate Details:\\n" + "\\n".join(details)

        # Prepare PagerDuty payload
        payload = {
            "routing_key": self.pagerduty_routing_key,
            "event_action": "trigger",
            "dedup_key": f"tls-cert-expiration-{instance_id}",
            "payload": {
                "summary": summary,
                "source": "omnistrate-tls-monitor",
                "severity": severity,
                "component": f"falkordb-{instance_id}",
                "group": "infrastructure",
                "class": "tls-certificate",
                "custom_details": {
                    "description": description,
                    "instance_id": instance_id,
                    "service_name": service_name,
                    "environment_name": environment,
                    "product_tier_name": tier,
                    "expiring_certificates": expiring_certs,
                    "total_expiring": total_expiring,
                    "critical_count": critical_count,
                    "warning_count": warning_count,
                },
            },
        }

        try:
            logger.info(
                "Sending PagerDuty alert for instance %s with %d expiring certificates",
                instance_id,
                total_expiring,
            )
            response = requests.post(
                self.pagerduty_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )

            if response.status_code == 202:
                logger.info(
                    "PagerDuty alert sent successfully for instance %s", instance_id
                )
                return True
            else:
                logger.error(
                    "Failed to send PagerDuty alert for instance %s: %d - %s",
                    instance_id,
                    response.status_code,
                    response.text,
                )
                return False

        except requests.RequestException as e:
            logger.error(
                "Error sending PagerDuty alert for instance %s: %s", instance_id, e
            )
            return False

    def run_check(self) -> Dict:
        """
        Run the complete TLS certificate expiration check.

        Returns:
            Dictionary with check results and statistics
        """
        logger.info("Starting TLS certificate expiration check...")

        try:
            # Get TLS-enabled instances
            instances = self.get_tls_enabled_instances()

            if not instances:
                logger.info("No TLS-enabled instances found")
                return {
                    "success": True,
                    "instances_checked": 0,
                    "certificates_checked": 0,
                    "expiring_certificates": 0,
                    "alerts_sent": 0,
                    "instances_with_expiring_certs": 0,
                }

            # Check certificates for all instances and group by instance
            total_certificates_checked = 0
            total_expiring_certificates = 0
            alerts_sent = 0
            instances_with_expiring_certs = 0

            for instance in instances:
                instance_id = instance["instance_id"]
                logger.info("Processing instance %s", instance_id)

                # Check certificates for this instance
                cert_results = self.check_instance_certificates(instance)
                total_certificates_checked += len(cert_results)

                # Filter expiring certificates for this instance
                expiring_certs = [
                    cert for cert in cert_results if cert["days_until_expiry"] <= 15
                ]

                if expiring_certs:
                    total_expiring_certificates += len(expiring_certs)
                    instances_with_expiring_certs += 1

                    # Send individual alert for this instance
                    alert_sent = self.send_pagerduty_alert_for_instance(
                        instance_id, instance, expiring_certs
                    )

                    if alert_sent:
                        alerts_sent += 1
                        logger.info(
                            "Alert sent for instance %s with %d expiring certificates",
                            instance_id,
                            len(expiring_certs),
                        )
                    else:
                        logger.error(
                            "Failed to send alert for instance %s", instance_id
                        )
                else:
                    logger.info("Instance %s has no expiring certificates", instance_id)

            if total_expiring_certificates == 0:
                logger.info("All certificates are valid and not expiring soon")
            else:
                logger.warning(
                    "Found %d expiring certificates across %d instances",
                    total_expiring_certificates,
                    instances_with_expiring_certs,
                )

            # Return summary
            return {
                "success": True,
                "instances_checked": len(instances),
                "certificates_checked": total_certificates_checked,
                "expiring_certificates": total_expiring_certificates,
                "alerts_sent": alerts_sent,
                "instances_with_expiring_certs": instances_with_expiring_certs,
            }

        except (requests.RequestException, ValueError) as e:
            logger.error("TLS certificate check failed: %s", e)
            return {"success": False, "error": str(e)}


def main():
    """Main function to run the TLS certificate expiration check."""
    parser = argparse.ArgumentParser(
        description="Monitor TLS certificate expiration for Omnistrate instances"
    )
    parser.add_argument("omnistrate_user", help="Omnistrate username/email")
    parser.add_argument("omnistrate_password", help="Omnistrate password")
    parser.add_argument(
        "pagerduty_routing_key", help="PagerDuty Events API v2 routing key"
    )
    parser.add_argument(
        "service_id", help="Omnistrate service ID to check (e.g. FalkorDB)"
    )
    parser.add_argument("env_id", help="Omnistrate environment ID to check (e.g. Prod)")
    parser.add_argument(
        "--include-free-tier",
        action="store_true",
        help="Include free tier instances in the check",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Initialize monitor
    monitor = TLSCertificateMonitor(
        omnistrate_user=args.omnistrate_user,
        omnistrate_password=args.omnistrate_password,
        pagerduty_routing_key=args.pagerduty_routing_key,
        service_id=args.service_id,
        env_id=args.env_id,
        skip_free_tier=not args.include_free_tier,
    )

    # Run the check
    result = monitor.run_check()

    if result["success"]:
        logger.info("Check completed successfully")
        logger.info("  - Instances checked: %d", result["instances_checked"])
        logger.info("  - Certificates checked: %d", result["certificates_checked"])
        logger.info("  - Expiring certificates: %d", result["expiring_certificates"])
        logger.info(
            "  - Instances with expiring certs: %d",
            result["instances_with_expiring_certs"],
        )
        logger.info("  - Alerts sent: %d", result["alerts_sent"])

        # Exit with appropriate code
        sys.exit(0 if result["expiring_certificates"] == 0 else 1)
    else:
        logger.error("Check failed: %s", result["error"])
        sys.exit(2)


if __name__ == "__main__":
    main()
