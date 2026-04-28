"""Unit tests for the git_providers abstraction package."""

import pytest

from backend.app.services.git_providers.factory import get_provider_backend
from backend.app.services.git_providers.gitea import GiteaForgejoBackend
from backend.app.services.git_providers.github import GitHubBackend
from backend.app.services.git_providers.github_enterprise import GitHubEnterpriseBackend
from backend.app.services.git_providers.gitlab import GitLabBackend


class TestFactory:
    def test_known_providers_return_correct_class(self):
        assert isinstance(get_provider_backend("github"), GitHubBackend)
        assert isinstance(get_provider_backend("github_enterprise"), GitHubEnterpriseBackend)
        assert isinstance(get_provider_backend("gitea"), GiteaForgejoBackend)
        assert isinstance(get_provider_backend("gitlab"), GitLabBackend)

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown Git provider"):
            get_provider_backend("bitbucket")


class TestGitHubBackendParseUrl:
    def setup_method(self):
        self.backend = GitHubBackend()

    def test_https_url(self):
        owner, repo = self.backend.parse_repo_url("https://github.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_https_url_with_git_suffix(self):
        owner, repo = self.backend.parse_repo_url("https://github.com/owner/repo.git")
        assert owner == "owner"
        assert repo == "repo"

    def test_ssh_url(self):
        owner, repo = self.backend.parse_repo_url("git@github.com:owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_ssh_url_with_git_suffix(self):
        owner, repo = self.backend.parse_repo_url("git@github.com:owner/repo.git")
        assert owner == "owner"
        assert repo == "repo"

    def test_invalid_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot parse repository URL"):
            self.backend.parse_repo_url("https://example.com/not-a-repo")

    def test_empty_url_raises_value_error(self):
        with pytest.raises(ValueError):
            self.backend.parse_repo_url("")


class TestGiteaBackendApiBase:
    def setup_method(self):
        self.backend = GiteaForgejoBackend()

    def test_derives_api_base_from_repo_url(self):
        result = self.backend.get_api_base("https://git.example.com/owner/repo")
        assert result == "https://git.example.com/api/v1"

    def test_derives_api_base_with_port(self):
        result = self.backend.get_api_base("https://git.example.com:3000/owner/repo")
        assert result == "https://git.example.com:3000/api/v1"

    def test_invalid_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot derive API base"):
            self.backend.get_api_base("not-a-url")

    def test_parse_url_uses_instance_host(self):
        owner, repo = self.backend.parse_repo_url("https://git.example.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"


class TestGitHubEnterpriseBackendApiBase:
    def setup_method(self):
        self.backend = GitHubEnterpriseBackend()

    def test_derives_api_base_from_repo_url(self):
        result = self.backend.get_api_base("https://ghe.corp.com/owner/repo")
        assert result == "https://ghe.corp.com/api/v3"

    def test_derives_api_base_with_port(self):
        result = self.backend.get_api_base("https://ghe.corp.com:8080/owner/repo")
        assert result == "https://ghe.corp.com:8080/api/v3"

    def test_invalid_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot derive API base"):
            self.backend.get_api_base("not-a-url")


class TestGitLabBackend:
    def setup_method(self):
        self.backend = GitLabBackend()

    def test_parse_url_https(self):
        owner, repo = self.backend.parse_repo_url("https://gitlab.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_url_ssh(self):
        owner, repo = self.backend.parse_repo_url("git@gitlab.com:owner/repo.git")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_url_invalid_raises(self):
        with pytest.raises(ValueError):
            self.backend.parse_repo_url("not-a-url")

    def test_get_api_base_derives_from_repo_url(self):
        result = self.backend.get_api_base("https://gitlab.com/owner/repo")
        assert result == "https://gitlab.com/api/v4"

    def test_get_api_base_derives_from_self_hosted_url(self):
        result = self.backend.get_api_base("https://my-gitlab.example.com/owner/repo")
        assert result == "https://my-gitlab.example.com/api/v4"

    def test_get_api_base_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot derive API base"):
            self.backend.get_api_base("not-a-url")

    def test_get_headers_uses_bearer_token(self):
        headers = self.backend.get_headers("mytoken")
        assert headers["Authorization"] == "Bearer mytoken"
        assert "Content-Type" in headers
