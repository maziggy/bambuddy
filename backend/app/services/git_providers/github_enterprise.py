"""GitHub Enterprise backend — same Git Data API as github.com under /api/v3."""

import re

from backend.app.services.git_providers.github import GitHubBackend


class GitHubEnterpriseBackend(GitHubBackend):
    """Backend for GitHub Enterprise Server instances."""

    def get_api_base(self, repo_url: str) -> str:
        match = re.match(r"(https?://[\w.\-]+(:\d+)?)/", repo_url)
        if match:
            return f"{match.group(1)}/api/v3"
        raise ValueError(f"Cannot derive API base from URL: {repo_url}")
