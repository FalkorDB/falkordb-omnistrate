#!/usr/bin/env python3
"""
Redis Crash Handler
Processes Redis crash alerts by extracting customer info, collecting logs,
analyzing crashes, and managing GitHub issues.
"""

import os
import sys
import json
import re
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import argparse
from google.cloud import storage


@dataclass
class CustomerInfo:
    """Customer information from Omnistrate API"""
    email: str
    name: str
    subscription_id: str


@dataclass
class CrashSummary:
    """Parsed crash information"""
    stack_traces: List[str]  # Top 3
    exit_code: str
    memory_rss: str
    client_command: str
    
    @property
    def signature(self) -> str:
        """Unique signature for duplicate detection"""
        return "|".join(self.stack_traces[:3] + [self.exit_code])


class OmnistrateClient:
    """Client for Omnistrate API"""
    
    def __init__(self, api_url: str, username: str, password: str):
        self.api_url = api_url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self._login()
    
    def _login(self):
        """Authenticate with Omnistrate API"""
        response = self.session.post(
            f"{self.api_url}/signin",
            json={"email": self.username, "password": self.password}
        )
        response.raise_for_status()
        # Session cookies are automatically handled by requests.Session
    
    def get_customer_info(self, service_id: str, environment_id: str, namespace: str) -> CustomerInfo:
        """Extract customer info from namespace (subscription ID)"""
        # Get all subscriptions
        response = self.session.get(
            f"{self.api_url}/service/{service_id}/environment/{environment_id}/subscription"
        )
        response.raise_for_status()
        subscriptions = response.json()
        
        # Find subscription by namespace (subscription ID)
        for sub in subscriptions:
            if sub.get('id') == namespace:
                return CustomerInfo(
                    email=sub.get('customerEmail', 'unknown@unknown.com'),
                    name=sub.get('customerOrgName', 'Unknown Customer'),
                    subscription_id=sub.get('id', namespace)
                )
        
        # If not found, return unknown
        return CustomerInfo(
            email='unknown@unknown.com',
            name='Unknown Customer',
            subscription_id=namespace
        )


class VMAauthClient:
    """Client for VictoriaLogs via VMAuth"""
    
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.auth = (username, password)
    
    def get_logs(self, namespace: str, pod: str, container: str, hours: int = 24) -> str:
        """Fetch logs from VictoriaLogs"""
        # Calculate time range
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)
        
        # Build LogsQL query
        query = f'{{namespace="{namespace}", pod=~"{pod}.*", container="{container}"}}'
        
        params = {
            'query': query,
            'start': int(start_time.timestamp() * 1000000000),  # nanoseconds
            'end': int(end_time.timestamp() * 1000000000),
            'limit': 10000
        }
        
        response = requests.get(
            f"{self.base_url}/select/logsql/query",
            params=params,
            auth=self.auth,
            timeout=60
        )
        response.raise_for_status()
        
        # Parse response and extract log messages
        data = response.json()
        logs = []
        
        for hit in data.get('hits', []):
            fields = hit.get('fields', [])
            for field in fields:
                if field.get('name') == '_msg':
                    logs.append(field.get('value', ''))
        
        return '\n'.join(logs)


class CrashAnalyzer:
    """Analyzes crash logs to extract useful information"""
    
    @staticmethod
    def parse_logs(logs: str) -> CrashSummary:
        """Parse crash logs and extract summary"""
        lines = logs.split('\n')
        
        # Extract stack traces - look for Redis crash format
        # Pattern: redis-server *:6379(debugCommand+0x244)[0xaaaab1e89984]
        stack_traces = []
        
        # First, try Redis crash stack trace format (function+offset)
        redis_stack_pattern = re.compile(r'redis-server[^(]*\(([^+)]+)(?:\+0x[0-9a-f]+)?\)')
        for line in lines:
            if 'redis-server' in line and '(' in line:
                matches = redis_stack_pattern.findall(line)
                for func in matches:
                    if func and func not in ['_start', '_libc_start_main']:
                        stack_traces.append(func)
        
        # If no Redis format found, try generic file:line format
        if not stack_traces:
            file_pattern = re.compile(r'([a-zA-Z0-9_/.-]+\.[ch]:?\d+)(?:\s+\(?(\w+)\)?)?')
            for line in lines:
                matches = file_pattern.findall(line)
                for match in matches:
                    location, function = match
                    if function:
                        stack_traces.append(f"{location} ({function})")
                    else:
                        stack_traces.append(location)
        
        # Get top 3 unique stack traces
        unique_stacks = []
        seen = set()
        for st in stack_traces:
            if st not in seen:
                unique_stacks.append(st)
                seen.add(st)
                if len(unique_stacks) >= 3:
                    break
        
        # Pad with "N/A" if less than 3
        while len(unique_stacks) < 3:
            unique_stacks.append("N/A")
        
        # Extract exit code
        exit_code = "unknown"
        
        # Try signal-based exit code (128 + signal number)
        signal_pattern = re.compile(r'crashed by signal:\s+(\d+)', re.IGNORECASE)
        for line in lines:
            match = signal_pattern.search(line)
            if match:
                signal_num = int(match.group(1))
                exit_code = str(128 + signal_num)
                break
        
        # Try explicit exit code
        if exit_code == "unknown":
            exit_pattern = re.compile(r'exit.*code[:\s]+(\d+)', re.IGNORECASE)
            for line in lines:
                match = exit_pattern.search(line)
                if match:
                    exit_code = match.group(1)
                    break
        
        # Extract memory RSS (in bytes)
        memory_rss = "unknown"
        
        # Try Redis INFO format: used_memory_rss:25403392
        rss_pattern = re.compile(r'used_memory_rss:(\d+)', re.IGNORECASE)
        for line in lines:
            match = rss_pattern.search(line)
            if match:
                memory_rss = match.group(1)
                break
        
        # Try generic RSS format
        if memory_rss == "unknown":
            generic_rss = re.compile(r'rss[:\s]+(\d+)', re.IGNORECASE)
            for line in lines:
                match = generic_rss.search(line)
                if match:
                    memory_rss = match.group(1)
                    break
        
        # Extract client command
        client_command = "unknown"
        
        # Try Redis client info format: cmd=debug
        cmd_pattern = re.compile(r'cmd=(\S+)', re.IGNORECASE)
        for line in lines:
            match = cmd_pattern.search(line)
            if match:
                client_command = match.group(1)
                break
        
        # Try argv format: argv[0]: '"debug"' argv[1]: '"segfault"'
        if client_command == "unknown":
            argv_parts = []
            argv_pattern = re.compile(r"argv\[\d+\]:\s*['\"]([^'\"]+)['\"]")
            for line in lines:
                matches = argv_pattern.findall(line)
                argv_parts.extend(matches)
            if argv_parts:
                client_command = ' '.join(argv_parts)
        
        # Try generic command format
        if client_command == "unknown":
            command_patterns = [
                re.compile(r'client.*command[:\s]+["\']?([^"\']+)["\']?', re.IGNORECASE),
                re.compile(r'last.*command[:\s]+["\']?([^"\']+)["\']?', re.IGNORECASE),
            ]
            
            for line in reversed(lines):
                for pattern in command_patterns:
                    match = pattern.search(line)
                    if match:
                        client_command = match.group(1).strip()
                        break
                if client_command != "unknown":
                    break
        
        return CrashSummary(
            stack_traces=unique_stacks,
            exit_code=exit_code,
            memory_rss=memory_rss,
            client_command=client_command
        )


class GitHubIssueManager:
    """Manages GitHub issues for crash tracking"""
    
    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo  # format: "owner/repo"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github.v3+json'
        })
        self.api_url = "https://api.github.com"
    
    def find_duplicate(self, customer_email: str, crash: CrashSummary, hours: int = 24) -> Optional[int]:
        """Find duplicate issue for same customer and crash signature"""
        # Calculate cutoff time (24 hours ago)
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        
        # Get all open issues for this customer
        params = {
            'state': 'open',
            'labels': f'customer:{customer_email}',
            'per_page': 100
        }
        
        response = self.session.get(
            f"{self.api_url}/repos/{self.repo}/issues",
            params=params
        )
        response.raise_for_status()
        issues = response.json()
        
        current_sig = crash.signature
        
        for issue in issues:
            # Check if issue was created in last 24 hours
            created_at = datetime.fromisoformat(issue['created_at'].replace('Z', '+00:00'))
            if created_at < cutoff:
                continue  # Skip issues older than 24 hours
            
            body = issue.get('body', '')
            
            # Extract crash signature from issue body
            stack_1 = self._extract_field(body, 'Stack Trace 1')
            stack_2 = self._extract_field(body, 'Stack Trace 2')
            stack_3 = self._extract_field(body, 'Stack Trace 3')
            exit_code = self._extract_field(body, 'Exit Code')
            
            existing_sig = f"{stack_1}|{stack_2}|{stack_3}|{exit_code}"
            
            if current_sig == existing_sig:
                return issue['number']
        
        return None
    
    @staticmethod
    def _extract_field(text: str, field_name: str) -> str:
        """Extract field value from issue body"""
        pattern = re.compile(rf'\*\*{field_name}:\*\*\s+(.+)')
        match = pattern.search(text)
        return match.group(1).strip() if match else ""
    
    def create_issue(
        self,
        customer: CustomerInfo,
        crash: CrashSummary,
        pod: str,
        namespace: str,
        cluster: str,
        container: str,
        log_url: str,
        timestamp: str
    ) -> int:
        """Create new GitHub issue"""
        title = f"[CRITICAL] Redis Crash: {pod} in {namespace} ({cluster}) - {timestamp}"
        
        body = f"""## Redis Crash Detected

**Customer:** {customer.name} ({customer.email})
**Subscription ID:** {customer.subscription_id}
**Pod:** {pod}
**Container:** {container}
**Namespace:** {namespace}
**Cluster:** {cluster}
**Time (IST):** {timestamp}

### Crash Summary

**Stack Trace 1:** {crash.stack_traces[0]}
**Stack Trace 2:** {crash.stack_traces[1]}
**Stack Trace 3:** {crash.stack_traces[2]}
**Exit Code:** {crash.exit_code}
**Memory RSS:** {crash.memory_rss} bytes
**Client Command:** {crash.client_command}

**Crash Logs:** [Download from GCS]({log_url})"""
        
        data = {
            'title': title,
            'body': body,
            'labels': [f'customer:{customer.email}', 'crash', 'redis']
        }
        
        response = self.session.post(
            f"{self.api_url}/repos/{self.repo}/issues",
            json=data
        )
        response.raise_for_status()
        return response.json()['number']
    
    def add_comment(
        self,
        issue_number: int,
        crash: CrashSummary,
        pod: str,
        namespace: str,
        cluster: str,
        container: str,
        log_url: str,
        timestamp: str
    ):
        """Add comment to existing issue"""
        comment = f"""### Another crash detected at {timestamp}

**Pod:** {pod}
**Container:** {container}
**Namespace:** {namespace}
**Cluster:** {cluster}

**Stack Trace 1:** {crash.stack_traces[0]}
**Stack Trace 2:** {crash.stack_traces[1]}
**Stack Trace 3:** {crash.stack_traces[2]}
**Exit Code:** {crash.exit_code}
**Memory RSS:** {crash.memory_rss} bytes
**Client Command:** {crash.client_command}

**Crash Logs:** [Download from GCS]({log_url})"""
        
        response = self.session.post(
            f"{self.api_url}/repos/{self.repo}/issues/{issue_number}/comments",
            json={'body': comment}
        )
        response.raise_for_status()


class GCSUploader:
    """Uploads logs to Google Cloud Storage"""
    
    def __init__(self, bucket: str):
        self.bucket_name = bucket
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket)
    
    def upload_logs(self, logs: str, customer_email: str, timestamp: str) -> str:
        """Upload logs to GCS and return signed URL"""
        log_filename = f"redis-crash-{timestamp}.log"
        temp_file = Path(log_filename)
        
        try:
            # Write logs to temp file
            temp_file.write_text(logs, encoding='utf-8')
            
            # Build GCS path: customer@email.com/20260201-121530/redis-crash-20260201-121530.log
            gcs_path = f"{customer_email}/{timestamp}/{log_filename}"
            
            # Upload to GCS
            blob = self.bucket.blob(gcs_path)
            blob.upload_from_filename(str(temp_file))
            
            print(f"Uploaded to gs://{self.bucket_name}/{gcs_path}")
            
            # Generate signed URL (7 day expiry - maximum allowed)
            signed_url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(days=7),
                method="GET"
            )
            
            return signed_url
            
        except Exception as e:
            print(f"Error uploading to GCS: {e}", file=sys.stderr)
            raise
        finally:
            # Clean up temp file
            if temp_file.exists():
                temp_file.unlink()
                print(f"Cleaned up temp file: {log_filename}")


class GoogleChatNotifier:
    """Sends notifications to Google Chat"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send_notification(
        self,
        customer_email: str,
        cluster: str,
        pod: str,
        namespace: str,
        crash: CrashSummary,
        issue_number: int,
        issue_repo: str,
        log_url: str,
        is_duplicate: bool
    ):
        """Send crash notification to Google Chat"""
        crash_type = "🔄 Redis Crash (Duplicate)" if is_duplicate else "🚨 Redis Crash (New)"
        
        payload = {
            "text": crash_type,
            "cards": [{
                "header": {
                    "title": crash_type,
                    "subtitle": f"Customer: {customer_email}"
                },
                "sections": [
                    {
                        "widgets": [
                            {"keyValue": {"topLabel": "Customer", "content": customer_email}},
                            {"keyValue": {"topLabel": "Cluster", "content": cluster}},
                            {"keyValue": {"topLabel": "Pod", "content": pod}},
                            {"keyValue": {"topLabel": "Namespace", "content": namespace}},
                            {"keyValue": {"topLabel": "Exit Code", "content": crash.exit_code}}
                        ]
                    },
                    {
                        "widgets": [{
                            "textParagraph": {
                                "text": f"<b>Stack Trace:</b><br><code>{crash.stack_traces[0]}<br>{crash.stack_traces[1]}<br>{crash.stack_traces[2]}</code>"
                            }
                        }]
                    },
                    {
                        "widgets": [{
                            "buttons": [
                                {
                                    "textButton": {
                                        "text": f"View Issue #{issue_number}",
                                        "onClick": {
                                            "openLink": {
                                                "url": f"https://github.com/{issue_repo}/issues/{issue_number}"
                                            }
                                        }
                                    }
                                },
                                {
                                    "textButton": {
                                        "text": "Download Logs",
                                        "onClick": {
                                            "openLink": {
                                                "url": log_url
                                            }
                                        }
                                    }
                                }
                            ]
                        }]
                    }
                ]
            }]
        }
        
        response = requests.post(self.webhook_url, json=payload)
        response.raise_for_status()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Process Redis crash alerts')
    parser.add_argument('--pod', required=True, help='Pod name')
    parser.add_argument('--namespace', required=True, help='Namespace')
    parser.add_argument('--cluster', required=True, help='Cluster name')
    parser.add_argument('--container', required=True, help='Container name')
    parser.add_argument('--vmauth-url', required=True, help='VMAuth URL for log collection')
    
    args = parser.parse_args()
    
    # Get environment variables
    omnistrate_url = os.environ['OMNISTRATE_API_URL']
    omnistrate_user = os.environ['OMNISTRATE_USERNAME']
    omnistrate_pass = os.environ['OMNISTRATE_PASSWORD']
    service_id = os.environ['OMNISTRATE_SERVICE_ID']
    environment_id = os.environ['OMNISTRATE_ENVIRONMENT_ID']
    
    vmauth_url = args.vmauth_url
    vmauth_user = os.environ['VMAUTH_USERNAME']
    vmauth_pass = os.environ['VMAUTH_PASSWORD']
    
    github_token = os.environ['GITHUB_TOKEN']
    issue_repo = os.environ['ISSUE_REPO']
    
    gcs_bucket = os.environ['GCS_BUCKET']
    google_chat_webhook = os.environ['GOOGLE_CHAT_WEBHOOK_URL']
    
    # Generate timestamp
    timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    
    print(f"Processing crash for pod {args.pod} in namespace {args.namespace}")
    
    # Step 1: Extract customer info
    print("Extracting customer information...")
    omnistrate = OmnistrateClient(omnistrate_url, omnistrate_user, omnistrate_pass)
    customer = omnistrate.get_customer_info(service_id, environment_id, args.namespace)
    print(f"Customer: {customer.name} ({customer.email})")
    
    # Step 2: Collect logs
    print("Collecting logs from VictoriaLogs...")
    vmauth = VMAauthClient(vmauth_url, vmauth_user, vmauth_pass)
    logs = vmauth.get_logs(args.namespace, args.pod, args.container)
    print(f"Collected {len(logs)} bytes of logs")
    
    # Step 3: Parse crash summary
    print("Analyzing crash...")
    analyzer = CrashAnalyzer()
    crash = analyzer.parse_logs(logs)
    print(f"Exit code: {crash.exit_code}, Stack traces: {len([s for s in crash.stack_traces if s != 'N/A'])}")
    
    # Step 4: Upload to GCS
    print("Uploading logs to GCS...")
    uploader = GCSUploader(gcs_bucket)
    log_url = uploader.upload_logs(logs, customer.email, timestamp)
    print(f"Logs uploaded: {log_url}")
    
    # Step 5: Check for duplicates
    print("Checking for duplicate issues...")
    github = GitHubIssueManager(github_token, issue_repo)
    duplicate_issue = github.find_duplicate(customer.email, crash)
    
    if duplicate_issue:
        print(f"Duplicate found: Issue #{duplicate_issue}")
        github.add_comment(
            duplicate_issue, crash, args.pod, args.namespace,
            args.cluster, args.container, log_url, timestamp
        )
        issue_number = duplicate_issue
        is_duplicate = True
    else:
        print("No duplicate found, creating new issue...")
        issue_number = github.create_issue(
            customer, crash, args.pod, args.namespace,
            args.cluster, args.container, log_url, timestamp
        )
        print(f"Created issue #{issue_number}")
        is_duplicate = False
    
    # Step 6: Send notification
    print("Sending Google Chat notification...")
    notifier = GoogleChatNotifier(google_chat_webhook)
    notifier.send_notification(
        customer.email, args.cluster, args.pod, args.namespace,
        crash, issue_number, issue_repo, log_url, is_duplicate
    )
    print("Notification sent!")
    
    # Output for GitHub Actions
    print(f"\n::set-output name=issue_number::{issue_number}")
    print(f"::set-output name=is_duplicate::{str(is_duplicate).lower()}")
    print(f"::set-output name=customer_email::{customer.email}")
    
    print("\n✅ Crash handling complete!")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
