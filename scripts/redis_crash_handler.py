#!/usr/bin/env python3
"""
Redis Crash Handler
Processes Redis crash alerts by extracting customer info, collecting logs,
analyzing crashes, and managing GitHub issues.
"""

import os
import sys
import re
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass
import argparse
import urllib3

# Disable SSL warnings for dev environment
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
        self.token = None
        self._login()
    
    def _login(self):
        """Authenticate with Omnistrate API and get JWT token"""
        response = requests.post(
            f"{self.api_url}/signin",
            json={"email": self.username, "password": self.password},
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        self.token = response.json()["jwtToken"]
    
    def _get_headers(self):
        """Get authentication headers"""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}"
        }
    
    def get_customer_info(self, service_id: str, environment_id: str, namespace: str) -> CustomerInfo:
        """Extract customer info from instance details
        
        The namespace parameter is the subscription ID.
        We query instances by subscription ID to get the owner, then look up the user email.
        """
        # Get instances filtered by subscription ID (namespace)
        response = requests.get(
            f"{self.api_url}/fleet/service/{service_id}/environment/{environment_id}/instances",
            params={
                "Filter": "excludeCloudAccounts",
                "ExcludeDetail": "true",
                "SubscriptionId": namespace
            },
            headers=self._get_headers(),
            timeout=30,
            verify=False
        )
        response.raise_for_status()
        resource_instances = response.json().get("resourceInstances", [])
        
        if not resource_instances:
            return CustomerInfo(
                email='unknown@unknown.com',
                name='Unknown Customer',
                subscription_id=namespace
            )
        
        # Get subscription owner name from first instance
        subscription_owner_name = resource_instances[0].get("subscriptionOwnerName", "Unknown")
        
        # Get all users to find the email
        users_response = requests.get(
            f"{self.api_url}/fleet/users",
            headers=self._get_headers(),
            timeout=30,
            verify=False
        )
        users_response.raise_for_status()
        users = users_response.json().get("users", [])
        
        # Find user by matching userName with subscriptionOwnerName
        for user in users:
            if user.get("userName") == subscription_owner_name:
                return CustomerInfo(
                    email=user.get('email', 'unknown@unknown.com'),
                    name=subscription_owner_name,
                    subscription_id=namespace
                )
        
        # If user not found, return with known name but unknown email
        return CustomerInfo(
            email='unknown@unknown.com',
            name=subscription_owner_name,
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
            timeout=60,
            verify=False
        )
        
        # Check for authentication issues
        if response.status_code == 401:
            print("❌ ERROR: Authentication failed (401 Unauthorized)")
            raise ValueError(f"Authentication failed for VictoriaLogs. Check VMAUTH_USERNAME and VMAUTH_PASSWORD.")
        elif response.status_code == 403:
            print("❌ ERROR: Access forbidden (403 Forbidden)")
            raise ValueError(f"Access forbidden for VictoriaLogs. User '{self.auth[0]}' may lack permissions.")
        
        response.raise_for_status()
        
        # Parse response - VictoriaLogs returns newline-delimited JSON (NDJSON)
        logs = []
        
        for line in response.text.strip().split('\n'):
            if not line:
                continue
            try:
                data = json.loads(line)
                
                # VictoriaLogs returns _msg directly in each JSON object
                msg = data.get('_msg', '')
                if msg:
                    logs.append(msg)
            except json.JSONDecodeError as e:
                # Skip malformed lines
                print(f"Warning: Failed to parse log line: {e}", file=sys.stderr)
                continue
        
        result = '\n'.join(logs)
        if not result:
            print(f"DEBUG: Empty result. Response text length: {len(response.text)}")
            print(f"DEBUG: Full response text:\n{response.text}")
            raise ValueError("No logs retrieved from VictoriaLogs. Check query parameters and data availability.")
        return result


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
    
    def __init__(self, token: str, repo: str, project_id: str = None):
        self.token = token
        self.repo = repo  # format: "owner/repo"
        self.project_id = project_id  # format: "PVT_kwDOCfJmL84AqS92"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github.v3+json'
        })
        self.api_url = "https://api.github.com"
    
    def _ensure_label_exists(self, label: str):
        """Ensure a label exists in the repository, create if it doesn't"""
        # Check if label exists
        response = self.session.get(
            f"{self.api_url}/repos/{self.repo}/labels/{label}",
            timeout=30
        )
        
        if response.status_code == 404:
            # Label doesn't exist, create it
            # Use a default color based on label type
            color = "d73a4a"  # Red for crash/redis
            if label.startswith("customer:"):
                color = "0075ca"  # Blue for customer labels
            
            create_response = self.session.post(
                f"{self.api_url}/repos/{self.repo}/labels",
                json={"name": label, "color": color},
                timeout=30
            )
            
            if create_response.status_code == 201:
                print(f"Created label: {label}")
            elif create_response.status_code == 422:
                # Label was created by another process, ignore
                pass
            else:
                create_response.raise_for_status()
        elif response.status_code == 200:
            # Label exists, all good
            pass
        else:
            response.raise_for_status()
    
    def _add_issue_to_project(self, issue_node_id: str):
        """Add issue to GitHub project using GraphQL API"""
        print(f"DEBUG: _add_issue_to_project called with node_id: {issue_node_id}")
        print(f"DEBUG: self.project_id = {self.project_id}")
        
        if not self.project_id:
            print("DEBUG: No project_id configured, skipping project linking")
            return  # No project configured
        
        # GraphQL mutation to add issue to project
        mutation = """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item {
              id
            }
          }
        }
        """
        
        variables = {
            "projectId": self.project_id,
            "contentId": issue_node_id
        }
        
        response = self.session.post(
            "https://api.github.com/graphql",
            json={"query": mutation, "variables": variables},
            timeout=30
        )
        
        print(f"DEBUG: Project linking response status: {response.status_code}")
        print(f"DEBUG: Project linking response: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            if "errors" in result:
                print(f"❌ ERROR: Failed to add issue to project: {result['errors']}", file=sys.stderr)
            else:
                print(f"✅ Issue added to project {self.project_id}")
        else:
            print(f"❌ ERROR: Failed to add issue to project (HTTP {response.status_code}): {response.text}", file=sys.stderr)
    
    def find_duplicate(self, customer_email: str, namespace: str, crash: CrashSummary, hours: int = 24) -> Optional[int]:
        """Find duplicate issue for same customer, namespace, and crash signature"""
        # Calculate cutoff time (24 hours ago) - make it timezone-aware
        from datetime import timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        # Get all open issues for this customer and namespace
        params = {
            'state': 'open',
            'labels': f'customer:{customer_email},namespace:{namespace}',
            'per_page': 100
        }
        
        response = self.session.get(
            f"{self.api_url}/repos/{self.repo}/issues",
            params=params,
            timeout=30
        )
        response.raise_for_status()
        issues = response.json()
        
        current_sig = crash.signature
        
        for issue in issues:
            # Check if issue was created in last 24 hours
            try:
                created_at_str = issue['created_at'].replace('Z', '+00:00')
                created_at = datetime.fromisoformat(created_at_str)
            except (ValueError, KeyError) as e:
                print(f"Warning: Failed to parse issue creation date: {e}", file=sys.stderr)
                continue
            
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
**Time (UTC):** {timestamp}

### Crash Summary

**Stack Trace 1:** {crash.stack_traces[0]}
**Stack Trace 2:** {crash.stack_traces[1]}
**Stack Trace 3:** {crash.stack_traces[2]}
**Exit Code:** {crash.exit_code}
**Memory RSS:** {crash.memory_rss} bytes
**Client Command:** {crash.client_command}

**Crash Logs:** [View in Grafana]({log_url})"""
        
        # Ensure all labels exist before creating the issue
        labels = [
            f'customer:{customer.email}',
            f'namespace:{namespace}',
            'crash',
            'redis'
        ]
        for label in labels:
            self._ensure_label_exists(label)
        
        data = {
            'title': title,
            'body': body,
            'labels': labels
        }
        
        response = self.session.post(
            f"{self.api_url}/repos/{self.repo}/issues",
            json=data,
            timeout=30
        )
        response.raise_for_status()
        issue_data = response.json()
        issue_number = issue_data['number']
        issue_node_id = issue_data['node_id']
        
        print(f"DEBUG: Created issue #{issue_number} with node_id: {issue_node_id}")
        print(f"DEBUG: Project ID configured: {self.project_id}")
        
        # Add issue to project
        self._add_issue_to_project(issue_node_id)
        
        return issue_number
    
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

**Crash Logs:** [View in Grafana]({log_url})"""
        
        response = self.session.post(
            f"{self.api_url}/repos/{self.repo}/issues/{issue_number}/comments",
            json={'body': comment},
            timeout=30
        )
        response.raise_for_status()


class GrafanaLinkGenerator:
    """Generates Grafana log viewer links"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
    
    def generate_link(self, namespace: str, pod: str, container: str, minutes: int = 7) -> str:
        """Generate Grafana link with log query parameters"""
        # Calculate time range (7 minutes from now backwards)
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=minutes)
        
        # Convert to milliseconds for Grafana
        from_ms = int(start_time.timestamp() * 1000)
        to_ms = int(end_time.timestamp() * 1000)
        
        # Build query: namespace:instance-123 AND container:service AND pod:node-f-0
        query = f'namespace:{namespace} AND container:{container} AND pod:{pod}'
        
        # URL encode the query
        from urllib.parse import quote
        encoded_query = quote(query)
        
        # Construct Grafana explore URL
        grafana_url = f"{self.base_url}/explore?left=%5B%22{from_ms}%22,%22{to_ms}%22,%22Loki%22,%7B%22expr%22:%22{encoded_query}%22%7D%5D"
        
        return grafana_url


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
                                        "text": "View Logs in Grafana",
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
        
        response = requests.post(self.webhook_url, json=payload, timeout=30, verify=False)
        response.raise_for_status()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Process Redis crash alerts')
    parser.add_argument('--pod', required=True, help='Pod name')
    parser.add_argument('--namespace', required=True, help='Namespace')
    parser.add_argument('--cluster', required=True, help='Cluster name')
    parser.add_argument('--container', required=True, help='Container name')
    parser.add_argument('--vmauth-url', required=True, help='VMAuth URL for log collection')
    parser.add_argument('--grafana-url', required=True, help='Grafana URL for log viewing')
    
    args = parser.parse_args()
    
    # Validate required environment variables
    required_env_vars = [
        'OMNISTRATE_API_URL', 'OMNISTRATE_USERNAME', 'OMNISTRATE_PASSWORD',
        'OMNISTRATE_SERVICE_ID', 'OMNISTRATE_ENVIRONMENT_ID',
        'VMAUTH_USERNAME', 'VMAUTH_PASSWORD',
        'GITHUB_TOKEN', 'ISSUE_REPO',
        'GOOGLE_CHAT_WEBHOOK_URL'
    ]
    
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"❌ Error: Missing required environment variables: {', '.join(missing_vars)}", file=sys.stderr)
        sys.exit(1)
    
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
    project_id = os.environ.get('PROJECT_ID')  # Optional
    
    grafana_url = args.grafana_url
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
    
    # Step 4: Generate Grafana link
    print("Generating Grafana link...")
    grafana = GrafanaLinkGenerator(grafana_url)
    log_url = grafana.generate_link(args.namespace, args.pod, args.container, minutes=7)
    print(f"Grafana link: {log_url}")
    
    # Step 5: Check for duplicates
    print("Checking for duplicate issues...")
    github = GitHubIssueManager(github_token, issue_repo, project_id)
    duplicate_issue = github.find_duplicate(customer.email, args.namespace, crash)
    
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
    
    # Step 6: Send notification (only for new crashes, not duplicates)
    if not is_duplicate:
        print("Sending Google Chat notification...")
        notifier = GoogleChatNotifier(google_chat_webhook)
        notifier.send_notification(
            customer.email, args.cluster, args.pod, args.namespace,
            crash, issue_number, issue_repo, log_url, is_duplicate
        )
        print("Notification sent!")
    else:
        print("Skipping Google Chat notification for duplicate crash")
    
    # Output for GitHub Actions
    github_output = os.environ.get('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a') as f:
            f.write(f"issue_number={issue_number}\n")
            f.write(f"is_duplicate={str(is_duplicate).lower()}\n")
            f.write(f"customer_email={customer.email}\n")
    else:
        # Fallback for local testing or older GitHub Actions
        print(f"\nIssue: #{issue_number}, Duplicate: {is_duplicate}, Customer: {customer.email}")
    
    print("\n✅ Crash handling complete!")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
