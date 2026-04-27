"""Abstract base class for Git hosting provider backends."""

from abc import ABC, abstractmethod

import httpx


class GitProviderBackend(ABC):
    """Abstract base for Git hosting provider API backends."""

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
    def get_api_base(self, repo_url: str, api_base_url: str | None) -> str:
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
        api_base_url: str | None = None,
    ) -> dict:
        """Push files to the repository. Returns status/message/commit_sha/files_changed."""
