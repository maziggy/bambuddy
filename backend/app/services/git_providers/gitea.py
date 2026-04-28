"""Gitea backend using the Gitea repository contents API."""

import base64
import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone

import httpx

from backend.app.services.git_providers.github import GitHubBackend

logger = logging.getLogger(__name__)


class GiteaBackend(GitHubBackend):
    """Backend for Gitea instances.

    Gitea exposes GitHub-like routes, but some responses differ from GitHub's
    Git Data API. Use Gitea's branch and contents endpoints for writes.
    """

    def parse_repo_url(self, url: str) -> tuple[str, str]:
        """Return (owner, repo) — accepts both https:// and http:// for self-hosted instances."""
        if not url or len(url) > 500:
            raise ValueError("Invalid Git URL: URL too long or empty")
        match = re.match(
            r"https?://[\w.\-]+(:\d+)?/([\w.\-]{1,100})/([\w.\-]{1,100})(?:\.git)?/?$",
            url,
        )
        if match:
            return match.group(2), match.group(3).removesuffix(".git")
        match = re.match(
            r"git@[\w.\-]+:([\w.\-]{1,100})/([\w.\-]{1,100})(?:\.git)?$",
            url,
        )
        if match:
            return match.group(1), match.group(2).removesuffix(".git")
        raise ValueError(f"Cannot parse repository URL: {url}")

    def get_api_base(self, repo_url: str) -> str:
        """Derive API base from the repository URL's scheme and host."""
        match = re.match(r"(https?://[\w.\-]+(:\d+)?)/", repo_url)
        if match:
            return f"{match.group(1)}/api/v1"
        raise ValueError(f"Cannot derive API base from URL: {repo_url}")

    def get_headers(self, token: str) -> dict:
        headers = super().get_headers(token)
        headers["Accept"] = "application/json"
        return headers

    async def push_files(
        self,
        repo_url: str,
        token: str,
        branch: str,
        files: dict,
        client: httpx.AsyncClient,
    ) -> dict:
        """Push files using Gitea's contents API."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            branch_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/branches/{urllib.parse.quote(branch, safe='')}",
                headers=headers,
            )
            if branch_response.status_code == 404:
                create_result = await self._create_branch(client, headers, api_base, owner, repo, branch)
                if create_result is not None:
                    return create_result
            elif branch_response.status_code != 200:
                return {
                    "status": "failed",
                    "message": f"Failed to check branch: {branch_response.status_code}",
                    "error": branch_response.text,
                }

            files_changed = 0
            last_commit_sha = None
            commit_message = f"Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"

            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                content_bytes = content_str.encode("utf-8")
                content_sha = self._blob_sha(content_bytes)
                encoded_path = urllib.parse.quote(path, safe="/")

                current_response = await client.get(
                    f"{api_base}/repos/{owner}/{repo}/contents/{encoded_path}",
                    headers=headers,
                    params={"ref": branch},
                )

                existing_sha = None
                if current_response.status_code == 200:
                    current_data = current_response.json()
                    if isinstance(current_data, list):
                        return {
                            "status": "failed",
                            "message": f"Expected file but found directory at {path}",
                            "error": current_response.text,
                        }
                    existing_sha = current_data.get("sha")
                    if existing_sha == content_sha:
                        continue
                elif current_response.status_code != 404:
                    return {
                        "status": "failed",
                        "message": f"Failed to inspect {path}: {current_response.status_code}",
                        "error": current_response.text,
                    }

                payload = {
                    "branch": branch,
                    "content": base64.b64encode(content_bytes).decode(),
                    "message": commit_message,
                }
                if existing_sha:
                    payload["sha"] = existing_sha
                    write_response = await client.put(
                        f"{api_base}/repos/{owner}/{repo}/contents/{encoded_path}",
                        headers=headers,
                        json=payload,
                    )
                    expected_status = 200
                else:
                    write_response = await client.post(
                        f"{api_base}/repos/{owner}/{repo}/contents/{encoded_path}",
                        headers=headers,
                        json=payload,
                    )
                    expected_status = 201

                if write_response.status_code != expected_status:
                    return {
                        "status": "failed",
                        "message": f"Failed to write {path}: {write_response.text}",
                        "error": write_response.text,
                    }

                files_changed += 1
                last_commit_sha = self._extract_commit_sha(write_response.json())

            if files_changed == 0:
                return {"status": "skipped", "message": "No changes to commit", "commit_sha": None, "files_changed": 0}

            return {
                "status": "success",
                "message": f"Backup successful - {files_changed} files updated",
                "commit_sha": last_commit_sha,
                "files_changed": files_changed,
            }
        except Exception as e:
            logger.error("Push to Gitea failed: %s", e)
            return {"status": "failed", "message": str(e), "error": str(e)}

    async def _create_branch(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        owner: str,
        repo: str,
        branch: str,
    ) -> dict | None:
        """Create the backup branch from the repository default branch.

        Returns a failure result when branch creation fails, otherwise None.
        """
        repo_response = await client.get(f"{api_base}/repos/{owner}/{repo}", headers=headers)
        if repo_response.status_code != 200:
            return {"status": "failed", "message": "Failed to get repo info", "error": repo_response.text}

        default_branch = repo_response.json().get("default_branch")
        if not default_branch:
            return {"status": "failed", "message": "Repository has no default branch"}
        if default_branch == branch:
            return None

        create_response = await client.post(
            f"{api_base}/repos/{owner}/{repo}/branches",
            headers=headers,
            json={"new_branch_name": branch, "old_branch_name": default_branch},
        )
        if create_response.status_code not in (200, 201, 409):
            return {"status": "failed", "message": f"Failed to create branch: {create_response.text}"}
        return None

    @staticmethod
    def _extract_commit_sha(response_data: dict) -> str | None:
        commit = response_data.get("commit") if isinstance(response_data, dict) else None
        if isinstance(commit, dict):
            return commit.get("sha") or commit.get("id")
        return None
