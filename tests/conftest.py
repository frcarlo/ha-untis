"""Fixtures: a fake WebUntis server on localhost."""

from __future__ import annotations

from typing import Any

from aiohttp import web
import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeUntis:
    def __init__(self) -> None:
        self.password = "secret"
        self.students = [{"id": 11, "displayName": "Max"}, {"id": 12, "displayName": "Mia"}]
        # student id → raw getTimetable rows
        self.timetable: dict[int, list[dict[str, Any]]] = {}
        self.calls: list[str] = []

    async def rpc(self, request: web.Request) -> web.Response:
        body = await request.json()
        method = body["method"]
        self.calls.append(method)
        if method == "authenticate":
            if body["params"]["password"] != self.password:
                return web.json_response({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -8504, "message": "bad"}})
            resp = web.json_response(
                {"jsonrpc": "2.0", "id": body["id"], "result": {"sessionId": "ABC", "personId": 99, "personType": 12}}
            )
            resp.set_cookie("tenant", "t1")
            return resp
        assert "JSESSIONID=ABC" in request.headers.get("Cookie", "")
        if method == "getTimetable":
            sid = body["params"]["options"]["element"]["id"]
            return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": self.timetable.get(sid, [])})
        return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": None})

    async def token(self, request: web.Request) -> web.Response:
        return web.Response(text="TOKEN")

    async def app_data(self, request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer TOKEN"
        return web.json_response({"user": {"students": self.students}})


@pytest.fixture
async def untis_server(socket_enabled, aiohttp_server):
    fake = FakeUntis()
    app = web.Application()
    app.router.add_post("/WebUntis/jsonrpc.do", fake.rpc)
    app.router.add_get("/WebUntis/api/token/new", fake.token)
    app.router.add_get("/WebUntis/api/rest/view/v1/app/data", fake.app_data)
    server = await aiohttp_server(app)
    fake.url = f"http://127.0.0.1:{server.port}/WebUntis/?school=Test+Schule"
    return fake
