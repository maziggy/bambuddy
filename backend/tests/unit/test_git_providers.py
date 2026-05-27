"""Unit tests for the git_providers abstraction package."""

import base64
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


class TestGitHubBackendApiBase:
    def setup_method(self):
        self.backend = GitHubBackend()

    def test_github_com_returns_api_github_com(self):
        assert self.backend.get_api_base("https://github.com/owner/repo") == "https://api.github.com"

    def test_ghe_host_returns_v3_endpoint(self):
        assert self.backend.get_api_base("https://github.example.com/owner/repo") == "https://github.example.com/api/v3"

    def test_ghe_host_with_port(self):
        assert (
            self.backend.get_api_base("https://github.example.com:8443/owner/repo")
            == "https://github.example.com:8443/api/v3"
        )

    def test_ssh_github_com_returns_api_github_com(self):
        assert self.backend.get_api_base("git@github.com:owner/repo.git") == "https://api.github.com"

    def test_ssh_ghe_host_returns_v3_endpoint(self):
        assert self.backend.get_api_base("git@github.example.com:owner/repo.git") == "https://github.example.com/api/v3"


class TestGitHubBackendPushFiles:
    def setup_method(self):
        self.backend = GitHubBackend()
        self.repo_url = "https://github.com/owner/repo"
        self.token = "ghp_token"
        self.branch = "bambuddy-backup"

    @pytest.mark.asyncio
    async def test_successful_push(self):
        """Happy path: changed file goes through blob→tree→commit→ref-update."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "c1"}}),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {"sha": "blob1"}),
                _make_mock_response(201, {"sha": "new-tree"}),
                _make_mock_response(201, {"sha": "new-commit"}),
            ]
        )
        client.patch = AsyncMock(return_value=_make_mock_response(200, {}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "success"
        assert result["files_changed"] == 1

    @pytest.mark.asyncio
    async def test_skips_unchanged_files(self):
        """File whose blob SHA matches the existing tree entry is excluded from the commit."""
        content = {"name": "my-printer"}
        sha = _blob_sha(content)

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "c1"}}),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": [{"type": "blob", "path": "config.json", "sha": sha}]}),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"config.json": content}, client)

        assert result["status"] == "skipped"
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_blob_failure_returns_failed_not_skipped(self):
        """A non-201 blob response must return 'failed', not silently fall through to 'skipped'."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "c1"}}),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(500, {}, text="Internal Server Error"))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "failed" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_blob_404_surfaces_token_scope_hint(self):
        """A 404 on POST /git/blobs surfaces a token scope/visibility hint, not 'skipped'."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "c1"}}),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "404" in result["message"]
        assert "token scope" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_initial_commit_blob_404_surfaces_token_scope_hint(self):
        """Empty repo path: 404 on POST /git/blobs surfaces token scope hint."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # backup branch missing
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(404, {}),  # default branch missing -> empty repo
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "404" in result["message"]
        assert "token scope" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_initial_commit_blob_non_201_returns_path_in_message(self):
        """Empty repo path: non-201 on POST /git/blobs includes the file path in the failure message."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # backup branch missing
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(404, {}),  # default branch missing -> empty repo
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(500, {}, text="Internal Server Error"))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "a.json" in result["message"]


class TestGitHubBackendRobustness:
    """Coverage for the B18-B26 PR feedback round: GitHub backend.

    Targets failure paths that previously silent-failed or surfaced cryptic
    one-word strings to operators (KeyError on missing JSON keys, etc.).
    """

    def setup_method(self):
        self.backend = GitHubBackend()
        self.repo_url = "https://github.com/owner/repo"
        self.token = "ghp_token"
        self.branch = "bambuddy-backup"

    @pytest.mark.asyncio
    async def test_tree_fetch_failure_returns_failed_not_silent_skip(self):
        """B18: A non-200 tree GET must surface a clear failure with status code, not let
        the downstream blob POSTs fire with an empty existing_files map."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "c1"}}),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(500, {}, text="Internal Server Error"),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "existing tree" in result["message"]
        assert "500" in result["message"]
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_truncated_tree_response_returns_failed(self):
        """B24: GitHub's tree API truncates >7MB / >100k entries. A truncated map would
        miss SHAs and re-upload every file as new on each backup — fail loudly instead."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "c1"}}),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(
                    200, {"tree": [{"type": "blob", "path": "a.json", "sha": "old"}], "truncated": True}
                ),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "truncated" in result["message"].lower()
        assert "rotate the backup repository" in result["message"].lower()
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_ref_response_returns_clear_message(self):
        """B20: An unexpected ref body (no object.sha) surfaces a clear shape-error,
        not 'object' as the user-facing message via the catch-all."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {"unexpected": "shape"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "ref response" in result["message"].lower()
        assert "missing key 'object'" in result["message"]

    @pytest.mark.asyncio
    async def test_malformed_commit_response_returns_clear_message(self):
        """B20: An unexpected commit body (no tree.sha) surfaces a clear shape-error,
        not 'tree' as the user-facing message via the catch-all."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "c1"}}),
                _make_mock_response(200, {"sha": "c1"}),  # no top-level tree
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "commit response" in result["message"].lower()
        assert "missing key 'tree'" in result["message"]

    @pytest.mark.asyncio
    async def test_malformed_blob_response_returns_clear_message(self):
        """B20: A 201 blob response with no sha field surfaces shape-error, not KeyError."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "c1"}}),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"unexpected": "shape"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "blob response" in result["message"].lower()
        assert "a.json" in result["message"]

    @pytest.mark.asyncio
    async def test_create_branch_403_includes_status_code(self):
        """B19: A 403 on POST /git/refs surfaces the HTTP status code so the operator can
        tell 'no write scope' apart from generic upstream errors."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # backup branch missing
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, {"object": {"sha": "main-sha"}}),  # default branch ref
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(403, {"message": "Forbidden"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "failed"
        assert "403" in result["message"]
        assert "branch" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_create_branch_422_includes_status_code(self):
        """B19: 422 with empty body must still produce a diagnostic message — the previous
        assertion accepted either the status code OR a body substring, masking this gap."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # backup branch missing
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, {"object": {"sha": "main-sha"}}),  # default branch ref
            ]
        )
        # 422 with empty body — the assertion must rely on the status code, not the body
        client.post = AsyncMock(return_value=_make_mock_response(422, {}, text=""))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "failed"
        assert "422" in result["message"]

    @pytest.mark.asyncio
    async def test_test_connection_failure_includes_exception_message(self):
        """B23: A network exception during test_connection surfaces both the class name and
        the message (truncated), so the user clicking Test Connection sees actionable text
        like 'certificate verify failed', not just 'ConnectError'."""
        import httpx

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.ConnectError("certificate verify failed: unable to get local issuer certificate")
        )

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        assert "ConnectError" in result["message"]
        assert "certificate verify failed" in result["message"]

    @pytest.mark.asyncio
    async def test_test_connection_truncates_long_exception_message(self):
        """B23: The user-facing exception detail is bounded to 200 chars."""
        import httpx

        long_message = "x" * 500
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError(long_message))

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        # Total message ≈ "Connection failed: ConnectError: " (33) + 200 char detail
        assert len(result["message"]) < 300

    @pytest.mark.asyncio
    async def test_initial_commit_malformed_blob_response(self):
        """B20: _create_initial_commit: 201 blob response with no sha surfaces shape-error."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # backup branch missing
                _make_mock_response(200, {"default_branch": "main"}),
                _make_mock_response(404, {}),  # default branch missing -> empty repo
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"unexpected": "shape"}))

        result = await self.backend.push_files(
            self.repo_url, self.token, self.branch, {"seed.json": {"k": "v"}}, client
        )

        assert result["status"] == "failed"
        assert "blob response" in result["message"].lower()
        assert "seed.json" in result["message"]

    @pytest.mark.asyncio
    async def test_recursive_push_files_log_marker_on_branch_create(self, caplog):
        """B26: After a successful branch create, the re-entry into push_files emits an
        info-level marker so operators can correlate the second pass with the first."""
        import logging

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # first push: branch missing
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, {"object": {"sha": "main-sha"}}),  # default branch ref
                _make_mock_response(200, {"object": {"sha": "c1"}}),  # second push: branch ref
                _make_mock_response(200, {"tree": {"sha": "t1"}}),  # commit
                _make_mock_response(200, {"tree": []}),  # tree listing
            ]
        )
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {}),  # POST /git/refs (create branch)
                _make_mock_response(201, {"sha": "blob1"}),
                _make_mock_response(201, {"sha": "new-tree"}),
                _make_mock_response(201, {"sha": "new-commit"}),
            ]
        )
        client.patch = AsyncMock(return_value=_make_mock_response(200, {}))

        with caplog.at_level(logging.INFO, logger="backend.app.services.git_providers.github"):
            result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "success"
        assert any("Re-entering push_files" in r.message for r in caplog.records)


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

    @pytest.mark.asyncio
    async def test_n_files_produce_single_commit(self):
        """All changed files are bundled into one Contents API call."""
        files = {"a.json": {"k": "v1"}, "b.json": {"k": "v2"}}
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "base-commit"}}),
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-commit"}}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, files, client)

        assert result["status"] == "success"
        assert result["files_changed"] == 2
        contents_calls = [c for c in client.post.call_args_list if "/contents" in c.args[0]]
        assert len(contents_calls) == 1

    @pytest.mark.asyncio
    async def test_uses_gitea_api_v1_base_not_github(self):
        """Contents API calls target the instance's /api/v1, not api.github.com."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "base-commit"}}),
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-commit"}}))

        await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        first_get_url = client.get.call_args_list[0].args[0]
        assert "git.example.com/api/v1" in first_get_url
        assert "api.github.com" not in first_get_url

    @pytest.mark.asyncio
    async def test_skips_unchanged_files(self):
        """Files whose blob SHA matches the existing tree entry are excluded from the commit."""
        content = {"name": "my-printer"}
        sha = _blob_sha(content)

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "base-commit"}}),
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
                _make_mock_response(200, {"tree": [{"type": "blob", "path": "config/printers.json", "sha": sha}]}),
            ]
        )

        result = await self.backend.push_files(
            self.repo_url, self.token, self.branch, {"config/printers.json": content}, client
        )

        assert result["status"] == "skipped"
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_changed_file_sent_as_update_with_sha(self):
        """A file whose content changed is sent with operation='update' and the current blob SHA."""
        old_content = {"version": "1.0", "archives": []}
        new_content = {"version": "1.0", "archives": [{"id": 1}]}
        old_sha = _blob_sha(old_content)

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(
                    200, {"tree": [{"type": "blob", "path": "archives/print_history.json", "sha": old_sha}]}
                ),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-sha"}}))

        result = await self.backend.push_files(
            self.repo_url,
            self.token,
            self.branch,
            {"archives/print_history.json": new_content},
            client,
        )

        assert result["status"] == "success"
        assert result["files_changed"] == 1
        body = client.post.call_args.kwargs["json"]
        assert body["files"][0]["operation"] == "update"
        assert body["files"][0]["sha"] == old_sha

    @pytest.mark.asyncio
    async def test_new_file_sent_as_create_without_sha(self):
        """A file not yet in the repo is sent with operation='create' and no sha field."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-sha"}}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"new.json": {"k": "v"}}, client)

        assert result["status"] == "success"
        body = client.post.call_args.kwargs["json"]
        assert body["files"][0]["operation"] == "create"
        assert "sha" not in body["files"][0]

    @pytest.mark.asyncio
    async def test_unchanged_file_excluded_from_contents_call(self):
        """A file whose blob SHA matches the existing tree entry is not included in the Contents API call."""
        content = {"name": "printer-1"}
        sha = _blob_sha(content)

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": [{"type": "blob", "path": "config.json", "sha": sha}]}),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"config.json": content}, client)

        assert result["status"] == "skipped"
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_batch_create_update_unchanged_in_single_call(self):
        """A batch with one new file, one changed file, and one unchanged file produces
        exactly one create + one update in the Contents API payload, files_changed=2."""
        unchanged_content = {"version": 1}
        unchanged_sha = _blob_sha(unchanged_content)
        old_content = {"version": 1}
        old_sha = _blob_sha(old_content)

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(
                    200,
                    {
                        "tree": [
                            {"type": "blob", "path": "unchanged.json", "sha": unchanged_sha},
                            {"type": "blob", "path": "updated.json", "sha": old_sha},
                        ]
                    },
                ),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-sha"}}))

        result = await self.backend.push_files(
            self.repo_url,
            self.token,
            self.branch,
            {
                "unchanged.json": unchanged_content,  # same SHA — should be skipped
                "updated.json": {"version": 2},  # changed — should be update
                "new.json": {"created": True},  # new — should be create
            },
            client,
        )

        assert result["status"] == "success"
        assert result["files_changed"] == 2
        body = client.post.call_args.kwargs["json"]
        ops = {f["path"]: f for f in body["files"]}
        assert set(ops.keys()) == {"updated.json", "new.json"}
        assert ops["updated.json"]["operation"] == "update"
        assert ops["updated.json"]["sha"] == old_sha
        assert ops["new.json"]["operation"] == "create"
        assert "sha" not in ops["new.json"]

    @pytest.mark.asyncio
    async def test_tree_fetch_failure_returns_failed(self):
        """A non-200 tree GET surfaces a clear failure, not a downstream 422 from the Contents API."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(500, {}, text="Internal Server Error"),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "existing tree" in result["message"]
        assert "500" in result["message"]
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_contents_api_failure_returns_failed(self):
        """A non-2xx response from the Contents API returns status='failed', not 'skipped'."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(403, {}, text="Forbidden"))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "failed" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_contents_api_404_surfaces_version_hint(self):
        """A 404 from POST /contents surfaces a Gitea version hint, not a generic 'Not Found'."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "v1.18" in result["message"]
        assert "404" in result["message"]

    @pytest.mark.asyncio
    async def test_contents_api_409_surfaces_conflict_hint(self):
        """409 on POST /contents surfaces a conflict hint (covers web-UI edit, concurrent backup, path collision)."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(409, {}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "conflict" in result["message"].lower()
        assert "advanced concurrently" in result["message"]
        assert "next scheduled backup" in result["message"]

    @pytest.mark.asyncio
    async def test_replication_lag_guard_fires_on_second_404(self):
        """Branch 404 after successful creation (replication lag) returns a clear message, not infinite recursion."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # first push_files: branch missing
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, [{"object": {"sha": "main-sha"}}]),  # default branch ref
                _make_mock_response(404, {}),  # second push_files: branch still missing (lag)
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {}))  # POST /branches succeeds

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "failed"
        assert "replication lag" in result["message"]
        assert "next scheduled backup" in result["message"]

    @pytest.mark.asyncio
    async def test_malformed_tree_entry_skipped_valid_entry_retained(self):
        """A tree entry missing 'sha' is skipped; the valid entry is still compared for deduplication."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "abc"}}]),  # branch ref
                _make_mock_response(200, {"tree": {"sha": "tree-sha"}}),  # commit
                _make_mock_response(
                    200,
                    {
                        "tree": [  # tree listing
                            {"type": "blob", "path": "valid.json", "sha": "old-sha"},
                            {"type": "blob", "path": "broken.json"},  # missing sha
                        ]
                    },
                ),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-commit"}}))

        result = await self.backend.push_files(
            self.repo_url,
            self.token,
            self.branch,
            {"valid.json": {"changed": True}, "broken.json": {"x": 1}},
            client,
        )

        assert result["status"] == "success"
        assert result["files_changed"] == 2  # both files pushed (broken.json treated as new)

    @pytest.mark.asyncio
    async def test_truncated_tree_response_returns_failed(self):
        """B24: A truncated tree listing makes SHA-equality dedup miss; surface a failure
        asking the user to rotate the repo rather than silently re-uploading every file."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(
                    200, {"tree": [{"type": "blob", "path": "a.json", "sha": "old"}], "truncated": True}
                ),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "truncated" in result["message"].lower()
        assert "rotate the backup repository" in result["message"].lower()
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_current_commit_failure_includes_status_and_body(self):
        """B22: A 5xx on GET /git/commits surfaces both the status code and the body,
        not the bare 'Failed to get current commit' string."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(503, {}, text="Service Unavailable"),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "503" in result["message"]
        assert "current commit" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_missing_tree_sha_surfaces_body(self):
        """B22: 'Failed to extract tree SHA' now includes the (truncated) response body so
        a future Gitea shape-shift is debuggable from the failure message alone."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                # Neither flat .tree nor wrapped .commit.tree present
                _make_mock_response(
                    200,
                    {"sha": "c1", "url": "https://gitea.example.com/api/v1/.../c1"},
                    text='{"sha":"c1","url":"https://gitea.example.com/api/v1/.../c1"}',
                ),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "tree SHA" in result["message"]
        assert "gitea.example.com" in result["message"]  # body context included

    @pytest.mark.asyncio
    async def test_repo_info_failure_includes_status_and_body(self):
        """B22: 'Failed to get repo info' inside _create_branch_and_push now includes
        the status code and response body."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # backup branch missing -> branch-and-push path
                _make_mock_response(500, {}, text="Internal Server Error"),  # repo info
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "failed"
        assert "repo info" in result["message"].lower()
        assert "500" in result["message"]
        assert "Internal Server Error" in result["message"]

    @pytest.mark.asyncio
    async def test_recursive_push_files_log_marker_on_branch_create(self, caplog):
        """B26: After POST /branches succeeds, the re-entry into push_files emits an
        info-level marker so operators can debug second-pass failures (e.g. replication lag)."""
        import logging

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # first push: branch missing
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, [{"object": {"sha": "main-sha"}}]),  # default branch ref
                # second push pass:
                _make_mock_response(200, [{"object": {"sha": "c1"}}]),
                _make_mock_response(200, {"tree": {"sha": "t1"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {}),  # POST /branches
                _make_mock_response(201, {"commit": {"sha": "new-commit"}}),  # POST /contents
            ]
        )

        with caplog.at_level(logging.INFO, logger="backend.app.services.git_providers.gitea"):
            result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "success"
        assert any("Re-entering push_files" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_creates_missing_branch_via_branches_api(self):
        """A missing backup branch is created via POST /branches, not /git/refs."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                # branch ref missing
                _make_mock_response(404, {}),
                # repo info for default branch
                _make_mock_response(200, {"default_branch": "main"}),
                # default branch ref
                _make_mock_response(200, {"object": {"sha": "base-sha"}}),
                # second push_files call: branch now exists
                _make_mock_response(200, {"object": {"sha": "base-sha"}}),
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {}),  # POST /branches
                _make_mock_response(201, {"commit": {"sha": "new-commit"}}),  # POST /contents
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "success"
        branch_call = client.post.call_args_list[0]
        assert "/branches" in branch_call.args[0]
        assert "/git/refs" not in branch_call.args[0]
        assert branch_call.kwargs["json"]["new_branch_name"] == self.branch

    @pytest.mark.asyncio
    async def test_truncates_upstream_error_body_in_failure_message(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "base-commit"}}),
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(500, {}, text="x" * 500))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert result["message"] == f"Backup commit failed: {'x' * 197}..."


class TestGiteaBackendListShapeRefResponse:
    """#1224, #1225 regression: Gitea/Forgejo return refs as a *list*, not a dict.

    GitHub: ``GET /git/refs/heads/{branch}`` -> ``{"ref": ..., "object": {...}}``.
    Gitea/Forgejo: same endpoint -> ``[{"ref": ..., "object": {...}}]``.

    The pre-fix code did ``response.json()["object"]["sha"]`` on the Gitea path
    and crashed with ``list indices must be integers or slices, not str``.
    """

    def setup_method(self):
        self.backend = GiteaBackend()
        self.repo_url = "https://git.example.com/owner/repo"
        self.token = "gitea-token"
        self.branch = "bambuddy-backup"

    def test_ref_sha_extracts_from_list(self):
        assert self.backend._ref_sha([{"object": {"sha": "abc"}}]) == "abc"

    def test_ref_sha_still_accepts_dict_shape(self):
        # Defensive — if Gitea ever returns a dict (older versions, future change),
        # we don't want to break.
        assert self.backend._ref_sha({"object": {"sha": "abc"}}) == "abc"

    def test_ref_sha_raises_on_empty_list(self):
        with pytest.raises(ValueError):
            self.backend._ref_sha([])

    @pytest.mark.asyncio
    async def test_push_files_handles_list_shape_branch_ref(self):
        """The configured backup branch already exists — ref endpoint returns a list."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "base-commit"}}]),  # list shape
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-commit"}}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "success"
        assert result["commit_sha"] == "new-commit"

    @pytest.mark.asyncio
    async def test_create_branch_handles_list_shape_default_branch_ref(self):
        """Backup branch missing — must read default branch's ref, also list-shaped."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # missing backup branch
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, [{"object": {"sha": "main-sha"}}]),  # default branch ref (list)
                # second push_files() call — branch now exists
                _make_mock_response(200, [{"object": {"sha": "main-sha"}}]),
                _make_mock_response(200, {"tree": {"sha": "main-tree"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {}),  # POST /branches
                _make_mock_response(201, {"commit": {"sha": "new-commit"}}),  # POST /contents
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_create_branch_403_returns_permission_message(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # missing backup branch
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, [{"object": {"sha": "main-sha"}}]),  # default branch ref
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(403, {"message": "Forbidden"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "failed"
        assert "Permission denied" in result["message"]
        assert "write access" in result["message"]

    @pytest.mark.asyncio
    async def test_create_branch_409_returns_race_condition_message(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # missing backup branch
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, [{"object": {"sha": "main-sha"}}]),  # default branch ref
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(409, {"message": "Conflict"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "failed"
        assert "already exists" in result["message"]
        assert "race" in result["message"]

    @pytest.mark.asyncio
    async def test_create_branch_unexpected_status_includes_code_in_message(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # missing backup branch
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(200, [{"object": {"sha": "main-sha"}}]),  # default branch ref
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(422, {"message": "Unprocessable"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "failed"
        assert "422" in result["message"]


class TestGiteaBackendWrappedCommitResponse:
    """#1224 regression: Gitea wraps the GitCommit fields under ``commit``.

    GitHub's ``GET /git/commits/{sha}`` returns the unwrapped GitCommit schema
    (``tree`` at top level). Gitea's same-named endpoint returns the wrapped
    Commit schema where ``tree`` lives at ``commit.tree`` (Gitea 1.24+).

    Pre-fix code did ``commit_response.json()["tree"]["sha"]`` and raised
    ``KeyError: 'tree'`` on every backup *after* the initial one — surfaced to
    the user as the opaque ``Backup failed: 'tree'`` message.
    """

    def setup_method(self):
        self.backend = GiteaBackend()
        self.repo_url = "https://git.example.com/owner/repo"
        self.token = "gitea-token"
        self.branch = "bambuddy-backup"

    def test_commit_tree_sha_reads_flat_shape(self):
        """GitHub-compatible / older Gitea: ``tree`` at top level."""
        assert self.backend._commit_tree_sha({"tree": {"sha": "abc"}}) == "abc"

    def test_commit_tree_sha_reads_wrapped_shape(self):
        """Gitea 1.24+ / Forgejo: ``tree`` nested under ``commit``."""
        assert self.backend._commit_tree_sha({"sha": "c1", "commit": {"tree": {"sha": "abc"}}}) == "abc"

    def test_commit_tree_sha_returns_none_on_missing(self):
        assert self.backend._commit_tree_sha({"sha": "c1", "commit": {}}) is None
        assert self.backend._commit_tree_sha({}) is None

    @pytest.mark.asyncio
    async def test_push_files_handles_wrapped_commit_response(self):
        """Subsequent backup against Gitea 1.24+ — commit endpoint returns wrapped shape."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "base-commit"}}]),
                # Wrapped Gitea commit response — tree under "commit", not top level
                _make_mock_response(200, {"sha": "base-commit", "commit": {"tree": {"sha": "base-tree"}}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-commit"}}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "success"
        assert result["commit_sha"] == "new-commit"

    @pytest.mark.asyncio
    async def test_missing_commit_sha_in_push_response_surfaces_warning(self):
        """200/201 with no commit.sha -> success with a human-readable note, not silent None."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "base-commit"}}]),
                _make_mock_response(200, {"sha": "base-commit", "commit": {"tree": {"sha": "base-tree"}}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {}))  # no commit key

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "success"
        assert result["commit_sha"] is None
        assert "not reported" in result["message"]

    @pytest.mark.asyncio
    async def test_push_files_fails_cleanly_when_tree_sha_missing(self):
        """Defensive: malformed/unexpected commit response surfaces a clear error, not KeyError."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "base-commit"}}]),
                _make_mock_response(200, {"sha": "base-commit"}),  # no tree at all
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert "tree SHA" in result["message"]


class TestGiteaBackendEmptyRepoInitialCommit:
    """#1224 regression: Git Data API refuses writes against empty Gitea repos.

    GitHub accepts ``POST /git/blobs`` against an empty repo and creates the
    initial commit + branch. Gitea returns 404 on every blob/tree/commit POST
    until the repo has at least one commit. The fix is to use the Contents
    API (``POST /repos/.../contents``) which seeds the branch + initial
    commit in a single transaction.
    """

    def setup_method(self):
        self.backend = GiteaBackend()
        self.repo_url = "https://git.example.com/owner/repo"
        self.token = "gitea-token"
        self.branch = "main"

    @pytest.mark.asyncio
    async def test_empty_repo_uses_contents_api_not_git_data_api(self):
        files = {"config/printers.json": {"name": "p1"}, "config/spools.json": {"id": 1}}
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),  # backup branch missing
                _make_mock_response(200, {"default_branch": "main"}),  # repo info
                _make_mock_response(404, {}),  # default branch missing too -> empty repo
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "initial-sha"}}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, files, client)

        assert result["status"] == "success"
        assert result["files_changed"] == 2
        assert result["commit_sha"] == "initial-sha"

        contents_calls = [c for c in client.post.call_args_list if "/contents" in c.args[0]]
        blob_calls = [c for c in client.post.call_args_list if "/git/blobs" in c.args[0]]
        tree_calls = [c for c in client.post.call_args_list if "/git/trees" in c.args[0]]
        commit_calls = [c for c in client.post.call_args_list if "/git/commits" in c.args[0]]
        ref_calls = [c for c in client.post.call_args_list if "/git/refs" in c.args[0]]
        # Exactly one Contents API call, no Git Data API writes
        assert len(contents_calls) == 1
        assert len(blob_calls) == 0
        assert len(tree_calls) == 0
        assert len(commit_calls) == 0
        assert len(ref_calls) == 0

    @pytest.mark.asyncio
    async def test_contents_api_payload_shape(self):
        """The Contents API call must carry branch+new_branch+files in the documented shape."""
        files = {"a.json": {"k": "v"}, "nested/b.json": {"x": 1}}
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),
                _make_mock_response(200, {"default_branch": "main"}),
                _make_mock_response(404, {}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "abc"}}))

        await self.backend.push_files(self.repo_url, self.token, self.branch, files, client)

        body = client.post.call_args.kwargs["json"]
        assert body["branch"] == "main"
        assert body["new_branch"] == "main"
        assert body["message"].startswith("Initial Bambuddy backup")
        assert len(body["files"]) == 2
        paths = {f["path"] for f in body["files"]}
        assert paths == {"a.json", "nested/b.json"}
        for f in body["files"]:
            assert f["operation"] == "create"
            # Content is base64-encoded JSON of the original dict
            decoded = base64.b64decode(f["content"]).decode("utf-8")
            assert json.loads(decoded) == files[f["path"]]

    @pytest.mark.asyncio
    async def test_contents_api_failure_truncates_error_body(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),
                _make_mock_response(200, {"default_branch": "main"}),
                _make_mock_response(404, {}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(500, {}, text="x" * 500))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert result["message"] == f"Failed to create initial commit: {'x' * 197}..."

    @pytest.mark.asyncio
    async def test_empty_files_skips_contents_api_call(self):
        # Edge: nothing to commit -> don't make a useless Contents API call.
        client = AsyncMock()
        client.post = AsyncMock()

        result = await self.backend._create_initial_commit(
            client, {}, "https://git.example.com/api/v1", "owner", "repo", "main", {}
        )

        assert result["status"] == "skipped"
        assert result["files_changed"] == 0
        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_commit_sha_in_initial_commit_response_surfaces_warning(self):
        """200/201 with no commit.sha -> success with a human-readable note, not silent None."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),
                _make_mock_response(200, {"default_branch": "main"}),
                _make_mock_response(404, {}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {}))  # no commit key

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {}}, client)

        assert result["status"] == "success"
        assert result["commit_sha"] is None
        assert "not reported" in result["message"]


class TestForgejoInheritsGiteaFixes:
    """ForgejoBackend extends GiteaBackend with no overrides — must inherit both fixes."""

    @pytest.mark.asyncio
    async def test_forgejo_handles_list_shape_ref_response(self):
        backend = ForgejoBackend()
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, [{"object": {"sha": "base-commit"}}]),
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "new-commit"}}))

        result = await backend.push_files(
            "https://forgejo.example.com/owner/repo",
            "token",
            "bambuddy-backup",
            {"a.json": {"k": "v"}},
            client,
        )
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_forgejo_empty_repo_uses_contents_api(self):
        backend = ForgejoBackend()
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(404, {}),
                _make_mock_response(200, {"default_branch": "main"}),
                _make_mock_response(404, {}),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"commit": {"sha": "fj-sha"}}))

        result = await backend.push_files(
            "https://forgejo.example.com/owner/repo",
            "token",
            "main",
            {"a.json": {"k": "v"}},
            client,
        )
        assert result["status"] == "success"
        contents_calls = [c for c in client.post.call_args_list if "/contents" in c.args[0]]
        blob_calls = [c for c in client.post.call_args_list if "/git/blobs" in c.args[0]]
        assert len(contents_calls) == 1
        assert len(blob_calls) == 0


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


class TestForgejoTestConnection:
    """ForgejoBackend overrides test_connection to handle Forgejo v15+ 404-not-403 behaviour."""

    def setup_method(self):
        self.backend = ForgejoBackend()
        self.repo_url = "https://forgejo.example.com/owner/repo"
        self.token = "fj-token"

    @pytest.mark.asyncio
    async def test_valid_token_and_push_permission_returns_success(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"login": "user"}),
                _make_mock_response(200, {"full_name": "owner/repo", "permissions": {"push": True, "pull": True}}),
            ]
        )

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is True
        assert result["repo_name"] == "owner/repo"

    @pytest.mark.asyncio
    async def test_invalid_token_returns_clear_message_without_repo_call(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(401, {}))

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        assert result["message"] == "Invalid access token"
        assert client.get.call_count == 1  # only /user was called

    @pytest.mark.asyncio
    async def test_zero_scope_token_403_on_user_returns_scope_hint(self):
        """A 403 from /user (v15+ zero-scope token) returns a clear message without hitting the repo."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(403, {}))

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        assert "read:user scope" in result["message"]
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_unexpected_user_status_returns_status_code(self):
        """A non-200/401/403 response from /user (e.g. 429, 5xx) surfaces the status code."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(429, {}))

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        assert "429" in result["message"]
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_repo_404_after_valid_token_surfaces_v15_scope_hint(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"login": "user"}),
                _make_mock_response(404, {}),
            ]
        )

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        assert "v15" in result["message"]
        assert "scope" in result["message"]

    @pytest.mark.asyncio
    async def test_token_lacks_push_permission_returns_failed(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"login": "user"}),
                _make_mock_response(200, {"full_name": "owner/repo", "permissions": {"push": False, "pull": True}}),
            ]
        )

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        assert "push permission" in result["message"]
        assert result["repo_name"] == "owner/repo"

    @pytest.mark.asyncio
    async def test_non_404_api_error_returns_status_code(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"login": "user"}),
                _make_mock_response(500, {}),
            ]
        )

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        assert "API error: 500" in result["message"]

    @pytest.mark.asyncio
    async def test_connection_exception_includes_detail_not_just_classname(self):
        """B23: A connection exception surfaces both the exception class and its message,
        so 'Test Connection' in the UI shows actionable detail (e.g. cert verify failure)."""
        import httpx

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.ConnectError("certificate verify failed: hostname mismatch"),
        )

        result = await self.backend.test_connection(self.repo_url, self.token, client)

        assert result["success"] is False
        assert "ConnectError" in result["message"]
        assert "certificate verify failed" in result["message"]


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

    def test_parse_url_subgroup_https(self):
        namespace, repo = self.backend.parse_repo_url("https://gitlab.com/group/subgroup/project")
        assert namespace == "group/subgroup"
        assert repo == "project"

    def test_parse_url_deep_namespace_https(self):
        namespace, repo = self.backend.parse_repo_url("https://gitlab.com/myorg/team/api/backend")
        assert namespace == "myorg/team/api"
        assert repo == "backend"

    def test_parse_url_subgroup_ssh(self):
        namespace, repo = self.backend.parse_repo_url("git@gitlab.com:group/subgroup/project.git")
        assert namespace == "group/subgroup"
        assert repo == "project"

    @pytest.mark.asyncio
    async def test_push_files_encodes_subgroup_namespace_in_api_url(self):
        backend = GitLabBackend()
        repo_url = "https://gitlab.com/group/subgroup/project"
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"name": "bambuddy-backup"}),
                _make_mock_response(200, []),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"id": "abc123"}))

        await backend.push_files(repo_url, "token", "bambuddy-backup", {"f.json": {}}, client)

        called_url = client.get.call_args_list[0].args[0]
        assert "group%2Fsubgroup%2Fproject" in called_url


def _blob_sha(content: dict) -> str:
    content_bytes = json.dumps(content, indent=2, default=str).encode("utf-8")
    return hashlib.sha1(f"blob {len(content_bytes)}\0".encode() + content_bytes, usedforsecurity=False).hexdigest()


def _make_mock_response(status_code: int, body=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
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
                # tree page 1 → one blob whose sha matches current content
                _make_mock_response(200, [{"type": "blob", "path": "config/printers.json", "id": sha}]),
                # tree page 2 → empty, stop pagination
                _make_mock_response(200, []),
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
                _make_mock_response(200, []),  # page 2 empty, stop pagination
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(201, {"id": "abc123"}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "success"
        assert result["files_changed"] == 1
        client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncates_upstream_error_body_in_failure_message(self):
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"name": self.branch}),
                _make_mock_response(200, []),
            ]
        )
        client.post = AsyncMock(return_value=_make_mock_response(500, {}, text="x" * 500))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "failed"
        assert result["message"] == f"Failed to create commit: {'x' * 197}..."

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

    @pytest.mark.asyncio
    async def test_paginates_tree_to_find_unchanged_file_on_page_2(self):
        """Files beyond the first 100 are fetched; a file on page 2 is correctly skipped if unchanged."""
        sha = _blob_sha(self.files["config/printers.json"])
        page1_items = [{"type": "blob", "path": f"other{i}.json", "id": "aaa"} for i in range(100)]
        page2_items = [{"type": "blob", "path": f"more{i}.json", "id": "bbb"} for i in range(19)] + [
            {"type": "blob", "path": "config/printers.json", "id": sha}
        ]  # 120 total blobs across two pages

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"name": self.branch}),  # branch check
                _make_mock_response(200, page1_items),  # tree page 1
                _make_mock_response(200, page2_items),  # tree page 2
                _make_mock_response(200, []),  # tree page 3 empty, stop
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, self.files, client)

        assert result["status"] == "skipped"
        client.post.assert_not_called()
