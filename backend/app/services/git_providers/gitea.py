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
    compatible with GitHub's, but diverges on three points that broke real-world
    backups (#1224, #1225, #1239):

    1. ``GET /git/refs/heads/{branch}`` returns a *list* of matching refs even
       when only one matches; GitHub returns a single object. The push paths
       below extract the SHA via ``_ref_sha()`` instead of the GitHub-style
       ``["object"]["sha"]`` chain.

    2. The Git Data API (blobs/trees/commits/refs) refuses writes against an
       empty repository — every blob POST returns 404 until the repo has at
       least one commit. ``_create_initial_commit()`` is overridden to use the
       Contents API, which seeds the branch + initial commit in a single call.

    3. The Git Data API does not support atomic multi-file commits — each file
       requires a separate blob POST followed by a tree/commit/ref sequence.
       ``push_files()`` is overridden to use the Contents API
       (``POST /repos/.../contents`` with a ``files`` array), which commits all
       changed files in a single round-trip and avoids partial-commit failures.
    """

    @staticmethod
    def _ref_sha(ref_data) -> str:
        """Extract the commit SHA from Gitea's list-shaped ref response."""
        if isinstance(ref_data, list):
            if not ref_data:
                raise ValueError("Empty refs list returned by Gitea API")
            return ref_data[0]["object"]["sha"]
        return ref_data["object"]["sha"]

    @staticmethod
    def _commit_tree_sha(commit_data: dict) -> str | None:
        """Extract the tree SHA from a commit response.

        GitHub's ``GET /git/commits/{sha}`` returns the GitCommit schema with
        ``tree`` at the top level. Gitea's same-named endpoint may return the
        wrapped Commit schema where ``tree`` lives under ``commit``. Try the
        flat shape first (GitHub-compatible deployments and some Gitea/Forgejo
        versions) then fall back to the wrapped shape.
        """
        tree_node = commit_data.get("tree")
        if not isinstance(tree_node, dict):
            tree_node = (commit_data.get("commit") or {}).get("tree")
        if isinstance(tree_node, dict):
            return tree_node.get("sha")
        return None

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
        _allow_branch_create: bool = True,
    ) -> dict:
        """Push files via the Git Data API, normalising Gitea's list-shaped ref response."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            ref_response = await client.get(f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{branch}", headers=headers)

            if ref_response.status_code == 404:
                if not _allow_branch_create:
                    return {
                        "status": "failed",
                        "message": (
                            f"Branch '{branch}' not found after creation — possible replication lag. "
                            "The next scheduled backup will retry."
                        ),
                    }
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
                msg = f"Failed to get current commit (HTTP {commit_response.status_code}): {self._truncated_response_text(commit_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            current_tree_sha = self._commit_tree_sha(commit_response.json())
            if not current_tree_sha:
                msg = (
                    f"Failed to extract tree SHA from commit response: {self._truncated_response_text(commit_response)}"
                )
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            tree_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/git/trees/{current_tree_sha}?recursive=1", headers=headers
            )
            if tree_response.status_code != 200:
                msg = f"Failed to list existing tree (HTTP {tree_response.status_code}): {self._truncated_response_text(tree_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg, "error": self._truncated_response_text(tree_response)}
            tree_data = tree_response.json()
            # Gitea's tree API can report ``truncated: true`` for large
            # listings; if we honour the partial map, the dedup check misses
            # and every file gets re-uploaded each run.
            if tree_data.get("truncated"):
                msg = (
                    "Repository tree exceeds the Gitea API listing limit (truncated=true). "
                    "Rotate the backup repository to avoid silent file-by-file churn on every backup."
                )
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}
            existing_files: dict[str, str] = {}
            for item in tree_data.get("tree", []):
                if item.get("type") != "blob":
                    continue
                path, sha = item.get("path"), item.get("sha")
                if not path or not sha:
                    logger.warning("push_files: skipping malformed tree entry: %s", item)
                    continue
                existing_files[path] = sha

            api_files = []
            files_changed = 0

            for path, content in files.items():
                content_str = json.dumps(content, indent=2, default=str)
                content_bytes = content_str.encode("utf-8")
                content_b64 = base64.b64encode(content_bytes).decode()
                content_sha = self._blob_sha(content_bytes)

                if path in existing_files:
                    if existing_files[path] == content_sha:
                        continue
                    api_files.append(
                        {"operation": "update", "path": path, "content": content_b64, "sha": existing_files[path]}
                    )
                else:
                    api_files.append({"operation": "create", "path": path, "content": content_b64})
                files_changed += 1

            if not api_files:
                return {"status": "skipped", "message": "No changes to commit", "commit_sha": None, "files_changed": 0}

            commit_message = f"Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/contents",
                headers=headers,
                json={"branch": branch, "message": commit_message, "files": api_files},
            )

            if response.status_code == 404:
                return {
                    "status": "failed",
                    "message": "Contents API endpoint not found — your Gitea instance may be older than v1.18 or the API may be disabled by an administrator (POST /contents returned 404)",
                }
            if response.status_code == 409:
                return {
                    "status": "failed",
                    "message": (
                        "Conflict committing files — the branch likely advanced concurrently "
                        "(web-UI edit, another backup run, or path-vs-tree collision). "
                        "The next scheduled backup will re-read the current tree and resolve this."
                    ),
                }
            if response.status_code not in (200, 201):
                return {
                    "status": "failed",
                    "message": f"Backup commit failed: {self._truncated_response_text(response)}",
                }

            commit_sha = (response.json().get("commit") or {}).get("sha")
            message = (
                f"Backup successful - {files_changed} files updated"
                if commit_sha
                else f"Backup successful - {files_changed} files updated (commit SHA not reported by server)"
            )
            return {
                "status": "success",
                "message": message,
                "commit_sha": commit_sha,
                "files_changed": files_changed,
            }

        except Exception as e:
            logger.exception("push_files failed for %s branch=%s", repo_url, branch)
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
                msg = f"Failed to get repo info (HTTP {repo_response.status_code}): {self._truncated_response_text(repo_response)}"
                logger.warning("_create_branch_and_push %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            default_branch = repo_response.json().get("default_branch", "main")

            # GET the default branch to confirm the repo is non-empty; SHA is intentionally unused —
            # POST /branches takes a branch name, not a SHA.
            ref_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{default_branch}", headers=headers
            )
            if ref_response.status_code != 200:
                return await self._create_initial_commit(client, headers, api_base, owner, repo, branch, files)

            create_ref = await client.post(
                f"{api_base}/repos/{owner}/{repo}/branches",
                headers=headers,
                json={"new_branch_name": branch, "old_ref_name": default_branch},
            )
            if create_ref.status_code == 403:
                msg = f"Permission denied creating branch '{branch}' — token may lack write access to this repository"
                logger.warning("_create_branch_and_push %s/%s: 403 %s", owner, repo, msg)
                return {"status": "failed", "message": msg}
            if create_ref.status_code == 409:
                msg = f"Branch '{branch}' already exists (possible race condition)"
                logger.warning("_create_branch_and_push %s/%s: 409 %s", owner, repo, msg)
                return {"status": "failed", "message": msg}
            if create_ref.status_code != 201:
                msg = f"Failed to create branch '{branch}' (HTTP {create_ref.status_code}): {self._truncated_response_text(create_ref)}"
                logger.warning("_create_branch_and_push %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            logger.info("Re-entering push_files after branch create %s/%s -> %s", owner, repo, branch)
            return await self.push_files(repo_url, token, branch, files, client, _allow_branch_create=False)

        except Exception as e:
            logger.exception("_create_branch_and_push failed for %s/%s branch=%s", owner, repo, branch)
            return {"status": "failed", "message": str(e), "error": str(e)}

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
            message = (
                f"Initial backup created - {len(files)} files"
                if commit_sha
                else f"Initial backup created - {len(files)} files (commit SHA not reported by server)"
            )
            return {
                "status": "success",
                "message": message,
                "commit_sha": commit_sha,
                "files_changed": len(files),
            }

        except Exception as e:
            logger.exception("_create_initial_commit failed for %s/%s branch=%s", owner, repo, branch)
            return {"status": "failed", "message": str(e), "error": str(e)}
