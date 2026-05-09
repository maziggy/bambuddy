"""Forgejo backend — currently API-compatible with Gitea (/api/v1)."""

import logging

import httpx

from backend.app.services.git_providers.gitea import GiteaBackend

logger = logging.getLogger(__name__)


class ForgejoBackend(GiteaBackend):
    """Backend for Forgejo instances.

    Currently API-compatible with Gitea (/api/v1). Override methods here
    as the two projects' APIs diverge.
    """

    async def test_connection(self, repo_url: str, token: str, client: httpx.AsyncClient) -> dict:
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            # Verify token validity before hitting the repo. On Forgejo v15+,
            # private repos return 404 (not 403) when the token lacks repo scope,
            # so we must distinguish "bad token" from "token OK but repo not visible".
            user_resp = await client.get(f"{api_base}/user", headers=headers)
            if user_resp.status_code == 401:
                return {"success": False, "message": "Invalid access token", "repo_name": None, "permissions": None}

            repo_resp = await client.get(f"{api_base}/repos/{owner}/{repo}", headers=headers)

            if repo_resp.status_code == 404:
                return {
                    "success": False,
                    "message": (
                        "Repository not found or token cannot access it. "
                        "On Forgejo v15+, private repositories return 404 (not 403) "
                        "when the token lacks repository scope."
                    ),
                    "repo_name": None,
                    "permissions": None,
                }

            if repo_resp.status_code != 200:
                return {
                    "success": False,
                    "message": f"API error: {repo_resp.status_code}",
                    "repo_name": None,
                    "permissions": None,
                }

            data = repo_resp.json()
            permissions = data.get("permissions", {})

            if not permissions.get("push", False):
                return {
                    "success": False,
                    "message": "Token does not have push permission to this repository",
                    "repo_name": data.get("full_name"),
                    "permissions": permissions,
                }

            return {
                "success": True,
                "message": "Connection successful",
                "repo_name": data.get("full_name"),
                "permissions": permissions,
            }

        except Exception as e:
            logger.error("Forgejo connection test failed: %s", e)
            return {"success": False, "message": f"Connection failed: {type(e).__name__}", "repo_name": None, "permissions": None}
