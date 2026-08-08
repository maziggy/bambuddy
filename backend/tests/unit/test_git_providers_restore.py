"""Unit tests for the git_providers read side used by restore (#2656).

Covers list_commits / list_tree / fetch_files across all four providers,
including that Gitea and Forgejo inherit GitHub's Git Data API implementation
rather than needing their own.
"""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.services.git_providers.forgejo import ForgejoBackend
from backend.app.services.git_providers.gitea import GiteaBackend
from backend.app.services.git_providers.github import GitHubBackend
from backend.app.services.git_providers.gitlab import GitLabBackend


def _make_mock_response(status_code: int, body=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json = MagicMock(return_value=body if body is not None else {})
    return resp


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode()


def _github_commit(sha: str, message: str = "Bambuddy backup", date: str = "2026-07-01T10:00:00Z"):
    return {"sha": sha, "commit": {"message": message, "author": {"name": "Bambuddy", "date": date}}}


class TestGitHubListCommits:
    def setup_method(self):
        self.backend = GitHubBackend()
        self.repo_url = "https://github.com/owner/repo"
        self.token = "ghp_token"

    @pytest.mark.asyncio
    async def test_returns_normalised_commits_newest_first(self):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(
                200,
                [
                    _github_commit("aaa111", "Bambuddy backup - newest", "2026-07-02T10:00:00Z"),
                    _github_commit("bbb222", "Bambuddy backup - older", "2026-07-01T10:00:00Z"),
                ],
            )
        )

        result = await self.backend.list_commits(self.repo_url, self.token, "main", client)

        assert result["success"] is True
        assert [c["sha"] for c in result["commits"]] == ["aaa111", "bbb222"]
        assert result["commits"][0]["message"] == "Bambuddy backup - newest"
        assert result["commits"][0]["author"] == "Bambuddy"
        assert result["commits"][0]["date"] == "2026-07-02T10:00:00Z"

    @pytest.mark.asyncio
    async def test_sends_both_per_page_and_limit(self):
        """GitHub honours per_page, Gitea honours limit — one call must carry both
        so GiteaBackend can inherit this method unchanged."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, []))

        await self.backend.list_commits(self.repo_url, self.token, "main", client, limit=7)

        params = client.get.await_args.kwargs["params"]
        assert params["per_page"] == 7
        assert params["limit"] == 7
        assert params["sha"] == "main"

    @pytest.mark.asyncio
    async def test_respects_limit_even_if_provider_overshoots(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, [_github_commit(f"sha{i}") for i in range(10)]))

        result = await self.backend.list_commits(self.repo_url, self.token, "main", client, limit=3)

        assert len(result["commits"]) == 3

    @pytest.mark.asyncio
    async def test_404_explains_empty_repository(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await self.backend.list_commits(self.repo_url, self.token, "nope", client)

        assert result["success"] is False
        assert "no commits yet" in result["message"]
        assert result["commits"] == []

    @pytest.mark.asyncio
    async def test_skips_entries_without_a_sha(self):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(200, [{"commit": {"message": "no sha"}}, _github_commit("good")])
        )

        result = await self.backend.list_commits(self.repo_url, self.token, "main", client)

        assert [c["sha"] for c in result["commits"]] == ["good"]

    @pytest.mark.asyncio
    async def test_non_list_body_is_an_error_not_a_crash(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {"unexpected": "shape"}))

        result = await self.backend.list_commits(self.repo_url, self.token, "main", client)

        assert result["success"] is False
        assert "Unexpected shape" in result["message"]


class TestGetCommit:
    """A ref older than the list window still needs a subject line and a date."""

    @pytest.mark.asyncio
    async def test_github_reads_one_commit_by_sha(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, _github_commit("abc1234567")))

        result = await GitHubBackend().get_commit("https://github.com/owner/repo", "tok", "abc1234567", client)

        assert result["success"] is True
        assert result["commit"] == {
            "sha": "abc1234567",
            "message": "Bambuddy backup",
            "author": "Bambuddy",
            "date": "2026-07-01T10:00:00Z",
        }
        assert "repos/owner/repo/commits/abc1234567" in client.get.await_args.args[0]

    @pytest.mark.asyncio
    async def test_github_404_names_the_ref(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await GitHubBackend().get_commit("https://github.com/owner/repo", "tok", "deadbee", client)

        assert result["success"] is False
        assert result["commit"] is None
        assert "deadbee" in result["message"]

    @pytest.mark.asyncio
    async def test_gitlab_reads_its_flattened_shape(self):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(
                200,
                {
                    "id": "abc1234567",
                    "message": "Bambuddy backup",
                    "author_name": "Bambuddy",
                    "committed_date": "2026-07-02T10:00:00Z",
                },
            )
        )

        result = await GitLabBackend().get_commit("https://gitlab.com/owner/repo", "tok", "abc1234567", client)

        assert result["commit"]["author"] == "Bambuddy"
        assert result["commit"]["date"] == "2026-07-02T10:00:00Z"

    @pytest.mark.asyncio
    async def test_gitlab_404_names_the_ref(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await GitLabBackend().get_commit("https://gitlab.com/owner/repo", "tok", "deadbee", client)

        assert result["success"] is False
        assert "deadbee" in result["message"]


class TestGitHubListTree:
    def setup_method(self):
        self.backend = GitHubBackend()
        self.repo_url = "https://github.com/owner/repo"
        self.token = "ghp_token"

    @pytest.mark.asyncio
    async def test_returns_sorted_blob_paths_only(self):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(
                200,
                {
                    "tree": [
                        {"type": "blob", "path": "spools/inventory.json", "sha": "s1"},
                        {"type": "tree", "path": "spools", "sha": "d1"},
                        {"type": "blob", "path": "backup_metadata.json", "sha": "m1"},
                    ]
                },
            )
        )

        result = await self.backend.list_tree(self.repo_url, self.token, "abc1234", client)

        assert result["success"] is True
        assert result["paths"] == ["backup_metadata.json", "spools/inventory.json"]

    @pytest.mark.asyncio
    async def test_truncated_tree_fails_loudly(self):
        """A truncated listing would make restore silently miss categories."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {"tree": [], "truncated": True}))

        result = await self.backend.list_tree(self.repo_url, self.token, "abc1234", client)

        assert result["success"] is False
        assert "truncated" in result["message"]

    @pytest.mark.asyncio
    async def test_404_names_the_missing_ref(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await self.backend.list_tree(self.repo_url, self.token, "deadbee", client)

        assert result["success"] is False
        assert "deadbee" in result["message"]


class TestGitHubFetchFiles:
    def setup_method(self):
        self.backend = GitHubBackend()
        self.repo_url = "https://github.com/owner/repo"
        self.token = "ghp_token"

    @pytest.mark.asyncio
    async def test_reads_requested_paths_via_blob_api(self):
        tree = _make_mock_response(
            200,
            {
                "tree": [
                    {"type": "blob", "path": "a.json", "sha": "sha-a"},
                    {"type": "blob", "path": "b.json", "sha": "sha-b"},
                ]
            },
        )
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                tree,
                _make_mock_response(200, {"content": _b64('{"a": 1}'), "encoding": "base64"}),
            ]
        )

        result = await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["a.json"], client)

        assert result["success"] is True
        assert result["files"] == {"a.json": '{"a": 1}'}
        # One tree listing regardless of how many files are read.
        assert client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_lists_the_tree_once_for_many_files(self):
        tree = _make_mock_response(
            200,
            {
                "tree": [
                    {"type": "blob", "path": "a.json", "sha": "sha-a"},
                    {"type": "blob", "path": "b.json", "sha": "sha-b"},
                ]
            },
        )
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                tree,
                _make_mock_response(200, {"content": _b64("1"), "encoding": "base64"}),
                _make_mock_response(200, {"content": _b64("2"), "encoding": "base64"}),
            ]
        )

        result = await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["a.json", "b.json"], client)

        assert result["files"] == {"a.json": "1", "b.json": "2"}
        assert client.get.await_count == 3

    @pytest.mark.asyncio
    async def test_a_supplied_blob_map_skips_the_second_tree_read(self):
        """list_tree already fetched this; fetching it again was a wasted GET."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {"content": _b64("1"), "encoding": "base64"}))

        result = await self.backend.fetch_files(
            self.repo_url, self.token, "abc1234", ["a.json"], client, blob_shas={"a.json": "sha-a"}
        )

        assert result["files"] == {"a.json": "1"}
        # The blob read and nothing else.
        assert client.get.await_count == 1
        assert "git/blobs/sha-a" in client.get.await_args.args[0]

    @pytest.mark.asyncio
    async def test_list_tree_hands_back_the_map_it_built(self):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(
                200,
                {
                    "tree": [
                        {"type": "blob", "path": "a.json", "sha": "sha-a"},
                        {"type": "tree", "path": "dir", "sha": "sha-d"},
                    ]
                },
            )
        )

        result = await self.backend.list_tree(self.repo_url, self.token, "abc1234", client)

        assert result["blob_shas"] == {"a.json": "sha-a"}

    @pytest.mark.asyncio
    async def test_missing_path_is_skipped_not_an_error(self):
        """Which categories a backup contains varies by config, so an absent
        path is expected rather than a failure."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {"tree": []}))

        result = await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["gone.json"], client)

        assert result["success"] is True
        assert result["files"] == {}

    @pytest.mark.asyncio
    async def test_blob_error_fails_the_whole_read(self):
        tree = _make_mock_response(200, {"tree": [{"type": "blob", "path": "a.json", "sha": "sha-a"}]})
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[tree, _make_mock_response(500, {}, text="boom")])

        result = await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["a.json"], client)

        assert result["success"] is False
        assert "a.json" in result["message"]
        assert result["files"] == {}

    @pytest.mark.asyncio
    async def test_utf8_content_survives_round_trip(self):
        payload = '{"color_name": "Jadeweiß", "note": "日本語"}'
        tree = _make_mock_response(200, {"tree": [{"type": "blob", "path": "a.json", "sha": "sha-a"}]})
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[tree, _make_mock_response(200, {"content": _b64(payload), "encoding": "base64"})]
        )

        result = await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["a.json"], client)

        assert result["files"]["a.json"] == payload

    @pytest.mark.asyncio
    async def test_unsupported_encoding_is_reported(self):
        tree = _make_mock_response(200, {"tree": [{"type": "blob", "path": "a.json", "sha": "sha-a"}]})
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[tree, _make_mock_response(200, {"content": "xx", "encoding": "quoted-printable"})]
        )

        result = await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["a.json"], client)

        assert result["success"] is False
        assert "Unsupported blob encoding" in result["message"]


class TestGiteaAndForgejoInheritReads:
    """Gitea overrides the *write* path, plus the one read that genuinely differs."""

    @pytest.mark.parametrize("backend_cls", [GiteaBackend, ForgejoBackend])
    def test_read_methods_are_not_overridden(self, backend_cls):
        for method in ("list_commits", "list_tree", "fetch_files", "get_commit"):
            assert getattr(backend_cls, method) is getattr(GitHubBackend, method)

    @pytest.mark.parametrize("backend_cls", [GiteaBackend, ForgejoBackend])
    def test_the_tree_read_is_paged_rather_than_inherited(self, backend_cls):
        """GitHub's trees endpoint is not paginated; Gitea's is (#2656)."""
        assert backend_cls._blob_shas_at is not GitHubBackend._blob_shas_at

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend_cls", [GiteaBackend, ForgejoBackend])
    async def test_a_paged_tree_is_read_to_the_end(self, backend_cls):
        """Inheriting GitHub's single GET read only the first page.

        The rest of the backup then looked absent from the commit, and the
        preview reported those categories as "not present" — a restore silently
        skipping data, which is exactly what GitHub's truncated=true check
        exists to prevent.
        """
        page1 = {
            "tree": [{"type": "blob", "path": f"f{i}.json", "sha": f"s{i}"} for i in range(1000)],
            "total_count": 1002,
        }
        page2 = {
            "tree": [
                {"type": "blob", "path": "settings/app_settings.json", "sha": "sx"},
                {"type": "tree", "path": "settings", "sha": "dx"},
            ],
            "total_count": 1002,
        }
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[_make_mock_response(200, page1), _make_mock_response(200, page2)])

        result = await backend_cls().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["success"] is True
        assert client.get.await_count == 2
        assert "settings/app_settings.json" in result["paths"]
        assert len(result["paths"]) == 1001

    @pytest.mark.asyncio
    async def test_a_single_page_tree_costs_one_request(self):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(
                200, {"tree": [{"type": "blob", "path": "a.json", "sha": "s1"}], "total_count": 1}
            )
        )

        result = await GiteaBackend().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["paths"] == ["a.json"]
        assert client.get.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend_cls", [GiteaBackend, ForgejoBackend])
    async def test_a_clamped_page_size_is_still_read_to_the_end(self, backend_cls):
        """Gitea clamps per_page to MAX_RESPONSE_ITEMS — 50 by default (#2656).

        Paging off the *requested* 1000 made page 2 believe it had seen 1050
        entries, which clears any total_count below that. The loop then returned
        the first 100 entries of a 120-entry tree as a success, and the restore
        reported the categories it could not see as absent from the commit.
        """
        clamped = 50
        total = 120
        pages = []
        for start in range(0, total, clamped):
            count = min(clamped, total - start)
            pages.append(
                _make_mock_response(
                    200,
                    {
                        "tree": [
                            {"type": "blob", "path": f"f{i}.json", "sha": f"s{i}"} for i in range(start, start + count)
                        ],
                        "total_count": total,
                    },
                )
            )
        client = AsyncMock()
        client.get = AsyncMock(side_effect=pages)

        result = await backend_cls().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["success"] is True
        assert client.get.await_count == 3
        assert len(result["paths"]) == total
        assert "f119.json" in result["paths"], "the tail of the tree is what a clamped pager loses"

    # --- a response with no usable total_count must not fail open -----------
    #
    # The pager used to short-circuit into a *success* holding page 1 whenever
    # total_count was missing or not an int — 50 entries of an arbitrarily large
    # tree under Gitea's default clamp. The restore then reported the categories
    # it could not see as "not present in this backup commit", the same silent
    # skip this whole override exists to prevent. GitHub and GitLab both
    # hard-fail in the equivalent spot; only Gitea guessed.

    @staticmethod
    def _page(start, count, **extra):
        return _make_mock_response(
            200,
            {
                "tree": [{"type": "blob", "path": f"f{i}.json", "sha": f"s{i}"} for i in range(start, start + count)],
                **extra,
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend_cls", [GiteaBackend, ForgejoBackend])
    async def test_a_countless_response_is_paged_to_the_end(self, backend_cls):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[self._page(0, 50), self._page(50, 50), self._page(100, 0)])

        result = await backend_cls().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["success"] is True
        assert client.get.await_count == 3
        assert len(result["paths"]) == 100
        assert "f99.json" in result["paths"], "the tail is what a fail-open pager loses"

    @pytest.mark.asyncio
    async def test_a_countless_short_page_ends_the_paging(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[self._page(0, 50), self._page(50, 7)])

        result = await GiteaBackend().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["success"] is True
        assert client.get.await_count == 2
        assert len(result["paths"]) == 57

    @pytest.mark.asyncio
    async def test_a_countless_single_page_tree_still_costs_one_request(self):
        """Control: a small tree must not pay for the fix."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=self._page(0, 3))

        result = await GiteaBackend().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["paths"] == ["f0.json", "f1.json", "f2.json"]
        assert client.get.await_count == 1

    @pytest.mark.asyncio
    async def test_a_non_int_total_count_is_treated_as_no_count(self):
        """The arm the code was written to defend against, and then trusted."""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[self._page(0, 50, total_count="120"), self._page(50, 4)])

        result = await GiteaBackend().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["success"] is True
        assert client.get.await_count == 2
        assert len(result["paths"]) == 54

    @pytest.mark.asyncio
    async def test_a_countless_tree_beyond_the_page_cap_still_fails(self):
        """The page ceiling is what keeps "page until short" from truncating."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=self._page(0, 1000))

        result = await GiteaBackend().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["success"] is False
        assert "listing limit" in result["message"]

    @pytest.mark.asyncio
    async def test_a_tree_beyond_the_page_cap_fails_rather_than_truncating(self):
        page = {"tree": [{"type": "blob", "path": f"f{i}.json", "sha": f"s{i}"} for i in range(1000)]}
        page["total_count"] = 10_000_000
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, page))

        result = await GiteaBackend().list_tree("https://git.example.com/owner/repo", "tok", "abc1234", client)

        assert result["success"] is False
        assert "listing limit" in result["message"]

    @pytest.mark.asyncio
    async def test_a_missing_ref_is_still_named(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await GiteaBackend().list_tree("https://git.example.com/owner/repo", "tok", "deadbee", client)

        assert result["success"] is False
        assert "deadbee" in result["message"]

    @pytest.mark.asyncio
    async def test_gitea_list_commits_uses_its_own_api_base(self):
        backend = GiteaBackend()
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, [_github_commit("abc")]))

        result = await backend.list_commits("https://git.example.com/owner/repo", "tok", "main", client)

        assert result["success"] is True
        url = client.get.await_args.args[0]
        assert url.startswith("https://git.example.com/api/v1/repos/owner/repo/commits")

    @pytest.mark.asyncio
    async def test_gitea_subpath_install_is_respected(self):
        """Gitea/Forgejo behind a ROOT_URL sub-path (#2642)."""
        backend = GiteaBackend()
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {"tree": []}))

        client.get = AsyncMock(return_value=_make_mock_response(200, {"tree": [], "total_count": 0}))
        await backend.list_tree("https://example.com/git/owner/repo", "tok", "abc1234", client)

        url = client.get.await_args.args[0]
        assert "/git/api/v1/repos/owner/repo/git/trees/abc1234" in url


class TestGitLabReads:
    def setup_method(self):
        self.backend = GitLabBackend()
        self.repo_url = "https://gitlab.com/owner/repo"
        self.token = "glpat-test"

    @pytest.mark.asyncio
    async def test_list_commits_reads_flattened_author_fields(self):
        """GitLab puts message/author/date on the entry, not under 'commit'."""
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(
                200,
                [
                    {
                        "id": "abc123",
                        "message": "Bambuddy backup",
                        "author_name": "Bambuddy",
                        "committed_date": "2026-07-02T10:00:00Z",
                    }
                ],
            )
        )

        result = await self.backend.list_commits(self.repo_url, self.token, "main", client)

        assert result["success"] is True
        assert result["commits"] == [
            {
                "sha": "abc123",
                "message": "Bambuddy backup",
                "author": "Bambuddy",
                "date": "2026-07-02T10:00:00Z",
            }
        ]

    @pytest.mark.asyncio
    async def test_list_commits_uses_ref_name(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, []))

        await self.backend.list_commits(self.repo_url, self.token, "bambuddy-backup", client, limit=5)

        params = client.get.await_args.kwargs["params"]
        assert params["ref_name"] == "bambuddy-backup"
        assert params["per_page"] == 5

    @pytest.mark.asyncio
    async def test_subgroup_path_is_url_encoded(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, []))

        await self.backend.list_commits("https://gitlab.com/group/subgroup/proj", self.token, "main", client)

        url = client.get.await_args.args[0]
        assert "projects/group%2Fsubgroup%2Fproj/repository/commits" in url

    @pytest.mark.asyncio
    async def test_list_tree_returns_blob_paths(self):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(
                200,
                [
                    {"type": "blob", "path": "spools/inventory.json"},
                    {"type": "tree", "path": "spools"},
                ],
            )
        )

        result = await self.backend.list_tree(self.repo_url, self.token, "abc1234", client)

        assert result["success"] is True
        assert result["paths"] == ["spools/inventory.json"]

    @pytest.mark.asyncio
    async def test_list_tree_follows_pagination(self):
        """GitLab paginates instead of exposing a truncated flag."""
        full_page = [{"type": "blob", "path": f"f{i}.json"} for i in range(100)]
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, full_page),
                _make_mock_response(200, [{"type": "blob", "path": "last.json"}]),
            ]
        )

        result = await self.backend.list_tree(self.repo_url, self.token, "abc1234", client)

        assert client.get.await_count == 2
        assert len(result["paths"]) == 101
        assert "last.json" in result["paths"]

    @pytest.mark.asyncio
    async def test_hitting_the_page_cap_is_a_failure_not_a_partial_list(self):
        """The mirror image of GitHub's truncated=true check.

        Falling out of the `while page <= 50` condition used to return
        success: True with a silently partial path list, which the restore then
        reported as "those categories are not present in this commit" — data
        skipped without anyone being told.
        """
        full_page = [{"type": "blob", "path": f"f{i}.json"} for i in range(100)]
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, full_page))

        result = await self.backend.list_tree(self.repo_url, self.token, "abc1234", client)

        assert result["success"] is False
        assert result["paths"] == []
        assert "cannot be enumerated reliably" in result["message"]

    @pytest.mark.asyncio
    async def test_list_tree_returns_no_blob_map(self):
        """GitLab reads files by path, so there is nothing to share."""
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, [{"type": "blob", "path": "a.json"}]))

        result = await self.backend.list_tree(self.repo_url, self.token, "abc1234", client)

        assert result["blob_shas"] == {}

    @pytest.mark.asyncio
    async def test_fetch_files_ignores_a_blob_map(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {"content": _b64("1"), "encoding": "base64"}))

        result = await self.backend.fetch_files(
            self.repo_url, self.token, "abc1234", ["a.json"], client, blob_shas={"a.json": "irrelevant"}
        )

        assert result["files"] == {"a.json": "1"}
        assert "repository/files/a.json" in client.get.await_args.args[0]

    @pytest.mark.asyncio
    async def test_fetch_files_decodes_base64(self):
        client = AsyncMock()
        client.get = AsyncMock(
            return_value=_make_mock_response(200, {"content": _b64('{"k": 1}'), "encoding": "base64"})
        )

        result = await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["a.json"], client)

        assert result["success"] is True
        assert result["files"] == {"a.json": '{"k": 1}'}

    @pytest.mark.asyncio
    async def test_fetch_files_encodes_nested_path(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(200, {"content": _b64("{}"), "encoding": "base64"}))

        await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["spools/inventory.json"], client)

        url = client.get.await_args.args[0]
        assert "repository/files/spools%2Finventory.json" in url

    @pytest.mark.asyncio
    async def test_fetch_files_skips_404(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=_make_mock_response(404, {}))

        result = await self.backend.fetch_files(self.repo_url, self.token, "abc1234", ["gone.json"], client)

        assert result["success"] is True
        assert result["files"] == {}
