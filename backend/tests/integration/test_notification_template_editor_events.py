"""Every notification event can be edited with its variables and a preview (CE-7).

The print outcome confirmation (#1898) had no entry in the editor's variable
list, sample data or names: the editor listed no variables for it and the
preview left {printer}, {filename} and {confirm_url} empty. Seven other
events had no display name.
"""

import pytest
from httpx import AsyncClient

from backend.app.api.routes.notification_templates import EVENT_NAMES
from backend.app.models.notification_template import DEFAULT_TEMPLATES
from backend.app.schemas.notification_template import EVENT_VARIABLES, SAMPLE_DATA

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

SEEDED = sorted({t["event_type"] for t in DEFAULT_TEMPLATES})


@pytest.mark.parametrize("event_type", SEEDED)
async def test_each_seeded_event_has_variables_samples_and_a_name(event_type):
    assert event_type in EVENT_VARIABLES
    assert set(EVENT_VARIABLES[event_type]) <= set(SAMPLE_DATA[event_type])
    assert event_type in EVENT_NAMES


async def test_the_confirmation_request_previews_its_link(async_client: AsyncClient):
    template = next(t for t in DEFAULT_TEMPLATES if t["event_type"] == "print_confirm_request")

    response = await async_client.post(
        "/api/v1/notification-templates/preview",
        json={
            "event_type": "print_confirm_request",
            "title_template": template["title_template"],
            "body_template": template["body_template"],
        },
    )

    assert response.status_code == 200
    body = response.json()["body"]
    assert SAMPLE_DATA["print_confirm_request"]["printer"] in body
    assert SAMPLE_DATA["print_confirm_request"]["confirm_url"] in body
