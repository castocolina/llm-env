"""Shared test doubles for the pylib.omniroute_* test modules.

Every pylib.omniroute_* feature module talks HTTP through the same
pylib.omniroute_client.login()/request_json() pair, so its tests fake
urllib.request.urlopen the same way -- this module holds that one shared
FakeResponse/_fake_login_ok pattern instead of duplicating it per test file.
"""

from __future__ import annotations

import json
from email.message import Message

LOGIN_URL = "http://127.0.0.1:20128/api/auth/login"


class FakeResponse:
    def __init__(self, payload, *, set_cookie=None):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = Message()
        if set_cookie is not None:
            self.headers["Set-Cookie"] = set_cookie

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def fake_login_ok(set_cookie="auth_token=session-token; Path=/; HttpOnly"):
    def fake(request, timeout):
        assert request.full_url == LOGIN_URL
        assert request.get_method() == "POST"
        assert json.loads(request.data) == {"password": "dashboard-pw"}
        return FakeResponse({"success": True}, set_cookie=set_cookie)

    return fake
