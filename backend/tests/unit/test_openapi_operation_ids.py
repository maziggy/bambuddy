"""Every operation in the OpenAPI schema has its own operation id.

A route registered with api_route(methods=[A, B]) gives both methods the
same id, and FastAPI only warns about it while it builds /openapi.json.
Register one decorator per method instead.
"""

import warnings

from fastapi.openapi.utils import get_openapi

from backend.app.main import app


def test_operation_ids_are_unique():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)

    ids = [op["operationId"] for path in schema["paths"].values() for op in path.values()]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
