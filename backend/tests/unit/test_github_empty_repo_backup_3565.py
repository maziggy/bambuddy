"""A first Git backup to a GitHub repository with no commits (#3565).

GitHub answers every Git Data call -- the branch lookup, but also blobs and
trees -- with 409 "Git Repository is empty" until the repository has a commit.
Bambuddy read the 409 on the branch lookup as a failure, and its blob/tree
initial-commit path could not have started the repository either. The first
commit now goes through the Contents API, and the rest follows as usual.
"""

import base64
import json
from unittest.mock import AsyncMock

import pytest

from backend.app.services.git_providers.github import GitHubBackend
from backend.tests.unit.test_git_providers import _blob_sha, _make_mock_response

pytestmark = pytest.mark.unit

REPO_URL = "https://github.com/owner/repo"
EMPTY = {"message": "Git Repository is empty.", "status": "409"}
FILES = {"settings.json": {"a": 1}, "printers.json": [{"name": "X1C"}]}


def _empty_409():
    return _make_mock_response(409, EMPTY)


def _client(*, get, put=(), post=(), patch=None):
    client = AsyncMock()
    client.get = AsyncMock(side_effect=list(get))
    client.put = AsyncMock(side_effect=list(put))
    client.post = AsyncMock(side_effect=list(post))
    client.patch = AsyncMock(return_value=patch or _make_mock_response(200, {}))
    return client


def _branch_with(*paths):
    """The three reads push_files makes on a branch holding these backup files."""
    tree = [{"type": "blob", "path": p, "sha": _blob_sha(FILES[p])} for p in paths]
    return [
        _make_mock_response(200, {"object": {"sha": "c0"}}),
        _make_mock_response(200, {"tree": {"sha": "t0"}}),
        _make_mock_response(200, {"tree": tree}),
    ]


def _seeded(sha="c0"):
    return _make_mock_response(201, {"commit": {"sha": sha}, "content": {}})


async def _push(client, files=FILES, branch="main"):
    return await GitHubBackend().push_files(REPO_URL, "ghp_token", branch, files, client)


class TestTheFirstBackup:
    @pytest.mark.asyncio
    async def test_the_reported_case_creates_the_first_commit_and_pushes_the_rest(self):
        client = _client(
            get=[_empty_409(), *_branch_with("settings.json")],
            put=[_seeded()],
            post=[
                _make_mock_response(201, {"sha": "blob-printers"}),
                _make_mock_response(201, {"sha": "t1"}),
                _make_mock_response(201, {"sha": "c1"}),
            ],
        )

        result = await _push(client)

        assert result["status"] == "success"
        assert result["files_changed"] == 2
        assert result["commit_sha"] == "c1"
        # The first commit through the Contents API, on the configured branch,
        # with the file exactly as push_files writes it, so it is not re-sent.
        url = client.put.await_args.args[0]
        body = client.put.await_args.kwargs["json"]
        assert url == "https://api.github.com/repos/owner/repo/contents/settings.json"
        assert body["branch"] == "main"
        assert base64.b64decode(body["content"]) == json.dumps(FILES["settings.json"], indent=2).encode()
        # The second file is the only blob: the seeded one was recognised.
        blob_posts = [c for c in client.post.await_args_list if c.args[0].endswith("/git/blobs")]
        assert len(blob_posts) == 1

    @pytest.mark.asyncio
    async def test_a_one_file_backup_is_done_by_the_first_commit(self):
        client = _client(get=[_empty_409()], put=[_seeded()])

        result = await _push(client, files={"settings.json": FILES["settings.json"]})

        assert result == {
            "status": "success",
            "message": "Initial backup created - 1 files",
            "commit_sha": "c0",
            "files_changed": 1,
        }
        client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_backup_branch_github_will_not_create_branches_off_the_default_one(self):
        files = {"settings.json": FILES["settings.json"]}
        client = _client(
            get=[
                _empty_409(),
                # After the commit on the default branch: backup branch missing,
                # so it is branched off the default one and pushed to again.
                _make_mock_response(404, {}),
                _make_mock_response(200, {"default_branch": "main"}),
                _make_mock_response(200, {"object": {"sha": "c0"}}),
                *_branch_with("settings.json"),
            ],
            put=[_make_mock_response(404, {"message": "Branch bambuddy-backup not found"}), _seeded()],
            post=[_make_mock_response(201, {"ref": "refs/heads/bambuddy-backup"})],
        )

        result = await _push(client, files=files, branch="bambuddy-backup")

        assert result["status"] == "success"
        first, second = client.put.await_args_list
        assert first.kwargs["json"]["branch"] == "bambuddy-backup"
        assert "branch" not in second.kwargs["json"]
        assert client.post.await_args.kwargs["json"] == {"ref": "refs/heads/bambuddy-backup", "sha": "c0"}

    @pytest.mark.asyncio
    async def test_an_empty_default_branch_behind_a_missing_backup_branch_is_seeded_too(self):
        client = _client(
            get=[
                _make_mock_response(404, {}),
                _make_mock_response(200, {"default_branch": "main"}),
                _empty_409(),
            ],
            put=[_seeded()],
        )

        result = await _push(client, files={"settings.json": FILES["settings.json"]}, branch="bambuddy-backup")

        assert result["status"] == "success"
        client.post.assert_not_awaited()


class TestWhatStaysAFailure:
    @pytest.mark.asyncio
    async def test_another_409_is_reported_not_seeded(self):
        client = _client(get=[_make_mock_response(409, {"message": "Conflict"}, text="Conflict")])

        result = await _push(client)

        assert result["status"] == "failed"
        assert "Failed to get branch ref (HTTP 409)" in result["message"]
        client.put.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_refused_first_commit_says_why(self):
        client = _client(
            get=[_empty_409()],
            put=[_make_mock_response(403, {}, text="Resource not accessible by personal access token")],
        )

        result = await _push(client)

        assert result["status"] == "failed"
        assert "HTTP 403" in result["message"]
        assert "personal access token" in result["message"]

    @pytest.mark.asyncio
    async def test_a_repository_still_empty_after_the_first_commit_is_not_seeded_twice(self):
        """GitHub lagging behind its own write: the next backup retries."""
        client = _client(get=[_empty_409(), _empty_409()], put=[_seeded()])

        result = await _push(client)

        assert result["status"] == "failed"
        assert "replication lag" in result["message"]
        assert client.put.await_count == 1
