"""Gitea backend — overrides GitHubBackend where Gitea's API diverges."""

import base64
import json
import logging
import re
from datetime import datetime, timezone

import httpx

from backend.app.services.git_providers.github import GitHubBackend

logger = logging.getLogger(__name__)


class GiteaBackend(GitHubBackend):
    """Backend for Gitea instances.

    Gitea's Git Data API (/api/v1/repos/{owner}/{repo}/git/...) is *mostly*
    compatible with GitHub's, but diverges on two points that broke real-world
    backups (#1224, #1225):

    1. ``GET /git/refs/heads/{branch}`` returns a *list* of matching refs even
       when only one matches; GitHub returns a single object. The push paths
       below extract the SHA via ``_ref_sha()`` instead of the GitHub-style
       ``["object"]["sha"]`` chain.

    2. The Git Data API (blobs/trees/commits/refs) refuses writes against an
       empty repository — every blob POST returns 404 until the repo has at
       least one commit. ``_create_initial_commit()`` is overridden to use the
       Contents API, which seeds the branch + initial commit in a single call.
    """

    @staticmethod
    def _ref_sha(ref_data) -> str:
        """Extract the commit SHA from Gitea's list-shaped ref response."""
        if isinstance(ref_data, list):
            if not ref_data:
                raise ValueError("Empty refs list returned by Gitea API")
            return ref_data[0]["object"]["sha"]
        return ref_data["object"]["sha"]

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
        """Push files via the Git Data API, normalising Gitea's list-shaped ref response."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            ref_response = await client.get(f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{branch}", headers=headers)

            if ref_response.status_code == 404:
                return await self._create_branch_and_push(
                    client, headers, api_base, owner, repo, branch, files, repo_url, token
                )

            if ref_response.status_code != 200:
                return {
                    "status": "failed",
                    "message": f"Failed to get branch ref: {ref_response.status_code}",
                    "error": self._truncated_response_text(ref_response),
                }

            current_commit_sha = self._ref_sha(ref_response.json())

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
                content_sha = self._blob_sha(content_bytes)

                if path in existing_files and existing_files[path] == content_sha:
                    continue

                blob_response = await client.post(
                    f"{api_base}/repos/{owner}/{repo}/git/blobs",
                    headers=headers,
                    json={"content": base64.b64encode(content_bytes).decode(), "encoding": "base64"},
                )
                if blob_response.status_code != 201:
                    logger.error("Failed to create blob for %s: %s", path, self._truncated_response_text(blob_response))
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
                return {
                    "status": "failed",
                    "message": f"Failed to create tree: {self._truncated_response_text(tree_response)}",
                }

            new_tree_sha = tree_response.json()["sha"]
            commit_message = f"Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            commit_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={"message": commit_message, "tree": new_tree_sha, "parents": [current_commit_sha]},
            )
            if commit_response.status_code != 201:
                return {
                    "status": "failed",
                    "message": f"Failed to create commit: {self._truncated_response_text(commit_response)}",
                }

            new_commit_sha = commit_response.json()["sha"]

            ref_update = await client.patch(
                f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{branch}",
                headers=headers,
                json={"sha": new_commit_sha},
            )
            if ref_update.status_code != 200:
                return {
                    "status": "failed",
                    "message": f"Failed to update branch: {self._truncated_response_text(ref_update)}",
                }

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

            base_sha = self._ref_sha(ref_response.json())

            create_ref = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )
            if create_ref.status_code != 201:
                return {
                    "status": "failed",
                    "message": f"Failed to create branch: {self._truncated_response_text(create_ref)}",
                }

            return await self.push_files(repo_url, token, branch, files, client)

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
        """Seed an empty Gitea repository via the Contents API.

        Gitea's Git Data API requires the repository to have at least one
        commit before it accepts blob/tree/commit writes; on an empty repo
        every ``POST /git/blobs`` returns 404. The Contents API is the
        documented bootstrap path: a single ``POST /repos/{owner}/{repo}/contents``
        with a ``files`` array creates the initial commit and the target
        branch in one round-trip (Gitea 1.18+, Forgejo all versions).
        """
        try:
            if not files:
                return {"status": "skipped", "message": "No files to commit", "commit_sha": None, "files_changed": 0}

            api_files = []
            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                content_b64 = base64.b64encode(content_str.encode("utf-8")).decode()
                api_files.append({"operation": "create", "path": path, "content": content_b64})

            commit_message = f"Initial Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            body = {
                "branch": branch,
                "new_branch": branch,
                "message": commit_message,
                "files": api_files,
            }

            response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/contents",
                headers=headers,
                json=body,
            )

            if response.status_code not in (200, 201):
                return {
                    "status": "failed",
                    "message": f"Failed to create initial commit: {self._truncated_response_text(response)}",
                }

            data = response.json()
            commit_sha = (data.get("commit") or {}).get("sha")
            return {
                "status": "success",
                "message": f"Initial backup created - {len(files)} files",
                "commit_sha": commit_sha,
                "files_changed": len(files),
            }

        except Exception as e:
            logger.error("Gitea initial commit failed: %s", e)
            return {"status": "failed", "message": str(e), "error": str(e)}
