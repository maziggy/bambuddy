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
        """All changed files are bundled into one commit via the Git Data API."""
        files = {"a.json": {"k": "v1"}, "b.json": {"k": "v2"}}
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "base-commit"}}),
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
                _make_mock_response(200, {"tree": []}),
            ]
        )
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {"sha": "blob1"}),
                _make_mock_response(201, {"sha": "blob2"}),
                _make_mock_response(201, {"sha": "new-tree"}),
                _make_mock_response(201, {"sha": "new-commit"}),
            ]
        )
        client.patch = AsyncMock(return_value=_make_mock_response(200, {}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, files, client)

        assert result["status"] == "success"
        assert result["files_changed"] == 2
        commit_calls = [c for c in client.post.call_args_list if "/git/commits" in c.args[0]]
        assert len(commit_calls) == 1

    @pytest.mark.asyncio
    async def test_uses_gitea_api_v1_base_not_github(self):
        """Git Data API calls target the instance's /api/v1, not api.github.com."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, {"object": {"sha": "base-commit"}}),
                _make_mock_response(200, {"tree": {"sha": "base-tree"}}),
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
    async def test_creates_missing_branch_via_git_refs_api(self):
        """A missing backup branch is created via the Git Data API refs endpoint."""
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
                _make_mock_response(201, {}),  # create ref
                _make_mock_response(201, {"sha": "blob1"}),
                _make_mock_response(201, {"sha": "new-tree"}),
                _make_mock_response(201, {"sha": "new-commit"}),
            ]
        )
        client.patch = AsyncMock(return_value=_make_mock_response(200, {}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "success"
        ref_create_call = client.post.call_args_list[0]
        assert "/git/refs" in ref_create_call.args[0]
        assert ref_create_call.kwargs["json"]["ref"] == f"refs/heads/{self.branch}"

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
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {"sha": "blob1"}),
                _make_mock_response(500, {}, text="x" * 500),
            ]
        )

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "failed"
        assert result["message"] == f"Failed to create tree: {'x' * 197}..."


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
                _make_mock_response(201, {}),  # create branch ref
                _make_mock_response(201, {"sha": "blob1"}),
                _make_mock_response(201, {"sha": "new-tree"}),
                _make_mock_response(201, {"sha": "new-commit"}),
            ]
        )
        client.patch = AsyncMock(return_value=_make_mock_response(200, {}))

        result = await self.backend.push_files(self.repo_url, self.token, self.branch, {"a.json": {"k": "v"}}, client)

        assert result["status"] == "success"


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
        client.post = AsyncMock(
            side_effect=[
                _make_mock_response(201, {"sha": "blob1"}),
                _make_mock_response(201, {"sha": "new-tree"}),
                _make_mock_response(201, {"sha": "new-commit"}),
            ]
        )
        client.patch = AsyncMock(return_value=_make_mock_response(200, {}))

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
