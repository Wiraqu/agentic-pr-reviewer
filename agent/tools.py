"""
Tool layer for external integrations: GitHub, Jira, Linear, Semgrep, Ruff.
Implements MCP (Model Context Protocol) pattern for agent tool access.
"""

import os
import re
import json
import subprocess
import tempfile
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from pathlib import Path

import requests
from github import Github, Auth
from github.PullRequest import PullRequest
from tenacity import retry, stop_after_attempt, wait_exponential


# ============================================================================
# GitHub Tool
# ============================================================================

@dataclass
class PRMetadata:
    number: int
    title: str
    description: str
    author: str
    branch: str
    base_branch: str
    diff: str
    files_changed: List[str]
    additions: int
    deletions: int
    commits: int


class GitHubTool:
    """
    MCP-style tool for GitHub PR operations.
    Read-only: agents can inspect but never modify repository state.
    """
    
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError("GITHUB_TOKEN not provided")
        auth = Auth.Token(self.token)
        self.client = Github(auth=auth, per_page=100)
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch_pr(self, repo_name: str, pr_number: int) -> PRMetadata:
        """Fetch complete PR metadata including diff."""
        try:
            repo = self.client.get_repo(repo_name)
            pr: PullRequest = repo.get_pull(pr_number)
            
            # Fetch diff
            diff_url = pr.diff_url
            headers = {
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3.diff"
            }
            diff_response = requests.get(diff_url, headers=headers, timeout=30)
            diff_response.raise_for_status()
            diff = diff_response.text
            
            # Extract changed files
            files = [f.filename for f in pr.get_files()]
            
            return PRMetadata(
                number=pr.number,
                title=pr.title,
                description=pr.body or "",
                author=pr.user.login,
                branch=pr.head.ref,
                base_branch=pr.base.ref,
                diff=diff,
                files_changed=files,
                additions=pr.additions,
                deletions=pr.deletions,
                commits=pr.commits
            )
        except Exception as e:
            raise RuntimeError(f"Failed to fetch PR #{pr_number}: {e}")
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def post_comment(self, repo_name: str, pr_number: int, body: str) -> None:
        """Post review comment to PR. Agents CANNOT call this directly."""
        # This is reserved for the entrypoint, not for agent tools
        repo = self.client.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        pr.create_issue_comment(body)
    
    def extract_ticket_refs(self, text: str) -> List[str]:
        """Extract Jira/Linear ticket references from text."""
        # Jira: PROJ-123, Linear: TEAM-123, GitHub: #123
        patterns = [
            r'([A-Z][A-Z0-9]{1,9}-\d+)',  # Jira/Linear
            r'(?:fixes|closes|resolves|related to)[\s:#]*(\d+)',  # GitHub issues
        ]
        refs = []
        for pattern in patterns:
            refs.extend(re.findall(pattern, text, re.IGNORECASE))
        return list(dict.fromkeys(refs))  # deduplicate preserve order


# ============================================================================
# Jira Tool
# ============================================================================

class JiraTool:
    """MCP-style tool for Jira ticket retrieval."""
    
    def __init__(self):
        self.base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        self.email = os.getenv("JIRA_EMAIL", "")
        self.token = os.getenv("JIRA_API_TOKEN", "")
        self.enabled = all([self.base_url, self.email, self.token])
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Fetch ticket details from Jira."""
        if not self.enabled:
            return None
        
        url = f"{self.base_url}/rest/api/2/issue/{ticket_id}"
        auth = (self.email, self.token)
        headers = {"Accept": "application/json"}
        
        try:
            response = requests.get(url, auth=auth, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            return {
                "id": data["key"],
                "summary": data["fields"].get("summary", ""),
                "description": data["fields"].get("description", ""),
                "status": data["fields"].get("status", {}).get("name", ""),
                "issue_type": data["fields"].get("issuetype", {}).get("name", ""),
                "acceptance_criteria": self._extract_ac(data["fields"].get("description", "")),
                "labels": data["fields"].get("labels", []),
            }
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise
    
    def _extract_ac(self, description: str) -> List[str]:
        """Extract acceptance criteria from description."""
        if not description:
            return []
        # Look for AC sections
        ac_pattern = r'(?:acceptance criteria|criteria|AC)[:\s]*(.+?)(?=\n\n|\Z)'
        matches = re.findall(ac_pattern, description, re.IGNORECASE | re.DOTALL)
        return [m.strip() for m in matches]


# ============================================================================
# Linear Tool
# ============================================================================

class LinearTool:
    """MCP-style tool for Linear issue retrieval."""
    
    def __init__(self):
        self.api_key = os.getenv("LINEAR_API_KEY", "")
        self.enabled = bool(self.api_key)
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch_issue(self, issue_id: str) -> Optional[Dict[str, Any]]:
        """Fetch issue details from Linear."""
        if not self.enabled:
            return None
        
        query = """
        query Issue($id: String!) {
            issue(id: $id) {
                id
                identifier
                title
                description
                state { name }
                labels { nodes { name } }
            }
        }
        """
        
        response = requests.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": self.api_key, "Content-Type": "application/json"},
            json={"query": query, "variables": {"id": issue_id}},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        
        if "errors" in data:
            return None
            
        issue = data["data"]["issue"]
        return {
            "id": issue["identifier"],
            "title": issue["title"],
            "description": issue.get("description", ""),
            "status": issue["state"]["name"],
            "labels": [l["name"] for l in issue["labels"]["nodes"]],
        }


# ============================================================================
# Semgrep Tool (Security)
# ============================================================================

class SemgrepTool:
    """
    Static analysis security scanner.
    Runs in isolated temp directory to prevent agent from accessing filesystem.
    """
    
    def __init__(self, rules: Optional[str] = None):
        self.rules = rules or os.getenv("SEMGREP_RULES", "p/python,security-audit")
    
    def scan_diff(self, diff_text: str, files_changed: List[str]) -> List[Dict[str, Any]]:
        """
        Run Semgrep on PR diff.
        Returns structured findings for the Security Agent.
        """
        with tempfile.TemporaryDirectory(prefix="intentguard_semgrep_") as tmpdir:
            # Write diff to temp file for semgrep to analyze
            diff_path = Path(tmpdir) / "pr.diff"
            diff_path.write_text(diff_text, encoding="utf-8")
            
            # Create minimal file structure from diff
            self._reconstruct_files_from_diff(diff_text, Path(tmpdir))
            
            cmd = [
                "semgrep",
                "--config", self.rules,
                "--json",
                "--quiet",
                "--max-target-bytes", "1000000",
                str(tmpdir)
            ]
            
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                # Semgrep exits 1 when findings exist, 0 when clean
                if result.returncode not in (0, 1):
                    return [{"error": f"Semgrep failed: {result.stderr}"}]
                
                output = json.loads(result.stdout)
                findings = []
                for match in output.get("results", []):
                    findings.append({
                        "rule_id": match.get("check_id", "unknown"),
                        "message": match.get("extra", {}).get("message", ""),
                        "severity": match.get("extra", {}).get("metadata", {}).get("severity", "medium"),
                        "file": match.get("path", ""),
                        "line": match.get("start", {}).get("line", 0),
                        "code": match.get("extra", {}).get("lines", ""),
                    })
                return findings
                
            except subprocess.TimeoutExpired:
                return [{"error": "Semgrep scan timed out after 120s"}]
            except FileNotFoundError:
                return [{"error": "Semgrep not installed. Install with: pip install semgrep"}]
            except json.JSONDecodeError:
                return [{"error": "Failed to parse Semgrep output"}]
    
    def _reconstruct_files_from_diff(self, diff_text: str, base_path: Path) -> None:
        """Reconstruct file content from unified diff for scanning."""
        # Simple reconstruction: extract added lines
        current_file = None
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                file_path = base_path / current_file
                file_path.parent.mkdir(parents=True, exist_ok=True)
            elif line.startswith("+") and not line.startswith("+++") and current_file:
                file_path = base_path / current_file
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(line[1:] + "\n")


# ============================================================================
# Ruff Tool (Quality)
# ============================================================================

class RuffTool:
    """Python code quality and style checker."""
    
    def check_code(self, diff_text: str) -> List[Dict[str, Any]]:
        """Run Ruff on extracted code from diff."""
        with tempfile.TemporaryDirectory(prefix="intentguard_ruff_") as tmpdir:
            self._reconstruct_files_from_diff(diff_text, Path(tmpdir))
            
            findings = []
            
            # Run ruff check
            try:
                check_result = subprocess.run(
                    ["ruff", "check", str(tmpdir), "--output-format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if check_result.stdout:
                    for item in json.loads(check_result.stdout):
                        findings.append({
                            "tool": "ruff-check",
                            "code": item.get("code", ""),
                            "message": item.get("message", ""),
                            "severity": "medium" if item.get("code", "").startswith("E") else "low",
                            "file": item.get("filename", ""),
                            "line": item.get("location", {}).get("row", 0),
                        })
            except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
                pass
            
            # Run ruff format --check
            try:
                fmt_result = subprocess.run(
                    ["ruff", "format", "--check", str(tmpdir), "--diff"],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if fmt_result.returncode != 0 and fmt_result.stdout:
                    findings.append({
                        "tool": "ruff-format",
                        "code": "FORMAT",
                        "message": "Code formatting issues detected",
                        "severity": "low",
                        "file": "multiple",
                        "line": 0,
                        "diff": fmt_result.stdout,
                    })
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            
            return findings
    
    def _reconstruct_files_from_diff(self, diff_text: str, base_path: Path) -> None:
        """Reconstruct files from diff for Ruff analysis."""
        current_file = None
        content_lines = []
        
        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                if current_file and content_lines:
                    self._write_file(base_path, current_file, content_lines)
                current_file = line[6:]
                content_lines = []
            elif line.startswith("+") and not line.startswith("+++"):
                content_lines.append(line[1:])
        
        if current_file and content_lines:
            self._write_file(base_path, current_file, content_lines)
    
    def _write_file(self, base_path: Path, file_name: str, lines: List[str]) -> None:
        file_path = base_path / file_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
