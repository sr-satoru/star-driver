"""The PRO engine refuses a run-token older than the newest it has accepted for this OS user.

Its clock-rollback guard (patch 990) keeps that newest ``iat`` in ``$LOCALAPPDATA/.clearcote/.cc_hwm`` (else
``$HOME``) and refuses any lower one: "this run-token is older than the last one accepted here (system clock
set back?); refusing." The SDK reuses a cached token for its 24 h life, and anything else under the same OS
user that launches with a newer token (another process, the hosted-browser gateway, a run with another key)
moves the mark past it. On a production worker that failed every job, twice, until the cache file was deleted
by hand.

So (1) acquire_lease() replaces a token it can see is behind the mark, preferring a heartbeat of the lease it
knows (same lease, nothing revoked) over a checkout; and (2) a launch the engine refuses anyway (a race)
refreshes the token and launches once more. The same scenario ran end to end against the real r29 engine.
Hermetic: _post is mocked, HOME and LOCALAPPDATA point at a temp dir.
"""
import asyncio
import base64
import json
import time

import pytest

import clearcote
import clearcote._license as L
from clearcote import async_api

KEY = "cc_lic_STALETOKEN"
REFUSAL = ("BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
           "Browser logs:\n[pid=1][err] [clearcote] licence: this run-token is older than the last one "
           "accepted here (system clock set back?); refusing.")


def tok(iat, plan="pro"):
    body = base64.urlsafe_b64encode(json.dumps({"v": 1, "plan": plan, "iat": iat}).encode()).decode().rstrip("=")
    return body + ".sig"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(L.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("CLEARCOTE_LICENSE_KEY", KEY)
    monkeypatch.setenv("CLEARCOTE_LICENSE_API", "http://test.local")
    monkeypatch.delenv("CLEARCOTE_INSTANCE_ID", raising=False)
    monkeypatch.setattr(L._MachineLease, "_start_heartbeat", lambda self: setattr(self, "_hb_started", True))
    _reset()
    yield tmp_path
    _reset()


def _reset():
    for ml in list(L._MACHINE_LEASES.values()):
        try:
            ml._stop.set()
        except Exception:
            pass
    L._MACHINE_LEASES.clear()


def set_mark(home, iat):
    (home / ".clearcote").mkdir(exist_ok=True)
    (home / ".clearcote" / ".cc_hwm").write_text(str(iat))


def cache_token(iat, lease_id="L1"):
    L._write_cache(KEY, tok(iat), time.time() + 3600, lease_id)


def backend(monkeypatch, answers):
    """_post: records (endpoint, body); answers[endpoint] is a list of (status, body) served in order."""
    calls = []

    def post(url, key, body, timeout=15.0):
        ep = url.rsplit("/", 1)[-1]
        calls.append((ep, dict(body)))
        queue = answers.get(ep) or [(200, {})]
        status, answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return status, answer

    monkeypatch.setattr(L, "_post", post)
    return calls


# ── reading the token and the engine's mark ──────────────────────────────────────────────────────────

def test_token_iat_is_read_from_the_payload():
    assert L.token_iat(tok(1790685547)) == 1790685547
    assert L.token_iat("not-a-token") is None
    assert L.token_iat(tok("soon")) is None
    assert L.token_iat("") is None


def test_engine_mark_is_found_where_the_engine_keeps_it(tmp_path):
    local, home = tmp_path / "local", tmp_path / "home"
    for d, v in ((local, "200"), (home, "100")):
        (d / ".clearcote").mkdir(parents=True)
        (d / ".clearcote" / ".cc_hwm").write_text(v + "\n")
    assert L.engine_hwm({"LOCALAPPDATA": str(local), "HOME": str(home)}) == 200   # Windows: LOCALAPPDATA first
    assert L.engine_hwm({"HOME": str(home)}) == 100                               # elsewhere: HOME
    assert L.engine_hwm({"LOCALAPPDATA": "", "HOME": str(home)}) == 100           # empty counts as unset, as there
    assert L.engine_hwm({"HOME": str(tmp_path / "none")}) == 0                    # no mark yet
    assert L.engine_hwm({}) == 0
    (home / ".clearcote" / ".cc_hwm").write_text("garbage")
    assert L.engine_hwm({"HOME": str(home)}) == 0


# ── (1) acquire: a token the engine would refuse is replaced before the launch ──────────────────────────

def test_a_cached_token_behind_the_mark_is_refreshed_by_heartbeating_its_lease(env, monkeypatch):
    cache_token(1000, lease_id="L-owner")
    set_mark(env, 1500)                                   # another process launched with a newer token
    calls = backend(monkeypatch, {"heartbeat": [(200, {"token": tok(2000), "exp": time.time() + 3600})]})
    h = L.acquire_lease()
    assert [c[0] for c in calls] == ["heartbeat"]          # no checkout: same lease, nothing revoked
    assert calls[0][1]["lease_id"] == "L-owner"
    assert L.token_iat(h.token) == 2000
    assert L._read_cache(KEY)["token"] == tok(2000)        # other processes pick up the fresh token too


def test_a_token_at_or_past_the_mark_is_reused_with_no_backend_call(env, monkeypatch):
    for mark in (1000, 900):                              # equal is accepted by the engine (it refuses iat < mark)
        _reset()
        cache_token(1000)
        set_mark(env, mark)
        calls = backend(monkeypatch, {})
        h = L.acquire_lease()
        assert calls == [] and L.token_iat(h.token) == 1000


def test_no_mark_yet_means_nothing_to_compare(env, monkeypatch):
    cache_token(1000)
    calls = backend(monkeypatch, {})
    assert L.token_iat(L.acquire_lease().token) == 1000
    assert calls == []


def test_a_gone_lease_falls_back_to_a_checkout_and_this_process_takes_over_the_slot(env, monkeypatch):
    cache_token(1000, lease_id="L-dead")
    set_mark(env, 1500)
    calls = backend(monkeypatch, {
        "heartbeat": [(409, {"code": "LEASE_NOT_FOUND"})],
        "checkout": [(200, {"lease_id": "L-new", "token": tok(2000), "exp": time.time() + 3600})],
    })
    h = L.acquire_lease()
    ml = next(iter(L._MACHINE_LEASES.values()))
    assert [c[0] for c in calls] == ["heartbeat", "checkout"]
    assert L.token_iat(h.token) == 2000 and ml.lease_id == "L-new"
    assert ml._owner and getattr(ml, "_hb_started", False)   # it now keeps the slot alive and checks it in


def test_a_legacy_cache_without_a_lease_id_refreshes_by_checkout(env, monkeypatch):
    cache_token(1000, lease_id=None)
    set_mark(env, 1500)
    calls = backend(monkeypatch, {"checkout": [(200, {"lease_id": "L2", "token": tok(2000), "exp": time.time() + 3600})]})
    assert L.token_iat(L.acquire_lease().token) == 2000
    assert [c[0] for c in calls] == ["checkout"]


def test_a_definitive_refusal_while_refreshing_surfaces_instead_of_a_doomed_launch(env, monkeypatch):
    cache_token(1000, lease_id="L-dead")
    set_mark(env, 1500)
    backend(monkeypatch, {
        "heartbeat": [(409, {"code": "LEASE_EXPIRED"})],
        "checkout": [(429, {"error": "limit", "code": "CONCURRENCY_LIMIT_EXCEEDED"})],
    })
    with pytest.raises(L.ConcurrencyLimitError):
        L.acquire_lease()


def test_an_in_memory_token_overtaken_since_the_last_launch_is_refreshed_too(env, monkeypatch):
    calls = backend(monkeypatch, {
        "checkout": [(200, {"lease_id": "L1", "token": tok(1000), "exp": time.time() + 3600, "heartbeat_interval_sec": 270})],
        "heartbeat": [(200, {"token": tok(3000), "exp": time.time() + 3600})],
    })
    assert L.token_iat(L.acquire_lease().token) == 1000   # cold checkout, nothing to compare yet
    set_mark(env, 2000)                                    # e.g. the hosted-browser gateway launched meanwhile
    assert L.token_iat(L.acquire_lease().token) == 3000
    assert [c[0] for c in calls] == ["checkout", "heartbeat"]


# ── (2) launch: the engine refused anyway (a race) -> fresh token, one more launch ─────────────────────

class _Lease:
    def __init__(self, fresh="TOK-FRESH", ok=True):
        self.token, self._fresh, self._ok, self.refreshes = "TOK-OLD", fresh, ok, 0

    def refresh_token(self):
        self.refreshes += 1
        if self._ok:
            self.token = self._fresh
        return self._ok


def test_a_refused_launch_is_retried_once_with_a_fresh_token():
    lease, pw_kwargs, seen = _Lease(), {"env": {"KEEP": "1", "CLEARCOTE_RUN_TOKEN": "TOK-OLD"}}, []

    def start():
        seen.append(dict(pw_kwargs["env"]))
        if len(seen) == 1:
            raise RuntimeError(REFUSAL)
        return "browser"

    assert clearcote._retry_on_stale_run_token(lease, pw_kwargs, ("C:/t/run.token", lambda: None), start) == "browser"
    assert lease.refreshes == 1 and len(seen) == 2
    assert seen[1]["CLEARCOTE_RUN_TOKEN"] == "TOK-FRESH" and seen[1]["KEEP"] == "1"
    assert seen[1]["CLEARCOTE_RUN_TOKEN_FILE"] == "C:/t/run.token"


@pytest.mark.parametrize("case", ["other error", "refresh failed", "no lease", "still refused"])
def test_anything_else_is_raised_as_it_was(case):
    lease = None if case == "no lease" else _Lease(ok=case != "refresh failed")
    attempts = []

    def start():
        attempts.append(1)
        if case == "other error":
            raise RuntimeError("spawn UNKNOWN")
        raise RuntimeError(REFUSAL)

    with pytest.raises(RuntimeError):
        clearcote._retry_on_stale_run_token(lease, {"env": {}}, None, start)
    assert len(attempts) == (2 if case == "still refused" else 1)   # at most ONE retry, never a loop
    if case == "other error":
        assert lease.refreshes == 0


async def test_the_async_launch_retries_the_same_way():
    lease, pw_kwargs, tokens = _Lease(), {"env": {}}, []

    async def start():
        tokens.append(pw_kwargs["env"].get("CLEARCOTE_RUN_TOKEN"))
        if len(tokens) == 1:
            raise RuntimeError(REFUSAL)
        return "context"

    assert await async_api._retry_on_stale_run_token_async(lease, pw_kwargs, None, start) == "context"
    assert tokens == [None, "TOK-FRESH"] and lease.refreshes == 1


# ── the per-browser (free) lease and the machine handle can both refresh on demand ──────────────────────

def test_a_browser_lease_refreshes_by_heartbeat_or_retakes_its_own_slot(env, monkeypatch):
    calls = backend(monkeypatch, {
        "heartbeat": [(200, {"token": tok(2000, "free")}), (409, {"code": "LEASE_EXPIRED"})],
        "checkout": [(200, {"lease_id": "L-b2", "token": tok(3000, "free"), "exp": time.time() + 180})],
    })
    ml = L._MachineLease(KEY, "http://test.local", "inst-1", "0.33.0", True)
    b = L._BrowserLease(ml, {"lease_id": "L-b1", "token": tok(1000, "free"), "heartbeat_interval_sec": 3600}, "launch-1")
    try:
        assert b.refresh_token() and L.token_iat(b.token) == 2000
        assert b.refresh_token() and L.token_iat(b.token) == 3000 and b.lease_id == "L-b2"
        assert calls[2] == ("checkout", {**calls[2][1], "launch_id": "launch-1"})   # the SAME launch retakes it
    finally:
        b._stopped.set()


def test_the_machine_handle_refreshes_even_when_it_cannot_see_the_mark(env, monkeypatch):
    cache_token(1000, lease_id="L1")
    calls = backend(monkeypatch, {"heartbeat": [(200, {"token": tok(2000), "exp": time.time() + 3600})]})
    h = L.acquire_lease()
    assert calls == []                                     # no mark: nothing to act on at acquire time
    assert h.refresh_token() and L.token_iat(h.token) == 2000   # the engine said otherwise: force it
