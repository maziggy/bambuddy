"""Forgejo backend — diverges from Gitea on token-scope validation (v15+)."""

import logging

import httpx

from backend.app.services.git_providers.gitea import GiteaBackend

logger = logging.getLogger(__name__)


class ForgejoBackend(GiteaBackend):
    """Backend for Forgejo instances.

    Forgejo v15+ returns 404 (not 403) for private repositories when the token
    lacks repository scope, so a bare repo call cannot tell "bad token" from
    "repo not visible" on its own. test_connection probes /user first to catch
    the outright-rejected token, then lets the repo call decide everything else.
    Other methods are inherited from GiteaBackend unchanged.
    """

    async def test_connection(self, repo_url: str, token: str, client: httpx.AsyncClient) -> dict:
        try:
            owner, repo = self.parse_repo_url(repo_url)
            api_base = self.get_api_base(repo_url)
            headers = self.get_headers(token)

            # Probe /user, but only a 401 here is conclusive: the instance rejects
            # the token outright, and saying so beats the 404 the repo call may
            # answer with instead (Forgejo v15+ hides private repos behind 404
            # rather than 403).
            #
            # Every other status falls through to the repo check (#2775). A
            # repository-scoped token — the kind Forgejo v15 recommends, limited
            # to one repo — can only carry read/write:issue and
            # read/write:repository, so /user answers 403 for exactly the tokens
            # worth encouraging. Treating that as fatal rejected a token that
            # reaches its own repository perfectly well, which is all a backup
            # needs: the push path uses the Contents API and the restore path
            # reads commits, trees and blobs, all under /repos/{owner}/{repo}.
            user_resp = await client.get(f"{api_base}/user", headers=headers)
            if user_resp.status_code == 401:
                return {"success": False, "message": "Invalid access token", "repo_name": None, "permissions": None}
            # Whether the token's identity was confirmed. Only used to word the
            # 404 below — an unconfirmed identity leaves "the token is invalid"
            # on the list of causes, a confirmed one rules it out.
            identity_confirmed = user_resp.status_code == 200

            repo_resp = await client.get(f"{api_base}/repos/{owner}/{repo}", headers=headers)

            if repo_resp.status_code == 401:
                return {"success": False, "message": "Invalid access token", "repo_name": None, "permissions": None}

            if repo_resp.status_code == 404:
                message = (
                    "Repository not found or token cannot access it. "
                    "On Forgejo v15+, private repositories return 404 (not 403) "
                    "when the token lacks repository scope. Check that the token has "
                    "write:repository, and that this repository is one it covers if the "
                    "token is scoped to specific repositories."
                )
                if not identity_confirmed:
                    message += " The token itself may also be invalid or expired."
                return {
                    "success": False,
                    "message": message,
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
            is_private = bool(data.get("private", False))

            if not permissions.get("push", False):
                return {
                    "success": False,
                    "message": "Token does not have push permission to this repository",
                    "repo_name": data.get("full_name"),
                    "permissions": permissions,
                    "is_private": is_private,
                }

            return {
                "success": True,
                "message": "Connection successful",
                "repo_name": data.get("full_name"),
                "permissions": permissions,
                "is_private": is_private,
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
                "is_private": None,
            }
