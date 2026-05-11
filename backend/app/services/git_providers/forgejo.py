"""Forgejo backend — diverges from Gitea on token-scope validation (v15+)."""

import logging

import httpx

from backend.app.services.git_providers.gitea import GiteaBackend

logger = logging.getLogger(__name__)


class ForgejoBackend(GiteaBackend):
    """Backend for Forgejo instances.

    Forgejo v15+ returns 404 (not 403) for private repositories when the token
    lacks repository scope, requiring a /user pre-check to distinguish bad tokens
    from inaccessible repos. test_connection is overridden to handle this.
    Other methods are inherited from GiteaBackend unchanged.
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
            if user_resp.status_code == 403:
                return {
                    "success": False,
                    "message": "Token has no read:user scope; cannot validate identity",
                    "repo_name": None,
                    "permissions": None,
                }
            if user_resp.status_code != 200:
                return {
                    "success": False,
                    "message": f"Forgejo API error on /user: {user_resp.status_code}",
                    "repo_name": None,
                    "permissions": None,
                }

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
            logger.exception("Forgejo connection test failed")
            detail = str(e)[:200]
            message = (
                f"Connection failed: {type(e).__name__}: {detail}"
                if detail
                else f"Connection failed: {type(e).__name__}"
            )
            return {
                "success": False,
                "message": message,
                "repo_name": None,
                "permissions": None,
            }
