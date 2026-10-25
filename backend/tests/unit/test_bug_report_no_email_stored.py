"""A bug report's email goes to the relay and is not kept locally.

Every submission, successful or not, was stored in ``bug_reports`` with the
reporter's email, and nothing ever read or purged that table. The email still
goes to the relay (it is how the report gets answered); the local row keeps
the outcome only, and the emails already stored are cleared at startup.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services.bug_report import submit_report

EMAIL = "reporter@example.invalid"


def _relay(response=None, error=None):
    client = AsyncMock()
    client.post = AsyncMock(side_effect=error) if error else AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _response(status, body=None):
    response = MagicMock(status_code=status)
    response.json.return_value = body or {}
    return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relay",
    [
        _relay(_response(200, {"success": True, "issue_number": 7, "issue_url": "https://example.invalid/7"})),
        _relay(_response(500)),
        _relay(error=httpx.ConnectError("down")),
        _relay(_response(200, {"success": False, "message": "refused"})),
    ],
    ids=["submitted", "relay-http-error", "relay-unreachable", "relay-refused"],
)
async def test_the_stored_report_has_no_email(relay):
    db = AsyncMock()
    db.add = MagicMock()
    session = MagicMock()
    session.return_value.__aenter__ = AsyncMock(return_value=db)
    session.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("backend.app.services.bug_report.httpx.AsyncClient", return_value=relay),
        patch("backend.app.services.bug_report.async_session", session),
        patch("backend.app.services.bug_report._rate_limit_timestamps", []),
        patch("backend.app.services.bug_report.BUG_REPORT_RELAY_URL", "https://example.invalid/api/bug-report"),
    ):
        await submit_report(description="It broke", reporter_email=EMAIL, screenshot_base64=None, support_info=None)

    stored = db.add.call_args.args[0]
    assert stored.reporter_email is None
    assert relay.post.await_args.kwargs["json"]["reporter_email"] == EMAIL


@pytest.mark.asyncio
async def test_emails_stored_before_are_cleared():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    import backend.app.models  # noqa: F401 - populate Base.metadata
    from backend.app.core.database import Base, _migrate_clear_bug_report_emails

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "INSERT INTO bug_reports (description, reporter_email, status, email_sent) VALUES ('x', :e, 'failed', 0)"
                ),
                {"e": EMAIL},
            )
            await _migrate_clear_bug_report_emails(conn)
            await _migrate_clear_bug_report_emails(conn)  # again at the next start: harmless

            emails = (await conn.execute(text("SELECT reporter_email FROM bug_reports"))).scalars().all()
        assert emails == [None]
    finally:
        await engine.dispose()
