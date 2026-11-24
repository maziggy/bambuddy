"""Project label routes (#3655): ``POST /projects/labels`` and its preview.

The drawing has its own unit tests (``test_project_label_renderer_3655.py``);
these cover what the routes put on each label, validation, and permissions.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from backend.app.api.routes import project_labels
from backend.app.core.printer_scope import ALL_PRINTERS, PrinterScope
from backend.app.models.archive import PrintArchive
from backend.app.models.group import Group
from backend.app.models.print_log import PrintLogEntry
from backend.app.models.project import Project
from backend.app.models.user import User
from backend.app.services.label_renderer import render_labels
from backend.app.services.project_label_renderer import ALL_PROJECT_LABEL_FIELDS

URL = "/api/v1/projects/labels"


@pytest.fixture
async def project(db_session: AsyncSession) -> Project:
    parent = Project(name="Spring Fair")
    db_session.add(parent)
    await db_session.commit()
    p = Project(
        name="Banana Slugs",
        color="#FFD700",
        due_date=datetime(2026, 12, 5),
        target_parts_count=10,
        parent_id=parent.id,
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    # Two completed runs of 3 parts each, the last on Nov 20; one failed run later.
    for when, status in ((datetime(2026, 11, 18, 9), "completed"), (datetime(2026, 11, 20, 15), "completed")):
        archive = PrintArchive(
            filename="slug.3mf",
            file_path="t/slug.3mf",
            file_size=1,
            print_name="Slug",
            status=status,
            quantity=3,
            project_id=p.id,
        )
        db_session.add(archive)
        await db_session.commit()
        db_session.add(PrintLogEntry(archive_id=archive.id, status=status, completed_at=when, print_name="Slug"))
    failed = PrintArchive(
        filename="slug.3mf", file_path="t/slug.3mf", file_size=1, status="failed", quantity=3, project_id=p.id
    )
    db_session.add(failed)
    await db_session.commit()
    db_session.add(PrintLogEntry(archive_id=failed.id, status="failed", completed_at=datetime(2026, 11, 21)))
    await db_session.commit()
    for deleted, verdict in ((None, "reject"), (datetime(2026, 11, 25), None)):
        archive = PrintArchive(
            filename="extra", file_path="extra", file_size=1, quantity=11, deleted_at=deleted, project_id=p.id
        )
        db_session.add(archive)
        await db_session.flush()
        db_session.add(
            PrintLogEntry(
                archive_id=archive.id, status="completed", user_verdict=verdict, completed_at=datetime(2026, 11, 24)
            )
        )
    await db_session.commit()
    return p


@pytest.mark.asyncio
@pytest.mark.integration
async def test_renders_a_pdf_with_the_projects_data(async_client: AsyncClient, project: Project, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    with patch.object(project_labels, "render_labels", wraps=render_labels) as rendered:
        resp = await async_client.post(
            URL,
            json={
                "template": "box_62x29",
                "fields": list(ALL_PROJECT_LABEL_FIELDS),
                "items": [
                    {"project_id": project.id, "quantity": 12, "note": "  Bag 1 of 2 "},
                    {"project_id": project.id, "quantity": 6, "note": "   "},
                ],
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")

    data = rendered.call_args.args[1]
    assert [(d.quantity, d.note) for d in data] == [(12, "Bag 1 of 2"), (6, None)]
    first = data[0]
    assert first.name == "Banana Slugs"
    assert first.parent_name == "Spring Fair"
    assert first.color == "#FFD700"
    assert first.due_date == date(2026, 12, 5)
    assert first.printed == date(2026, 11, 21)  # Nov 20 at 15:00 UTC is midnight in Tokyo
    assert (first.completed_parts, first.target_parts) == (6, 10)
    assert first.deeplink_url.endswith(f"/projects/{project.id}")
    assert rendered.call_args.kwargs["subject"] == "project"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_qr_points_at_the_external_url(async_client: AsyncClient, db_session: AsyncSession, project: Project):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="external_url", value="https://bambuddy.example/"))
    await db_session.commit()
    with patch.object(project_labels, "render_labels", wraps=render_labels) as rendered:
        resp = await async_client.post(URL, json={"template": "box_62x29", "items": [{"project_id": project.id}]})
    assert resp.status_code == 200
    assert rendered.call_args.args[1][0].deeplink_url == f"https://bambuddy.example/projects/{project.id}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_png_of_a_roll_template_zips_one_file_per_label(async_client: AsyncClient, project: Project):
    resp = await async_client.post(
        URL,
        json={
            "template": "box_62x29",
            "format": "png",
            "dpi": 300,
            "items": [{"project_id": project.id}, {"project_id": project.id}],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert names == [f"project-{project.id}-1.png", f"project-{project.id}-2.png"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_custom_sheet_with_a_starting_position(async_client: AsyncClient, project: Project):
    layout = {"page": "a4", "label_w_mm": 90, "label_h_mm": 29, "cols": 2, "rows": 9, "top_mm": 10, "left_mm": 10}
    body = {"template": "custom", "layout": layout, "starting_position": 5, "items": [{"project_id": project.id}]}
    with patch.object(project_labels, "draw_project_label", wraps=project_labels.draw_project_label) as draw:
        assert (await async_client.post(URL, json=body)).status_code == 200
    from reportlab.lib.units import mm

    _, x, y, _, _, *_ = draw.call_args.args
    assert x / mm == pytest.approx(10)
    assert y / mm == pytest.approx(297 - 10 - 3 * 29)  # first column, third row
    body["starting_position"] = 19
    assert (await async_client.post(URL, json=body)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_preview_is_one_png(async_client: AsyncClient, project: Project):
    resp = await async_client.post(
        f"{URL}/preview",
        json={"template": "box_62x29", "project_id": project.id, "quantity": 4, "fields": ["name", "quantity"]},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(resp.content)).size[0] > 600


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    [
        {"template": "box_62x29", "items": []},
        {"template": "box_62x29", "items": [{"project_id": 2**63}]},
        {"template": "box_62x29", "items": [{"project_id": 1, "quanity": 3}]},
        {"template": "box_62x29", "items": [{"project_id": 1}], "copies": 5},
        {"template": "box_62x29", "items": [{"project_id": 1, "quantity": 100_001}]},
        {"template": "box_62x29", "items": [{"project_id": 1, "quantity": 0}]},
        {"template": "box_62x29", "items": [{"project_id": 1, "note": "x" * 201}]},
        {"template": "box_62x29", "items": [{"project_id": 1}], "fields": ["brand"]},  # a spool field
        {"template": "custom", "items": [{"project_id": 1}]},  # no layout
        {"template": "box_62x29", "items": [{"project_id": 1}], "starting_position": 2},  # roll
    ],
)
async def test_rejects_bad_requests(async_client: AsyncClient, body):
    assert (await async_client.post(URL, json=body)).status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unknown_project_is_404(async_client: AsyncClient, project: Project):
    resp = await async_client.post(URL, json={"template": "box_62x29", "items": [{"project_id": project.id + 99}]})
    assert resp.status_code == 404
    preview = await async_client.post(f"{URL}/preview", json={"template": "box_62x29", "project_id": 99999})
    assert preview.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_needs_projects_read_when_auth_is_on(
    async_client: AsyncClient, db_session: AsyncSession, project: Project
):
    from backend.app.core.auth import create_access_token, get_password_hash
    from backend.app.models.group import Group
    from backend.app.models.settings import Settings
    from backend.app.models.user import User

    db_session.add(Settings(key="auth_enabled", value="true"))
    await db_session.commit()
    body = {"template": "box_62x29", "items": [{"project_id": project.id}]}
    assert (await async_client.post(URL, json=body)).status_code == 401

    preview = {"template": body["template"], "project_id": project.id}
    assert (await async_client.post(URL + "/preview", json=preview)).status_code == 401

    viewers = (await db_session.execute(select(Group).where(Group.name == "Viewers"))).scalar_one()
    user = User(username="projlabel_viewer", password_hash=get_password_hash("password"), is_active=True)
    user.groups.append(viewers)
    db_session.add(user)
    await db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(data={'sub': user.username})}"}
    assert (await async_client.post(URL, json=body, headers=headers)).status_code == 200
    assert (await async_client.post(URL + "/preview", json=preview, headers=headers)).status_code == 200

    for name, permissions, expected in (
        ("project_reader", ["projects:read"], 200),
        ("inventory_reader", ["inventory:read"], 403),
    ):
        group = Group(name=name, permissions=permissions)
        reader = User(username=name, password_hash=user.password_hash, is_active=True)
        reader.groups.append(group)
        db_session.add(reader)
        await db_session.commit()
        headers = {"Authorization": f"Bearer {create_access_token(data={'sub': reader.username})}"}
        assert (await async_client.get("/api/v1/label-templates", headers=headers)).status_code == 200
        assert (await async_client.delete("/api/v1/label-templates/1", headers=headers)).status_code == 403
        with patch.object(project_labels, "render_labels", wraps=render_labels) as render:
            assert (
                await async_client.post(URL, json={**body, "fields": ["printed", "parts"]}, headers=headers)
            ).status_code == expected
        if expected == 200:
            label = render.call_args.args[1][0]
            assert label.completed_parts == 6
            assert label.printed is None  # project totals are shared, archive details need archive access
        assert (await async_client.post(URL + "/preview", json=preview, headers=headers)).status_code == expected


@pytest.mark.asyncio
async def test_loading_only_queries_selected_data_for_the_whole_batch(db_session, project):
    other = Project(name="Second bag")
    db_session.add(other)
    await db_session.commit()
    items = [project_labels.ProjectLabelItem(project_id=pid) for pid in [other.id, project.id, other.id]]
    request = Request({"type": "http"})
    bind = db_session.get_bind()
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", record)
    try:
        labels = await project_labels._project_label_data(
            items, frozenset({"name"}), request, db_session, ALL_PRINTERS, None
        )
        assert [label.name for label in labels] == ["Second bag", "Banana Slugs", "Second bag"]
        assert len(statements) == 1
        statements.clear()
        labels = await project_labels._project_label_data(
            items, frozenset({"parent", "parts", "printed"}), request, db_session, ALL_PRINTERS, None
        )
        assert len(statements) == 4  # projects, parents, parts and dates, irrespective of batch length
        assert [label.completed_parts for label in labels] == [0, 6, 0]
    finally:
        event.remove(bind, "before_cursor_execute", record)


@pytest.mark.asyncio
async def test_last_print_uses_the_visible_run_on_a_shared_archive(db_session, project, printer_factory, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    first, second = await printer_factory(), await printer_factory()
    archive = await db_session.scalar(
        select(PrintArchive).where(PrintArchive.project_id == project.id).order_by(PrintArchive.id)
    )
    archive.printer_id = second.id
    for printer, day in ((first, 22), (second, 23)):
        db_session.add(
            PrintLogEntry(
                archive_id=archive.id, printer_id=printer.id, status="completed", completed_at=datetime(2026, 11, day)
            )
        )
    await db_session.commit()
    scope = PrinterScope(frozenset({first.id}))
    [label] = await project_labels._project_label_data(
        [project_labels.ProjectLabelItem(project_id=project.id)],
        frozenset({"printed", "parts"}),
        Request({"type": "http"}),
        db_session,
        scope,
        None,
    )
    assert label.printed == date(2026, 11, 22)
    assert label.completed_parts == 12


@pytest.mark.asyncio
@pytest.mark.integration
async def test_last_print_is_only_from_archives_the_caller_may_read(db_session, project, monkeypatch):
    """A user who may read only their own archives gets the date of their own
    last run, not a colleague's later one; the parts count stays project-wide."""
    monkeypatch.setenv("TZ", "UTC")
    group = Group(name="Own archives", permissions=["projects:read", "archives:read_own"])
    alice, bob = User(username="alice", groups=[group]), User(username="bob")
    db_session.add_all([group, alice, bob])
    await db_session.flush()
    # The fixture's two completed runs: Nov 18 becomes Alice's, Nov 20 Bob's.
    early, late = (
        await db_session.scalars(
            select(PrintArchive)
            .where(
                PrintArchive.project_id == project.id,
                PrintArchive.filename == "slug.3mf",
                PrintArchive.status == "completed",
            )
            .order_by(PrintArchive.id)
        )
    ).all()
    early.created_by_id, late.created_by_id = alice.id, bob.id
    await db_session.commit()

    [label] = await project_labels._project_label_data(
        [project_labels.ProjectLabelItem(project_id=project.id)],
        frozenset({"printed", "parts"}),
        Request({"type": "http"}),
        db_session,
        ALL_PRINTERS,
        alice,
    )
    assert label.printed == date(2026, 11, 18)
    assert label.completed_parts == 6
