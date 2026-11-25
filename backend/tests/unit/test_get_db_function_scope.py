"""Every ``get_db`` dependency is function-scoped (#3320).

With the default request scope, ``get_db`` commits after the response has been
sent, so a read straight after a write can miss it. FastAPI keys its dependency
cache on the scope too: one route mixing both would get two sessions, and a
request-scoped yield dependency cannot use a function-scoped one at all. So it
is all of them or none.
"""

from fastapi.routing import APIRoute, APIWebSocketRoute

from backend.app.core.database import get_db
from backend.app.main import app


def _get_db_uses(dependant, path: str, found: list):
    for dep in dependant.dependencies:
        if dep.call is get_db:
            found.append((path, dep.computed_scope))
        _get_db_uses(dep, path, found)


def test_every_get_db_dependency_is_function_scoped():
    found: list[tuple[str, str | None]] = []
    for route in app.routes:
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            _get_db_uses(route.dependant, route.path, found)

    assert len(found) > 100  # the walk reaches the routes at all
    request_scoped = sorted({path for path, scope in found if scope != "function"})
    assert request_scoped == [], 'use Depends(get_db, scope="function"):\n' + "\n".join(request_scoped)
