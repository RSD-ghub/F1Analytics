"""Every response carries a request id, and a caller's id is kept."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from f1_common.observability import HEADER, RequestIdMiddleware, current_request_id, outgoing_headers


def _app():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware, service="test")

    @app.get("/echo")
    def echo():
        return {"rid": current_request_id(), "forward": outgoing_headers()}
    return app


def test_a_request_id_is_minted_and_echoed():
    response = TestClient(_app()).get("/echo")
    rid = response.headers[HEADER]
    assert rid and response.json()["rid"] == rid
    assert response.json()["forward"] == {HEADER: rid}


def test_a_callers_id_is_kept_and_a_hostile_one_replaced():
    client = TestClient(_app())
    assert client.get("/echo", headers={HEADER: "abc-123"}).headers[HEADER] == "abc-123"
    replaced = client.get("/echo", headers={HEADER: "bad id\nwith newline"}).headers[HEADER]
    assert replaced != "bad id\nwith newline" and "\n" not in replaced
