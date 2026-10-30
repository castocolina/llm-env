import json
import stat

import pytest

from pylib import zai_reset
from pylib.omniroute import OmniRouteError

BASE = "http://127.0.0.1:20128"
WINDOW = "2026-11-04T22:01:57.997Z"


class FakeOmniRoute:
    """Routes the module's (method, path) calls; records every POST."""

    def __init__(self, used=9900, total=10000, window=WINDOW, cards=None, connections=None, redeem_error=None):
        self.usage = {"quotas": {"weekly": {"used": used, "total": total, "resetAt": window}}}
        self.cards = {"credits": [{"resetType": "WEEK", "expiresAt": "2026-12-22 23:59:59", "selectionToken": "11"}]
                      if cards is None else cards}
        self.connections = connections if connections is not None else [
            {"id": "c1", "name": "casto-dev at z.ai", "provider": "zai", "isActive": True},
            {"id": "c2", "name": "other", "provider": "kimi-coding", "isActive": True},
            {"id": "c3", "name": "off", "provider": "zai", "isActive": False},
        ]
        self.redeem_error = redeem_error
        self.posts = []

    def __call__(self, method, url, token, payload=None):
        assert token == "tok"
        path = url.removeprefix(BASE)
        if method == "POST":
            self.posts.append((path, payload))
            if self.redeem_error:
                raise OmniRouteError(self.redeem_error)
            return {"ok": True}
        if path == "/api/providers":
            return {"connections": self.connections}
        if path == "/api/usage/c1":
            return self.usage
        if path.startswith("/api/usage/glm-reset-card?connectionId=c1"):
            return self.cards
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def run(monkeypatch, tmp_path):
    state = tmp_path / "cache" / "state.json"

    def go(fake, dry_run=False):
        monkeypatch.setattr(zai_reset, "_login", lambda base, password: "tok")
        monkeypatch.setattr(zai_reset, "_request", fake)
        return zai_reset.check(BASE, "pw", state, dry_run=dry_run)

    go.state = state
    return go


def test_below_threshold_redeems_nothing(run):
    fake = FakeOmniRoute(used=9800)
    assert run(fake) == [{"connection": "casto-dev at z.ai", "used_percent": 98.0, "window": WINDOW,
                          "action": "none", "reason": "below_threshold"}]
    assert fake.posts == []


def test_redeems_one_week_card_at_99_percent_and_only_once_per_window(run):
    fake = FakeOmniRoute(used=9900)
    first = run(fake)
    assert [r["action"] for r in first] == ["redeemed"]  # the kimi and the inactive z.ai connections are ignored
    assert fake.posts == [("/api/usage/glm-reset-card", {
        "connectionId": "c1", "idempotencyKey": f"llm-env-zai-reset:c1:{WINDOW}", "creditId": "11"})]
    assert stat.S_IMODE(run.state.stat().st_mode) == 0o600
    assert json.loads(run.state.read_text())["c1"]["window"] == WINDOW
    second = run(fake)  # a stale usage snapshot still says 100 %: the window is already spent
    assert second[0]["reason"] == "already_redeemed_this_window"
    assert len(fake.posts) == 1


def test_a_new_window_can_redeem_again(run):
    fake = FakeOmniRoute(used=10000)
    run(fake)
    fake.usage["quotas"]["weekly"]["resetAt"] = "2026-11-11T22:01:57.997Z"
    assert run(fake)[0]["action"] == "redeemed"
    assert len(fake.posts) == 2


def test_picks_the_soonest_expiring_week_card_and_ignores_other_types(run):
    fake = FakeOmniRoute(cards=[
        {"resetType": "FIVE_HOUR", "expiresAt": "2026-10-01 00:00:00", "selectionToken": "5h"},
        {"resetType": "WEEK", "expiresAt": "2026-12-30 00:00:00", "selectionToken": "late"},
        {"resetType": "WEEK", "expiresAt": "2026-12-22 00:00:00", "selectionToken": "soon"},
    ])
    run(fake)
    assert fake.posts[0][1]["creditId"] == "soon"


def test_without_a_week_card_nothing_is_redeemed(run):
    fake = FakeOmniRoute(cards=[{"resetType": "FIVE_HOUR", "expiresAt": "x", "selectionToken": "5h"}])
    assert run(fake)[0]["reason"] == "no_week_card"
    assert fake.posts == [] and not run.state.exists()


def test_dry_run_reports_without_redeeming_or_recording(run):
    fake = FakeOmniRoute()
    assert run(fake, dry_run=True)[0]["action"] == "would_redeem"
    assert fake.posts == [] and not run.state.exists()


def test_a_failed_redeem_is_reported_not_recorded_and_retried_with_the_same_key(run):
    fake = FakeOmniRoute(redeem_error="POST failed: HTTP 502")
    result = run(fake)
    assert result[0] == {"connection": "casto-dev at z.ai", "used_percent": 99.0, "window": WINDOW,
                         "action": "error", "error": "POST failed: HTTP 502"}
    assert not run.state.exists()
    fake.redeem_error = None
    assert run(fake)[0]["action"] == "redeemed"
    assert fake.posts[0][1]["idempotencyKey"] == fake.posts[1][1]["idempotencyKey"]


@pytest.mark.parametrize("usage", [{}, {"quotas": {}}, {"quotas": {"weekly": {"used": 1, "total": 0}}}])
def test_a_connection_without_a_usable_weekly_quota_is_skipped(run, usage):
    fake = FakeOmniRoute()
    fake.usage = usage
    assert run(fake)[0]["reason"] == "no_weekly_quota"


def test_garbage_state_counts_as_empty(run):
    run.state.parent.mkdir(parents=True)
    run.state.write_text("not json")
    assert run(FakeOmniRoute())[0]["action"] == "redeemed"
