"""Factory for instantiating the correct Git provider backend."""

from backend.app.services.git_providers.base import GitProviderBackend
from backend.app.services.git_providers.forgejo import ForgejoBackend
from backend.app.services.git_providers.gitea import GiteaBackend
from backend.app.services.git_providers.github import GitHubBackend
from backend.app.services.git_providers.gitlab import GitLabBackend

_BACKENDS: dict[str, type[GitProviderBackend]] = {
    "github": GitHubBackend,
    "gitea": GiteaBackend,
    "forgejo": ForgejoBackend,
    "gitlab": GitLabBackend,
}


def get_provider_backend(provider: str) -> GitProviderBackend:
    """Return an instantiated backend for the given provider key."""
    backend_cls = _BACKENDS.get(provider)
    if backend_cls is None:
        raise ValueError(f"Unknown Git provider: {provider!r}")
    return backend_cls()
