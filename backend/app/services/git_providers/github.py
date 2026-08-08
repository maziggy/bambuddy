"""GitHub backend — implements GitProviderBackend using the GitHub Git Data API."""

import base64
import json
import logging
import re
from datetime import datetime, timezone

import httpx

from backend.app.services.git_providers.base import GitProviderBackend

logger = logging.getLogger(__name__)


class GitHubBackend(GitProviderBackend):
    """Backend for github.com using the GitHub Git Data API."""

    def get_api_base(self, repo_url: str) -> str:
        m = re.match(r"https?://([\w.\-]+(:\d+)?)/", repo_url)
        if m:
            host = m.group(1)
            return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        m = re.match(r"git@([\w.\-]+):", repo_url)
        if m:
            host = m.group(1)
            return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        return "https://api.github.com"

    def parse_repo_url(self, url: str) -> tuple[str, str]:
        """Return (owner, repo) from a Git HTTPS or SSH URL."""
        if not url or len(url) > 500:
            raise ValueError("Invalid Git URL: URL too long or empty")

        # HTTPS: https://<host>[:<port>]/<owner>/<repo>[.git][/]
        match = re.match(
            r"https://[\w.\-]+(:\d+)?/([\w.\-]{1,100})/([\w.\-]{1,100})(?:\.git)?/?$",
            url,
        )
        if match:
            return match.group(2), match.group(3).removesuffix(".git")

        # SSH: git@<host>:<owner>/<repo>[.git]
        match = re.match(
            r"git@[\w.\-]+:([\w.\-]{1,100})/([\w.\-]{1,100})(?:\.git)?$",
            url,
        )
        if match:
            return match.group(1), match.group(2).removesuffix(".git")

        raise ValueError(f"Cannot parse repository URL: {url}")

    async def test_connection(self, repo_url: str, token: str, client: httpx.AsyncClient) -> dict:
        """Test API access and push permission for the repository."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
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
            is_private = bool(data.get("private", False))

            if not permissions.get("push", False):
                return {
                    "success": False,
                    "message": "Token does not have push permission to this repository",
                    "repo_name": data.get("full_name"),
                    "permissions": permissions,
                    "is_private": is_private,
                }

            return {
                "success": True,
                "message": "Connection successful",
                "repo_name": data.get("full_name"),
                "permissions": permissions,
                "is_private": is_private,
            }

        except Exception as e:
            logger.exception("Git connection test failed")
            detail = str(e)[:200]
            message = (
                f"Connection failed: {type(e).__name__}: {detail}"
                if detail
                else f"Connection failed: {type(e).__name__}"
            )
            return {
                "success": False,
                "message": message,
                "repo_name": None,
                "permissions": None,
                "is_private": None,
            }

    async def list_commits(
        self,
        repo_url: str,
        token: str,
        branch: str,
        client: httpx.AsyncClient,
        limit: int = 20,
    ) -> dict:
        """List recent commits on ``branch`` via the repo commits API."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            # GitHub pages with ``per_page`` and ignores ``limit``; Gitea/Forgejo
            # do the reverse. Sending both lets GiteaBackend inherit this method
            # unchanged instead of duplicating it for one query parameter.
            response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/commits",
                headers=headers,
                params={"sha": branch, "per_page": limit, "limit": limit},
            )

            if response.status_code == 404:
                return {
                    "success": False,
                    "message": (
                        f"Branch '{branch}' not found, or the repository has no commits yet. "
                        "Run a backup before restoring."
                    ),
                    "commits": [],
                }
            if response.status_code != 200:
                msg = f"Failed to list commits (HTTP {response.status_code}): {self._truncated_response_text(response)}"
                logger.warning("list_commits %s/%s: %s", owner, repo, msg)
                return {"success": False, "message": msg, "commits": []}

            try:
                data = response.json()
            except ValueError:
                return {"success": False, "message": "Non-JSON response listing commits", "commits": []}
            if not isinstance(data, list):
                return {"success": False, "message": "Unexpected shape listing commits", "commits": []}

            return {"success": True, "message": "OK", "commits": self._parse_commit_entries(data, limit)}

        except Exception as e:
            logger.exception("list_commits failed for %s branch=%s", repo_url, branch)
            return {"success": False, "message": f"{type(e).__name__}: {str(e)[:200]}", "commits": []}

    @staticmethod
    def _parse_commit_entries(data: list, limit: int) -> list[dict]:
        """Normalise GitHub/Gitea commit list entries to our flat shape."""
        commits = []
        for entry in data[:limit]:
            if not isinstance(entry, dict):
                continue
            sha = entry.get("sha")
            if not isinstance(sha, str) or not sha:
                continue
            commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
            author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
            commits.append(
                {
                    "sha": sha,
                    "message": commit.get("message") or "",
                    "author": author.get("name") or "",
                    "date": author.get("date") or "",
                }
            )
        return commits

    async def _blob_shas_at(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        api_base: str,
        owner: str,
        repo: str,
        ref: str,
    ) -> tuple[dict[str, str] | None, str]:
        """Return ``({path: blob_sha}, "")`` at ``ref``, or ``(None, error_message)``.

        A commit SHA is a valid tree-ish for the trees API, so this resolves the
        commit's tree in one request rather than commit -> tree -> list.
        """
        response = await client.get(
            f"{api_base}/repos/{owner}/{repo}/git/trees/{ref}?recursive=1",
            headers=headers,
        )
        if response.status_code == 404:
            return None, f"Commit or tree '{ref}' not found in the repository"
        if response.status_code != 200:
            return None, f"Failed to list tree (HTTP {response.status_code}): {self._truncated_response_text(response)}"
        try:
            data = response.json()
        except ValueError:
            return None, "Non-JSON response listing tree"
        # Same limit the push path guards against: a truncated listing would make
        # a restore silently skip categories that are actually in the backup.
        if data.get("truncated"):
            return None, (
                "Repository tree exceeds the API listing limit (truncated=true), so the backup "
                "contents cannot be enumerated reliably. Rotate the backup repository."
            )
        blobs: dict[str, str] = {}
        for item in data.get("tree", []):
            if not isinstance(item, dict) or item.get("type") != "blob":
                continue
            path, sha = item.get("path"), item.get("sha")
            if isinstance(path, str) and isinstance(sha, str) and path and sha:
                blobs[path] = sha
        return blobs, ""

    async def get_commit(self, repo_url: str, token: str, ref: str, client: httpx.AsyncClient) -> dict:
        """Read one commit's metadata directly, for refs outside the list window."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            response = await client.get(f"{api_base}/repos/{owner}/{repo}/commits/{ref}", headers=headers)
            if response.status_code == 404:
                return {"success": False, "message": f"Commit '{ref}' not found in the repository", "commit": None}
            if response.status_code != 200:
                msg = f"Failed to read commit (HTTP {response.status_code}): {self._truncated_response_text(response)}"
                logger.warning("get_commit %s/%s ref=%s: %s", owner, repo, ref, msg)
                return {"success": False, "message": msg, "commit": None}

            try:
                data = response.json()
            except ValueError:
                return {"success": False, "message": "Non-JSON response reading commit", "commit": None}
            if not isinstance(data, dict):
                return {"success": False, "message": "Unexpected shape reading commit", "commit": None}

            # Same entry shape as list_commits, so callers can treat the two
            # interchangeably.
            parsed = self._parse_commit_entries([data], 1)
            if not parsed:
                return {"success": False, "message": "Commit response carried no SHA", "commit": None}
            return {"success": True, "message": "OK", "commit": parsed[0]}

        except Exception as e:
            logger.exception("get_commit failed for %s ref=%s", repo_url, ref)
            return {"success": False, "message": f"{type(e).__name__}: {str(e)[:200]}", "commit": None}

    async def list_tree(
        self,
        repo_url: str,
        token: str,
        ref: str,
        client: httpx.AsyncClient,
    ) -> dict:
        """List blob paths present at ``ref`` via the Git Data trees API."""
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            blobs, error = await self._blob_shas_at(client, headers, api_base, owner, repo, ref)
            if blobs is None:
                logger.warning("list_tree %s/%s ref=%s: %s", owner, repo, ref, error)
                return {"success": False, "message": error, "paths": [], "blob_shas": {}}

            # The map is handed back so fetch_files does not GET the same
            # recursive tree a second time for the same ref.
            return {"success": True, "message": "OK", "paths": sorted(blobs), "blob_shas": blobs}

        except Exception as e:
            logger.exception("list_tree failed for %s ref=%s", repo_url, ref)
            return {"success": False, "message": f"{type(e).__name__}: {str(e)[:200]}", "paths": [], "blob_shas": {}}

    async def fetch_files(
        self,
        repo_url: str,
        token: str,
        ref: str,
        paths: list[str],
        client: httpx.AsyncClient,
        blob_shas: dict[str, str] | None = None,
    ) -> dict:
        """Read ``paths`` at ``ref`` via the Git Data blobs API.

        The blobs API is used rather than the contents API because contents
        inlines only files up to 1 MB — an archive-heavy ``print_history.json``
        can exceed that, and it would come back with an empty body instead of an
        error.
        """
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            blobs = blob_shas
            if blobs is None:
                blobs, error = await self._blob_shas_at(client, headers, api_base, owner, repo, ref)
                if blobs is None:
                    logger.warning("fetch_files %s/%s ref=%s: %s", owner, repo, ref, error)
                    return {"success": False, "message": error, "files": {}}

            files: dict[str, str] = {}
            for path in paths:
                sha = blobs.get(path)
                if sha is None:
                    continue
                response = await client.get(f"{api_base}/repos/{owner}/{repo}/git/blobs/{sha}", headers=headers)
                if response.status_code != 200:
                    msg = f"Failed to read {path} (HTTP {response.status_code}): {self._truncated_response_text(response)}"
                    logger.warning("fetch_files %s/%s: %s", owner, repo, msg)
                    return {"success": False, "message": msg, "files": {}}
                text, error = self._decode_blob(response, path)
                if text is None:
                    logger.warning("fetch_files %s/%s: %s", owner, repo, error)
                    return {"success": False, "message": error, "files": {}}
                files[path] = text

            return {"success": True, "message": "OK", "files": files}

        except Exception as e:
            logger.exception("fetch_files failed for %s ref=%s", repo_url, ref)
            return {"success": False, "message": f"{type(e).__name__}: {str(e)[:200]}", "files": {}}

    def _decode_blob(self, response: httpx.Response, path: str) -> tuple[str | None, str]:
        """Decode a blob API response body to text, or return an error message."""
        try:
            data = response.json()
        except ValueError:
            return None, f"Non-JSON response reading {path}"
        if not isinstance(data, dict):
            return None, f"Unexpected shape reading {path}"
        content = data.get("content")
        if not isinstance(content, str):
            return None, f"Missing content reading {path}"
        encoding = data.get("encoding", "base64")
        try:
            if encoding == "base64":
                # Both providers wrap base64 payloads at 60 chars; b64decode
                # tolerates the newlines, but be explicit about it.
                return base64.b64decode(content).decode("utf-8"), ""
            if encoding in ("utf-8", "text", "plain"):
                return content, ""
        except (ValueError, UnicodeDecodeError) as e:
            return None, f"Could not decode {path}: {type(e).__name__}"
        return None, f"Unsupported blob encoding {encoding!r} reading {path}"

    async def push_files(
        self,
        repo_url: str,
        token: str,
        branch: str,
        files: dict,
        client: httpx.AsyncClient,
        _allow_branch_create: bool = True,
    ) -> dict:
        """Push files to the repository using the Git Data API."""
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
                msg = f"Failed to get branch ref (HTTP {ref_response.status_code}): {self._truncated_response_text(ref_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg, "error": self._truncated_response_text(ref_response)}

            current_commit_sha, err = self._read_sha(ref_response, "object", "sha")
            if err:
                msg = f"Malformed ref response ({err}): {self._truncated_response_text(ref_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            commit_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/git/commits/{current_commit_sha}", headers=headers
            )
            if commit_response.status_code != 200:
                msg = f"Failed to get current commit (HTTP {commit_response.status_code}): {self._truncated_response_text(commit_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            current_tree_sha, err = self._read_sha(commit_response, "tree", "sha")
            if err:
                msg = f"Malformed commit response ({err}): {self._truncated_response_text(commit_response)}"
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
            # GitHub's tree API truncates >7MB / >100k entries. A truncated tree
            # listing makes the SHA-equality dedup miss and every file gets
            # re-uploaded as a new blob each run — silent churn until someone
            # notices the bloated history. Fail loudly so the user rotates the
            # backup repo.
            if tree_data.get("truncated"):
                msg = (
                    "Repository tree exceeds the GitHub API listing limit (truncated=true). "
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
                if blob_response.status_code == 404:
                    msg = "GitHub API returned 404 for POST /git/blobs — check repository visibility and token scope"
                    logger.warning("push_files %s/%s: %s", owner, repo, msg)
                    return {"status": "failed", "message": msg}
                if blob_response.status_code != 201:
                    msg = f"Failed to create blob for {path} (HTTP {blob_response.status_code}): {self._truncated_response_text(blob_response)}"
                    logger.warning("push_files %s/%s: %s", owner, repo, msg)
                    return {"status": "failed", "message": msg}

                blob_sha, err = self._read_sha(blob_response, "sha")
                if err:
                    msg = f"Malformed blob response for {path} ({err}): {self._truncated_response_text(blob_response)}"
                    logger.warning("push_files %s/%s: %s", owner, repo, msg)
                    return {"status": "failed", "message": msg}
                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})
                files_changed += 1

            if not tree_items:
                return {"status": "skipped", "message": "No changes to commit", "commit_sha": None, "files_changed": 0}

            tree_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"base_tree": current_tree_sha, "tree": tree_items},
            )
            if tree_response.status_code != 201:
                msg = f"Failed to create tree (HTTP {tree_response.status_code}): {self._truncated_response_text(tree_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            new_tree_sha, err = self._read_sha(tree_response, "sha")
            if err:
                msg = f"Malformed tree-create response ({err}): {self._truncated_response_text(tree_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}
            commit_message = f"Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            commit_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={"message": commit_message, "tree": new_tree_sha, "parents": [current_commit_sha]},
            )
            if commit_response.status_code != 201:
                msg = f"Failed to create commit (HTTP {commit_response.status_code}): {self._truncated_response_text(commit_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            new_commit_sha, err = self._read_sha(commit_response, "sha")
            if err:
                msg = f"Malformed commit-create response ({err}): {self._truncated_response_text(commit_response)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            ref_update = await client.patch(
                f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{branch}",
                headers=headers,
                json={"sha": new_commit_sha},
            )
            if ref_update.status_code != 200:
                msg = f"Failed to update branch (HTTP {ref_update.status_code}): {self._truncated_response_text(ref_update)}"
                logger.warning("push_files %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            return {
                "status": "success",
                "message": f"Backup successful - {files_changed} files updated",
                "commit_sha": new_commit_sha,
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

            try:
                default_branch = repo_response.json().get("default_branch", "main")
            except ValueError:
                msg = f"Malformed repo-info response (non-JSON body): {self._truncated_response_text(repo_response)}"
                logger.warning("_create_branch_and_push %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            ref_response = await client.get(
                f"{api_base}/repos/{owner}/{repo}/git/refs/heads/{default_branch}", headers=headers
            )
            if ref_response.status_code != 200:
                return await self._create_initial_commit(client, headers, api_base, owner, repo, branch, files)

            base_sha, err = self._read_sha(ref_response, "object", "sha")
            if err:
                msg = f"Malformed default-branch ref response ({err}): {self._truncated_response_text(ref_response)}"
                logger.warning("_create_branch_and_push %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            create_ref = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            )
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
                if blob_response.status_code == 404:
                    msg = "GitHub API returned 404 for POST /git/blobs — check repository visibility and token scope"
                    logger.warning("_create_initial_commit %s/%s: %s", owner, repo, msg)
                    return {"status": "failed", "message": msg}
                if blob_response.status_code != 201:
                    msg = f"Failed to create blob for {path} (HTTP {blob_response.status_code}): {self._truncated_response_text(blob_response)}"
                    logger.warning("_create_initial_commit %s/%s: %s", owner, repo, msg)
                    return {"status": "failed", "message": msg}
                blob_sha, err = self._read_sha(blob_response, "sha")
                if err:
                    msg = f"Malformed blob response for {path} ({err}): {self._truncated_response_text(blob_response)}"
                    logger.warning("_create_initial_commit %s/%s: %s", owner, repo, msg)
                    return {"status": "failed", "message": msg}
                tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})

            tree_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"tree": tree_items},
            )
            if tree_response.status_code != 201:
                msg = f"Failed to create tree (HTTP {tree_response.status_code}): {self._truncated_response_text(tree_response)}"
                logger.warning("_create_initial_commit %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            tree_sha, err = self._read_sha(tree_response, "sha")
            if err:
                msg = f"Malformed tree-create response ({err}): {self._truncated_response_text(tree_response)}"
                logger.warning("_create_initial_commit %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}
            commit_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={
                    "message": f"Initial Bambuddy backup - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    "tree": tree_sha,
                },
            )
            if commit_response.status_code != 201:
                msg = f"Failed to create commit (HTTP {commit_response.status_code}): {self._truncated_response_text(commit_response)}"
                logger.warning("_create_initial_commit %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            commit_sha, err = self._read_sha(commit_response, "sha")
            if err:
                msg = f"Malformed commit-create response ({err}): {self._truncated_response_text(commit_response)}"
                logger.warning("_create_initial_commit %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}
            ref_response = await client.post(
                f"{api_base}/repos/{owner}/{repo}/git/refs",
                headers=headers,
                json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
            )
            if ref_response.status_code != 201:
                msg = f"Failed to create branch ref (HTTP {ref_response.status_code}): {self._truncated_response_text(ref_response)}"
                logger.warning("_create_initial_commit %s/%s: %s", owner, repo, msg)
                return {"status": "failed", "message": msg}

            return {
                "status": "success",
                "message": f"Initial backup created - {len(files)} files",
                "commit_sha": commit_sha,
                "files_changed": len(files),
            }

        except Exception as e:
            logger.exception("_create_initial_commit failed for %s/%s branch=%s", owner, repo, branch)
            return {"status": "failed", "message": str(e), "error": str(e)}
