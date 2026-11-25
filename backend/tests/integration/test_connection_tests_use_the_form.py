"""The LDAP and SMTP "Test" buttons test what the form holds, not only what was saved.

A password left empty in the form keeps the saved one, which still only goes
to the server it was entered for.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

LDAP_SAVED = {
    "ldap_server_url": "ldaps://ldap.example.com",
    "ldap_bind_dn": "cn=svc,dc=example,dc=com",
    "ldap_bind_password": "saved-bind-secret",
    "ldap_search_base": "dc=example,dc=com",
    "ldap_security": "ldaps",
}

SMTP_SAVED = {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_username": "mailer@example.com",
    "smtp_password": "saved-smtp-secret",
    "smtp_security": "starttls",
    "smtp_auth_enabled": True,
    "smtp_from_email": "noreply@example.com",
}


async def _ldap_test(async_client: AsyncClient, body: dict | None):
    tested = []

    def fake_test(config):
        tested.append(config)
        return True, "ok"

    with patch("backend.app.services.ldap_service.test_ldap_connection", side_effect=fake_test):
        if body is None:
            response = await async_client.post("/api/v1/auth/ldap/test")
        else:
            response = await async_client.post("/api/v1/auth/ldap/test", json=body)
    assert response.status_code == 200, response.text
    return response.json(), tested


@pytest.fixture
async def ldap_saved(async_client: AsyncClient):
    response = await async_client.put("/api/v1/settings/", json=LDAP_SAVED)
    assert response.status_code == 200, response.text


class TestLdap:
    @pytest.mark.asyncio
    async def test_tests_the_form_values(self, async_client, ldap_saved):
        result, tested = await _ldap_test(
            async_client,
            {
                **LDAP_SAVED,
                "ldap_bind_dn": "cn=other,dc=example,dc=com",
                "ldap_bind_password": "typed-secret",
                "ldap_search_base": "ou=people,dc=example,dc=com",
            },
        )

        assert result["success"] is True
        assert tested[0].bind_dn == "cn=other,dc=example,dc=com"
        assert tested[0].bind_password == "typed-secret"
        assert tested[0].search_base == "ou=people,dc=example,dc=com"

    @pytest.mark.asyncio
    async def test_an_empty_password_keeps_the_saved_one_for_the_same_server(self, async_client, ldap_saved):
        _, tested = await _ldap_test(async_client, {**LDAP_SAVED, "ldap_bind_password": ""})

        assert tested[0].bind_password == "saved-bind-secret"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "changed",
        [{"ldap_server_url": "ldaps://elsewhere.example.net"}, {"ldap_security": "starttls"}],
    )
    async def test_the_saved_password_never_goes_to_another_server(self, async_client, ldap_saved, changed):
        result, tested = await _ldap_test(async_client, {**LDAP_SAVED, "ldap_bind_password": "", **changed})

        assert result["success"] is False
        assert "again" in result["message"]
        assert tested == []

    @pytest.mark.asyncio
    async def test_without_a_body_it_tests_the_saved_settings(self, async_client, ldap_saved):
        _, tested = await _ldap_test(async_client, None)

        assert tested[0].server_url == "ldaps://ldap.example.com"
        assert tested[0].bind_password == "saved-bind-secret"

    @pytest.mark.asyncio
    async def test_a_saved_ca_certificate_is_used_while_ldap_is_off(self, async_client, db_session, ldap_saved):
        from backend.app.models.settings import Settings

        db_session.add(Settings(key="ldap_ca_cert_path", value="/certs/ca.pem"))
        await db_session.commit()

        _, tested = await _ldap_test(async_client, {**LDAP_SAVED, "ldap_bind_password": ""})

        assert tested[0].ca_cert_path == "/certs/ca.pem"


@pytest.mark.asyncio
async def test_ldap_unset_security_is_starttls_as_when_saving(async_client, db_session):
    """Settings saved without a security row: the form's StartTLS is no change of destination."""
    from backend.app.models.settings import Settings

    for key, value in {**LDAP_SAVED, "ldap_server_url": "ldap://ldap.example.com"}.items():
        if key != "ldap_security":
            db_session.add(Settings(key=key, value=value))
    await db_session.commit()

    result, tested = await _ldap_test(
        async_client,
        {
            **LDAP_SAVED,
            "ldap_server_url": "ldap://ldap.example.com",
            "ldap_security": "starttls",
            "ldap_bind_password": "",
        },
    )

    assert result["success"] is True, result
    assert tested[0].bind_password == "saved-bind-secret"


async def _smtp_test(async_client: AsyncClient, smtp: dict | None):
    body: dict = {"test_recipient": "someone@example.com"}
    if smtp is not None:
        body["smtp"] = smtp
    with patch("backend.app.api.routes.auth.send_email") as send:
        response = await async_client.post("/api/v1/auth/smtp/test", json=body)
    assert response.status_code == 200, response.text
    return response.json(), send


@pytest.fixture
async def smtp_saved(async_client: AsyncClient):
    response = await async_client.post("/api/v1/auth/smtp", json=SMTP_SAVED)
    assert response.status_code == 200, response.text


class TestSmtp:
    @pytest.mark.asyncio
    async def test_tests_the_form_values(self, async_client, smtp_saved):
        result, send = await _smtp_test(
            async_client, {**SMTP_SAVED, "smtp_from_email": "typed@example.com", "smtp_password": "typed-secret"}
        )

        assert result["success"] is True
        used = send.call_args.kwargs["smtp_settings"]
        assert used.smtp_from_email == "typed@example.com"
        assert used.smtp_password == "typed-secret"

    @pytest.mark.asyncio
    async def test_an_empty_password_keeps_the_saved_one_for_the_same_server(self, async_client, smtp_saved):
        _, send = await _smtp_test(async_client, {**SMTP_SAVED, "smtp_password": ""})

        assert send.call_args.kwargs["smtp_settings"].smtp_password == "saved-smtp-secret"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "changed", [{"smtp_host": "mail.elsewhere.example.net"}, {"smtp_port": 2525}, {"smtp_security": "ssl"}]
    )
    async def test_the_saved_password_never_goes_to_another_server(self, async_client, smtp_saved, changed):
        result, send = await _smtp_test(async_client, {**SMTP_SAVED, "smtp_password": "", **changed})

        assert result["success"] is False
        assert "again" in result["message"]
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_the_form_it_tests_the_saved_settings(self, async_client, smtp_saved):
        _, send = await _smtp_test(async_client, None)

        assert send.call_args.kwargs["smtp_settings"].smtp_host == "smtp.example.com"

    @pytest.mark.asyncio
    async def test_the_form_works_before_anything_was_saved(self, async_client):
        result, send = await _smtp_test(async_client, SMTP_SAVED)

        assert result["success"] is True
        assert send.call_args.kwargs["smtp_settings"].smtp_password == "saved-smtp-secret"
