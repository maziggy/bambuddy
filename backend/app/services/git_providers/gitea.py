"""Gitea backend — uses the Git Data API inherited from GitHubBackend."""

import re

from backend.app.services.git_providers.github import GitHubBackend


class GiteaBackend(GitHubBackend):
    """Backend for Gitea instances.

    Gitea's Git Data API (/api/v1/repos/{owner}/{repo}/git/...) is compatible
    with GitHub's, so push_files, _create_branch_and_push, and _create_initial_commit
    are inherited unchanged. Only the API base URL and Accept header differ.
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
