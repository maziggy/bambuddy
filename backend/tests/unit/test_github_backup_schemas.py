"""Unit tests for GitHub backup Pydantic schemas."""

import pytest
from pydantic import ValidationError

from backend.app.schemas.github_backup import GitHubBackupConfigCreate, ProviderType


class TestProviderTypeEnum:
    def test_has_expected_string_values(self):
        assert ProviderType.GITHUB == "github"
        assert ProviderType.GITEA == "gitea"
        assert ProviderType.FORGEJO == "forgejo"
        assert ProviderType.GITLAB == "gitlab"


class TestGitHubBackupConfigCreate:
    BASE_FIELDS = {
        "repository_url": "https://github.com/owner/repo",
        "access_token": "ghp_token",
    }

    def test_plain_github_is_valid(self):
        config = GitHubBackupConfigCreate(**self.BASE_FIELDS)
        assert config.provider == ProviderType.GITHUB

    def test_gitea_is_valid_without_api_base_url(self):
        config = GitHubBackupConfigCreate(
            repository_url="https://git.example.com/owner/repo",
            access_token="token",
            provider="gitea",
        )
        assert config.provider == ProviderType.GITEA

    def test_forgejo_is_valid(self):
        config = GitHubBackupConfigCreate(
            repository_url="https://forgejo.example.com/owner/repo",
            access_token="token",
            provider="forgejo",
        )
        assert config.provider == ProviderType.FORGEJO

    def test_url_regex_accepts_self_hosted_https_url(self):
        config = GitHubBackupConfigCreate(
            repository_url="https://git.example.com/owner/repo",
            access_token="token",
            provider="gitea",
        )
        assert "git.example.com" in config.repository_url

    def test_url_regex_accepts_ssh_url(self):
        config = GitHubBackupConfigCreate(
            repository_url="git@github.com:owner/repo",
            access_token="ghp_token",
        )
        assert config.repository_url == "git@github.com:owner/repo"

    def test_invalid_url_raises_validation_error(self):
        with pytest.raises(ValidationError, match="Invalid Git repository URL"):
            GitHubBackupConfigCreate(
                repository_url="not-a-url",
                access_token="ghp_token",
            )

    def test_unknown_provider_raises_validation_error(self):
        with pytest.raises(ValidationError):
            GitHubBackupConfigCreate(
                **self.BASE_FIELDS,
                provider="bitbucket",
            )
