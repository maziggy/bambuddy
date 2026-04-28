"""Unit tests for the git_providers abstraction package."""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.git_providers.factory import get_provider_backend
from backend.app.services.git_providers.forgejo import ForgejoBackend
from backend.app.services.git_providers.gitea import GiteaBackend
from backend.app.services.git_providers.github import GitHubBackend
from backend.app.services.git_providers.gitlab import GitLabBackend


class TestFactory:
    def test_known_providers_return_correct_class(self):
        assert isinstance(get_provider_backend("github"), GitHubBackend)
        assert isinstance(get_provider_backend("gitea"), GiteaBackend)
        assert isinstance(get_provider_backend("forgejo"), ForgejoBackend)
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
        self.backend = GiteaBackend()

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


class TestGiteaBackendPushFiles:
    def setup_method(self):
        self.backend = GiteaBackend()
        self.repo_url = "https://git.example.com/owner/repo"
        self.token = "gitea-token"
        self.branch = "bambuddy-backup"
        self.files = {"config/printers.json": {"name": "my-printer"}}

    @pytest.mark.asyncio
    async def test_creates_file_using_contents_api(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"name": self.branch}),
                _make_mock_response(404, {}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "abc123"}}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "success"
        assert result["commit_sha"] == "abc123"
        assert result["files_changed"] == 1
        assert "/contents/config/printers.json" in client.post.call_args.args[0]

    @pytest.mark.asyncio
    async def test_updates_existing_changed_file_using_contents_api(self):
        stale_sha = "0000000000000000000000000000000000000000"

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"name": self.branch}),
                _make_mock_response(200, {"sha": stale_sha}),
            ]
        )
        client.put = AsyncMock(return_value=_make_mock_response(200, {"commit": {"id": "def456"}}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "success"
        assert result["commit_sha"] == "def456"
        payload = client.put.call_args.kwargs["json"]
        assert payload["sha"] == stale_sha

    @pytest.mark.asyncio
    async def test_skips_unchanged_existing_file(self):
        sha = _blob_sha(self.files["config/printers.json"])

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"name": self.branch}),
                _make_mock_response(200, {"sha": sha}),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "skipped"
        client.post.assert_not_called()
        client.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_missing_branch_from_default_branch(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),
                _make_mock_response(200, {"default_branch": "main"}),
                _make_mock_response(404, {}),
            ]
        )
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {"name": self.branch}),
                _make_mock_response(201, {"commit": {"sha": "abc123"}}),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "success"
        branch_payload = client.post.call_args_list[0].kwargs["json"]
        assert branch_payload["new_branch_name"] == self.branch


class TestForgejoBackendApiBase:
    def setup_method(self):
        self.backend = ForgejoBackend()

    def test_derives_api_base_from_repo_url(self):
        result = self.backend.get_api_base("https://forgejo.example.com/owner/repo")
        assert result == "https://forgejo.example.com/api/v1"

    def test_derives_api_base_with_port(self):
        result = self.backend.get_api_base("https://forgejo.example.com:3000/owner/repo")
        assert result == "https://forgejo.example.com:3000/api/v1"

    def test_invalid_url_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot derive API base"):
            self.backend.get_api_base("not-a-url")

    def test_parse_url_uses_instance_host(self):
        owner, repo = self.backend.parse_repo_url("https://forgejo.example.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"


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


def _blob_sha(content: dict) -> str:
    content_bytes = json.dumps(content, indent=2, default=str).encode("utf-8")
    return hashlib.sha1(f"blob {len(content_bytes)}\0".encode() + content_bytes, usedforsecurity=False).hexdigest()


def _make_mock_response(status_code: int, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=body or {})
    return resp


class TestGitLabBackendPushFiles:
    def setup_method(self):
        self.backend = GitLabBackend()
        self.repo_url = "https://gitlab.com/owner/repo"
        self.token = "glpat-test"
        self.branch = "bambuddy-backup"
        self.files = {"config/printers.json": {"name": "my-printer"}}

    @pytest.mark.asyncio
    async def test_skips_commit_when_content_unchanged(self):
        sha = _blob_sha(self.files["config/printers.json"])

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                # branch check → branch exists
                _make_mock_response(200, {"name": self.branch}),
                # tree fetch → one blob whose sha matches current content
                _make_mock_response(200, [{"type": "blob", "path": "config/printers.json", "id": sha}]),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "skipped"
        assert result["files_changed"] == 0
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_commits_when_content_changed(self):
        stale_sha = "0000000000000000000000000000000000000000"

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"name": self.branch}),
                _make_mock_response(200, [{"type": "blob", "path": "config/printers.json", "id": stale_sha}]),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"id": "abc123"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "success"
        assert result["files_changed"] == 1
        client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_new_file_not_in_existing_tree(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"name": self.branch}),
                # tree is empty
                _make_mock_response(200, []),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"id": "def456"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "success"
        call_kwargs = client.post.call_args.kwargs["json"]
        assert call_kwargs["actions"][0]["action"] == "create"
