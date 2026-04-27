"""GitHub Enterprise backend — same Git Data API as github.com under /api/v3."""

from backend.app.services.git_providers.github import GitHubBackend


class GitHubEnterpriseBackend(GitHubBackend):
    """Backend for GitHub Enterprise Server instances."""

    def get_api_base(self, repo_url: str, api_base_url: str | None) -> str:
        if not api_base_url:
            raise ValueError("api_base_url is required for GitHub Enterprise")
        return f"{api_base_url.rstrip('/')}/api/v3"
