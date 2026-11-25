"""Skip Objects does not hold a database connection across its FTP download (RC-C9).

For a print started outside Bambuddy the route pulls the 3MF from the
printer -- up to 6 names in 3 folders, from a printer that is mid-print.
A ``Depends(get_db)`` session stayed open idle-in-transaction for all of it.
"""

import inspect

import pytest

from backend.app.api.routes import printers


# The connection diagnostic is the same pattern: port, TLS and FTP probes and
# a wait of up to 10 s for the printer to report.
@pytest.mark.parametrize("route", ["get_printable_objects", "diagnose_printer"])
def test_the_route_takes_no_request_long_session(route):
    for name, param in inspect.signature(getattr(printers, route)).parameters.items():
        assert getattr(param.default, "dependency", None) is not printers.get_db, (
            f"{route} holds a get_db session via {name!r} (RC-C9)"
        )
