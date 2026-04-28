"""Gitea backend — identical Git Data API to GitHub, different base URL."""

import re

from backend.app.services.git_providers.github import GitHubBackend


class GiteaBackend(GitHubBackend):
    """Backend for Gitea instances.

    The Git Data API is endpoint-compatible with GitHub; only the base URL
    (scheme://host/api/v1) and Accept header differ.
    """

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
