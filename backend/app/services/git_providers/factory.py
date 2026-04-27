"""Factory for instantiating the correct Git provider backend."""

from backend.app.services.git_providers.base import GitProviderBackend
from backend.app.services.git_providers.gitea import GiteaForgejoBackend
from backend.app.services.git_providers.github import GitHubBackend
from backend.app.services.git_providers.github_enterprise import GitHubEnterpriseBackend
from backend.app.services.git_providers.gitlab import GitLabBackend

_BACKENDS: dict[str, type[GitProviderBackend]] = {
    "github": GitHubBackend,
    "github_enterprise": GitHubEnterpriseBackend,
    "gitea": GiteaForgejoBackend,
    "gitlab": GitLabBackend,
}


def get_provider_backend(provider: str) -> GitProviderBackend:
    """Return an instantiated backend for the given provider key."""
    backend_cls = _BACKENDS.get(provider)
    if backend_cls is None:
        raise ValueError(f"Unknown Git provider: {provider!r}")
    return backend_cls()
