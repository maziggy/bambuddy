"""Forgejo backend — currently API-compatible with Gitea (/api/v1)."""

from backend.app.services.git_providers.gitea import GiteaBackend


class ForgejoBackend(GiteaBackend):
    """Backend for Forgejo instances.

    Currently API-compatible with Gitea (/api/v1). Override methods here
    as the two projects' APIs diverge.
    """
