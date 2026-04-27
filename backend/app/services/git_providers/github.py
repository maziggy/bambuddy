"""GitHub backend — implements GitProviderBackend using the GitHub Git Data API."""

import base64
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

import httpx

from backend.app.services.git_providers.base import GitProviderBackend

logger = logging.getLogger(__name__)


class GitHubBackend(GitProviderBackend):
    """Backend for github.com using the GitHub Git Data API."""

    def get_api_base(self, repo_url: str, api_base_url: str | None) -> str:
        return "https://api.github.com"

    def parse_repo_url(self, url: str) -> tuple[str, str]:
        """Return (owner, repo) from a GitHub HTTPS or SSH URL."""
        if not url or len(url) > 500:
            raise ValueError("Invalid Git URL: URL too long or empty")

        api_base = self.get_api_base(url, None)
        # Derive host from api_base for HTTPS matching
        host = api_base.replace("https://", "").replace("http://", "").split("/")[0]

        # HTTPS: https://<host>[:<port>]/<owner>/<repo>[.git][/]
        match = re.match(
            rf"https://{re.escape(host)}(?::\d+)?/([\w.\-]{{1,100}})/([\w.\-]{{1,100}})(?:\.git)?/?$",
            url,
        )
        if match:
            return match.group(1), match.group(2)

        # SSH: git@<host>:<owner>/<repo>[.git]
        match = re.match(
            rf"git@{re.escape(host)}:([\w.\-]{{1,100}})/([\w.\-]{{1,100}})(?:\.git)?$",
            url,
        )
        if match:
            return match.group(1), match.group(2)

        raise ValueError(f"Cannot parse repository URL: {url}")

    async def test_connection(self, repo_url: str, token: str, client: httpx.AsyncClient) -> dict:
        """Test API access and push permission for the repository."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url, None)
            headers = self.get_headers(token)

            response = await client.get(f"{api_base}/repos/{owner}/{repo}", headers=headers)

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
            permissions = data.get("permissions", {})

            if not permissions.get("push", False):
                return {
                    "success": False,
                    "message": "Token does not have push permission to this repository",
                    "repo_name": data.get("full_name"),
                    "permissions": permissions,
                }

            return {
                "success": True,
                "message": "Connection successful",
                "repo_name": data.get("full_name"),
                "permissions": permissions,
            }

        except Exception as e:
            logger.error("Git connection test failed: %s", e)
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
        api_base_url: str | None = None,
    ) -> dict:
        """Push files to the repository using the Git Data API."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url, api_base_url)
            headers = self.get_headers(token)

            ref_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{branch}", headers=headers
            )

            if ref_response.status_code == 404:
                return await self._create_branch_and_push(
                    client, headers, api_base, owner, repo, branch, files, repo_url, token, api_base_url
                )

            if ref_response.status_code != 200:
                return {
                    "status": "failed",
                    "message": f"Failed to get branch ref: {ref_response.status_code}",
                    "error": ref_response.text,
                }

            current_commit_sha = ref_response.json()["object"]["sha"]

            commit_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/git/commits/{current_commit_sha}", headers=headers
            )
            if commit_response.status_code != 200:
                return {"status": "failed", "message": "Failed to get current commit"}

            current_tree_sha = commit_response.json()["tree"]["sha"]

            tree_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/git/trees/{current_tree_sha}?recursive=1", headers=headers
            )
            existing_files: dict[str, str] = {}
            if tree_response.status_code == 200:
                for item in tree_response.json().get("tree", []):
                    if item["type"] == "blob":
                        existing_files[item["path"]] = item["sha"]

            tree_items = []
            files_changed = 0

            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                content_bytes = content_str.encode("utf-8")
                content_sha = hashlib.sha1(
                    f"blob {len(content_bytes)}\0".encode() + content_bytes, usedforsecurity=False
                ).hexdigest()

                if path in existing_files and existing_files[path] == content_sha:
                    continue

                blob_response = await client.post(
                    f"{api_base}/repos/{owner}/{repo}/git/blobs",
                    headers=headers,
                    json={"content": base64.b64encode(content_bytes).decode(), "encoding": "base64"},
                )
                if blob_response.status_code != 201:
                    logger.error("Failed to create blob for %s: %s", path, blob_response.text)
                    continue

                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_response.json()["sha"]})
                files_changed += 1

            if not tree_items:
                return {"status": "skipped", "message": "No changes to commit", "commit_sha": None, "files_changed": 0}

            tree_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"base_tree": current_tree_sha, "tree": tree_items},
            )
            if tree_response.status_code != 201:
                return {"status": "failed", "message": f"Failed to create tree: {tree_response.text}"}

            new_tree_sha = tree_response.json()["sha"]
            commit_message = f"Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            commit_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={"message": commit_message, "tree": new_tree_sha, "parents": [current_commit_sha]},
            )
            if commit_response.status_code != 201:
                return {"status": "failed", "message": f"Failed to create commit: {commit_response.text}"}

            new_commit_sha = commit_response.json()["sha"]

            ref_update = await client.patch(
                f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{branch}",
                headers=headers,
                json={"sha": new_commit_sha},
            )
            if ref_update.status_code != 200:
                return {"status": "failed", "message": f"Failed to update branch: {ref_update.text}"}

            return {
                "status": "success",
                "message": f"Backup successful - {files_changed} files updated",
                "commit_sha": new_commit_sha,
                "files_changed": files_changed,
            }

        except Exception as e:
            logger.error("Push to Git failed: %s", e)
            return {"status": "failed", "message": str(e), "error": str(e)}

    async def _create_branch_and_push(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        owner: str,
        repo: str,
        branch: str,
        files: dict,
        repo_url: str,
        token: str,
        api_base_url: str | None,
    ) -> dict:
        """Create branch (from default branch or as initial commit) then push."""
        try:
            repo_response = await client.get(f"{api_base}/repos/{owner}/{repo}", headers=headers)
            if repo_response.status_code != 200:
                return {"status": "failed", "message": "Failed to get repo info"}

            default_branch = repo_response.json().get("default_branch", "main")

            ref_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{default_branch}", headers=headers
            )
            if ref_response.status_code != 200:
                return await self._create_initial_commit(client, headers, api_base, owner, repo, branch, files)

            base_sha = ref_response.json()["object"]["sha"]

            create_ref = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )
            if create_ref.status_code != 201:
                return {"status": "failed", "message": f"Failed to create branch: {create_ref.text}"}

            return await self.push_files(repo_url, token, branch, files, client, api_base_url)

        except Exception as e:
            return {"status": "failed", "message": str(e)}

    async def _create_initial_commit(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        owner: str,
        repo: str,
        branch: str,
        files: dict,
    ) -> dict:
        """Create the first commit in an empty repository."""
        try:
            tree_items = []
            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                blob_response = await client.post(
                    f"{api_base}/repos/{owner}/{repo}/git/blobs",
                    headers=headers,
                    json={"content": base64.b64encode(content_str.encode()).decode(), "encoding": "base64"},
                )
                if blob_response.status_code == 201:
                    tree_items.append(
                        {"path": path, "mode": "100644", "type": "blob", "sha": blob_response.json()["sha"]}
                    )

            tree_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"tree": tree_items},
            )
            if tree_response.status_code != 201:
                return {"status": "failed", "message": "Failed to create tree"}

            tree_sha = tree_response.json()["sha"]
            commit_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={
                    "message": f"Initial Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    "tree": tree_sha,
                },
            )
            if commit_response.status_code != 201:
                return {"status": "failed", "message": "Failed to create commit"}

            commit_sha = commit_response.json()["sha"]
            ref_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
            )
            if ref_response.status_code != 201:
                return {"status": "failed", "message": "Failed to create branch ref"}

            return {
                "status": "success",
                "message": f"Initial backup created - {len(files)} files",
                "commit_sha": commit_sha,
                "files_changed": len(files),
            }

        except Exception as e:
            return {"status": "failed", "message": str(e)}
