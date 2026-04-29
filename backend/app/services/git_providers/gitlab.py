"""GitLab backend — implements GitProviderBackend using the GitLab REST API v4."""

import base64
import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone

import httpx

from backend.app.services.git_providers.base import GitProviderBackend

logger = logging.getLogger(__name__)


class GitLabBackend(GitProviderBackend):
    """Backend for gitlab.com and self-hosted GitLab instances."""

    def get_api_base(self, repo_url: str) -> str:
        match = re.match(r"(https?://[\w.\-]+(:\d+)?)/", repo_url)
        if not match:
            raise ValueError(f"Cannot derive API base from URL: {repo_url}")
        return f"{match.group(1)}/api/v4"

    def get_headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def parse_repo_url(self, url: str) -> tuple[str, str]:
        """Return (namespace, repo) from HTTPS or SSH URL.

        namespace may include subgroups, e.g. 'group/subgroup' for
        gitlab.com/group/subgroup/project. Callers join them with '/' and
        URL-encode the result for /api/v4/projects/{encoded_path}.
        """
        if not url or len(url) > 500:
            raise ValueError("Invalid Git URL: URL too long or empty")
        match = re.match(r"https?://[\w.\-]+(:\d+)?/(.+?)(?:\.git)?/?$", url)
        if match:
            full_path = match.group(2)
            if "/" not in full_path:
                raise ValueError(f"Cannot parse repository URL: {url}")
            namespace, _, repo = full_path.rpartition("/")
            return namespace, repo
        match = re.match(r"git@[\w.\-]+:(.+?)(?:\.git)?$", url)
        if match:
            full_path = match.group(1)
            if "/" not in full_path:
                raise ValueError(f"Cannot parse repository URL: {url}")
            namespace, _, repo = full_path.rpartition("/")
            return namespace, repo
        raise ValueError(f"Cannot parse repository URL: {url}")

    async def test_connection(self, repo_url: str, token: str, client: httpx.AsyncClient) -> dict:
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)
            encoded_path = urllib.parse.quote(f"{owner}/{repo}", safe="")

            response = await client.get(f"{api_base}/projects/{encoded_path}", headers=headers)

            if response.status_code == 401:
                return {"success": False, "message": "Invalid access token", "repo_name": None, "permissions": None}
            if response.status_code == 404:
                return {
                    "success": False,
                    "message": "Repository not found. Check URL and token permissions.",
                    "repo_name": None,
                    "permissions": None,
                }
            if response.status_code != 200:
                return {
                    "success": False,
                    "message": f"API error: {response.status_code}",
                    "repo_name": None,
                    "permissions": None,
                }

            data = response.json()
            perms = data.get("permissions") or {}
            project_level = (perms.get("project_access") or {}).get("access_level", 0)
            group_level = (perms.get("group_access") or {}).get("access_level", 0)
            effective = max(project_level, group_level)

            if effective < 30:  # Developer = 30, Maintainer = 40, Owner = 50
                return {
                    "success": False,
                    "message": "Token requires Developer access or higher to push",
                    "repo_name": data.get("name_with_namespace"),
                    "permissions": perms,
                }

            return {
                "success": True,
                "message": "Connection successful",
                "repo_name": data.get("name_with_namespace"),
                "permissions": perms,
            }
        except Exception as e:
            logger.error("GitLab connection test failed: %s", e)
            return {
                "success": False,
                "message": f"Connection failed: {type(e).__name__}",
                "repo_name": None,
                "permissions": None,
            }

    async def push_files(
        self,
        repo_url: str,
        token: str,
        branch: str,
        files: dict,
        client: httpx.AsyncClient,
    ) -> dict:
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)
            encoded_path = urllib.parse.quote(f"{owner}/{repo}", safe="")

            encoded_branch = urllib.parse.quote(branch, safe="")
            branch_response = await client.get(
                f"{api_base}/projects/{encoded_path}/repository/branches/{encoded_branch}",
                headers=headers,
            )

            if branch_response.status_code == 404:
                proj_response = await client.get(f"{api_base}/projects/{encoded_path}", headers=headers)
                if proj_response.status_code != 200:
                    return {"status": "failed", "message": "Failed to get project info"}

                default_branch = proj_response.json().get("default_branch", "main")
                default_encoded = urllib.parse.quote(default_branch, safe="")
                default_response = await client.get(
                    f"{api_base}/projects/{encoded_path}/repository/branches/{default_encoded}",
                    headers=headers,
                )

                if default_response.status_code != 200:
                    return await self._create_initial_commit(client, headers, api_base, encoded_path, branch, files)

                create_response = await client.post(
                    f"{api_base}/projects/{encoded_path}/repository/branches",
                    headers=headers,
                    json={"branch": branch, "ref": default_branch},
                )
                if create_response.status_code not in (200, 201):
                    return {"status": "failed", "message": f"Failed to create branch: {create_response.status_code}"}
            elif branch_response.status_code != 200:
                return {"status": "failed", "message": f"Failed to check branch: {branch_response.status_code}"}

            existing_blobs: dict[str, str] = {}
            page = 1
            while True:
                tree_response = await client.get(
                    f"{api_base}/projects/{encoded_path}/repository/tree",
                    headers=headers,
                    params={"recursive": "true", "ref": branch, "per_page": 100, "page": page},
                )
                if tree_response.status_code != 200:
                    break
                items = tree_response.json()
                if not items:
                    break
                for item in items:
                    if item.get("type") == "blob":
                        existing_blobs[item["path"]] = item["id"]
                page += 1

            actions = []
            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                content_bytes = content_str.encode("utf-8")
                content_sha = self._blob_sha(content_bytes)

                if path in existing_blobs and existing_blobs[path] == content_sha:
                    continue

                actions.append(
                    {
                        "action": "update" if path in existing_blobs else "create",
                        "file_path": path,
                        "content": base64.b64encode(content_bytes).decode(),
                        "encoding": "base64",
                    }
                )

            if not actions:
                return {"status": "skipped", "message": "No changes to commit", "commit_sha": None, "files_changed": 0}

            commit_message = f"Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            commit_response = await client.post(
                f"{api_base}/projects/{encoded_path}/repository/commits",
                headers=headers,
                json={"branch": branch, "commit_message": commit_message, "actions": actions},
            )
            if commit_response.status_code not in (200, 201):
                return {
                    "status": "failed",
                    "message": f"Failed to create commit: {self._truncated_response_text(commit_response)}",
                }

            return {
                "status": "success",
                "message": f"Backup successful - {len(actions)} files updated",
                "commit_sha": commit_response.json().get("id"),
                "files_changed": len(actions),
            }
        except Exception as e:
            logger.error("Push to GitLab failed: %s", e)
            return {"status": "failed", "message": str(e), "error": str(e)}

    async def _create_initial_commit(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        encoded_path: str,
        branch: str,
        files: dict,
    ) -> dict:
        """Create the first commit in an empty repository."""
        try:
            actions = []
            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                actions.append(
                    {
                        "action": "create",
                        "file_path": path,
                        "content": base64.b64encode(content_str.encode()).decode(),
                        "encoding": "base64",
                    }
                )

            commit_message = f"Initial Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            commit_response = await client.post(
                f"{api_base}/projects/{encoded_path}/repository/commits",
                headers=headers,
                json={"branch": branch, "commit_message": commit_message, "actions": actions, "start_branch": branch},
            )
            if commit_response.status_code not in (200, 201):
                return {
                    "status": "failed",
                    "message": f"Failed to create initial commit: {self._truncated_response_text(commit_response)}",
                }

            return {
                "status": "success",
                "message": f"Initial backup created - {len(files)} files",
                "commit_sha": commit_response.json().get("id"),
                "files_changed": len(files),
            }
        except Exception as e:
            return {"status": "failed", "message": str(e)}
