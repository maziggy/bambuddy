"""Abstract base class for Git hosting provider backends."""

import hashlib
from abc import ABC, abstractmethod

import httpx


class GitProviderBackend(ABC):
    """Abstract base for Git hosting provider API backends."""

    @staticmethod
    def _blob_sha(content_bytes: bytes) -> str:
        """Compute the git blob SHA for content_bytes (sha1("blob {len}\\0" + data))."""
        return hashlib.sha1(f"blob {len(content_bytes)}\0".encode() + content_bytes, usedforsecurity=False).hexdigest()

    @staticmethod
    def _truncated_response_text(response: httpx.Response, max_length: int = 200) -> str:
        """Return a bounded response body for errors surfaced to logs/UI."""
        text = response.text
        if len(text) <= max_length:
            return text
        return f"{text[: max_length - 3]}..."

    @staticmethod
    def _read_sha(response: httpx.Response, *path: str) -> tuple[str | None, str | None]:
        """Walk a JSON path to a string SHA value.

        Returns ``(sha, None)`` on success, ``(None, reason)`` if the body is
        not JSON, the path is missing, or the leaf is not a string. Callers
        use the reason to build a clear failure message instead of letting
        ``KeyError``/``JSONDecodeError`` bubble to the outer catch-all (which
        surfaces cryptic one-word strings like ``"'object'"`` to operators).
        """
        try:
            data = response.json()
        except ValueError:
            return None, "non-JSON response body"
        for key in path:
            if not isinstance(data, dict):
                return None, f"unexpected shape at key {key!r}"
            if key not in data:
                return None, f"missing key {key!r}"
            data = data[key]
        if not isinstance(data, str):
            return None, f"value at {'.'.join(path)} is not a string"
        return data, None

    def get_headers(self, token: str) -> dict:
        """Return HTTP headers for authenticated API requests."""
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Bambuddy-Backup",
        }

    @abstractmethod
    def parse_repo_url(self, url: str) -> tuple[str, str]:
        """Return (owner, repo) extracted from the repository URL."""

    @abstractmethod
    def get_api_base(self, repo_url: str) -> str:
        """Return the API base URL for this provider instance."""

    @abstractmethod
    async def test_connection(self, repo_url: str, token: str, client: httpx.AsyncClient) -> dict:
        """Test API connectivity and push permissions. Returns success/message/repo_name/permissions."""

    @abstractmethod
    async def push_files(
        self,
        repo_url: str,
        token: str,
        branch: str,
        files: dict,
        client: httpx.AsyncClient,
    ) -> dict:
        """Push files to the repository. Returns status/message/commit_sha/files_changed."""

    # --- Read side (restore, issue #2656) ---------------------------------
    # The backup path only ever writes. Restore needs to walk history, list a
    # snapshot and read individual blobs back, so these three mirror the
    # ``{"success": bool, "message": str, ...}`` convention ``test_connection``
    # already uses rather than raising.

    @abstractmethod
    async def list_commits(
        self,
        repo_url: str,
        token: str,
        branch: str,
        client: httpx.AsyncClient,
        limit: int = 20,
    ) -> dict:
        """List recent commits on ``branch``, newest first.

        Returns ``{"success", "message", "commits": [{"sha", "message", "author", "date"}]}``.
        """

    @abstractmethod
    async def get_commit(self, repo_url: str, token: str, ref: str, client: httpx.AsyncClient) -> dict:
        """Read one commit's display metadata by SHA.

        ``list_commits`` only reaches back as far as its limit, so a ref outside
        that window has no entry to describe it. This is the direct lookup for
        that case.

        Returns ``{"success", "message", "commit": {"sha", "message", "author",
        "date"} | None}``.
        """

    @abstractmethod
    async def list_tree(
        self,
        repo_url: str,
        token: str,
        ref: str,
        client: httpx.AsyncClient,
    ) -> dict:
        """List every blob path present at ``ref``.

        ``ref`` is a concrete commit SHA — the caller resolves "latest" to a SHA
        via :meth:`list_commits` first, so the snapshot being previewed and the
        one being restored are provably the same commit even if a scheduled
        backup lands in between.

        Returns ``{"success", "message", "paths": [str], "blob_shas":
        {path: sha}}``. ``blob_shas`` is the path -> blob SHA map the listing
        already had to build, offered so :meth:`fetch_files` need not fetch the
        same tree again; providers that read files by path return ``{}``.
        """

    @abstractmethod
    async def fetch_files(
        self,
        repo_url: str,
        token: str,
        ref: str,
        paths: list[str],
        client: httpx.AsyncClient,
        blob_shas: dict[str, str] | None = None,
    ) -> dict:
        """Read several files' decoded UTF-8 text at ``ref``.

        Batched rather than one-file-at-a-time so providers that need a tree
        listing to map path -> blob SHA can do that lookup once for the whole
        restore instead of per file.

        ``blob_shas`` is the map :meth:`list_tree` returned for the same ref, if
        the caller has one. Passing it saves a second recursive tree GET; a
        provider that reads by path ignores it, and one that needs it fetches
        the tree itself when it is absent.

        Returns ``{"success", "message", "files": {path: text}}``. Paths absent
        from the commit are simply missing from ``files`` — that is not an error,
        since which categories a given backup contains varies by config.
        """
