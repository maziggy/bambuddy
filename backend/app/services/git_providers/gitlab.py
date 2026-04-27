"""GitLab backend stub — not yet implemented."""

import httpx

from backend.app.services.git_providers.base import GitProviderBackend


class GitLabBackend(GitProviderBackend):
    """Stub for GitLab. GitLab's API differs fundamentally from GitHub's Git Data API."""

    def parse_repo_url(self, url: str) -> tuple[str, str]:
        raise NotImplementedError("GitLab support is not yet implemented")

    def get_api_base(self, repo_url: str, api_base_url: str | None) -> str:
        raise NotImplementedError("GitLab support is not yet implemented")

    async def test_connection(self, repo_url: str, token: str, client: httpx.AsyncClient) -> dict:
        raise NotImplementedError("GitLab support is not yet implemented")

    async def push_files(
        self,
        repo_url: str,
        token: str,
        branch: str,
        files: dict,
        client: httpx.AsyncClient,
        api_base_url: str | None = None,
    ) -> dict:
        raise NotImplementedError("GitLab support is not yet implemented")
