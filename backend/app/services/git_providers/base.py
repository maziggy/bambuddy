"""Abstract base class for Git hosting provider backends."""

import hashlib
from abc import ABC, abstractmethod

import httpx


class GitProviderBackend(ABC):
    """Abstract base for Git hosting provider API backends."""

    @staticmethod
    def _blob_sha(content_bytes: bytes) -> str:
        """Compute the git blob SHA for content_bytes (sha1("blob {len}\\0" + data))."""
        return hashlib.sha1(
            f"blob {len(content_bytes)}\0".encode() + content_bytes, usedforsecurity=False
        ).hexdigest()

    @staticmethod
    def _truncated_response_text(response: httpx.Response, max_length: int = 200) -> str:
        """Return a bounded response body for errors surfaced to logs/UI."""
        text = response.text
        if len(text) <= max_length:
            return text
        return f"{text[: max_length - 3]}..."

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
