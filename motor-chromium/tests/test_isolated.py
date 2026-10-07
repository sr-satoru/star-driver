"""Humanize's DOM reads run in an isolated world, never the page's (clearcote._isolated)."""
import json

from clearcote._isolated import (
    AsyncIsolatedWorld, IsolatedWorld, IS_FOCUSED, SELECT_PLAN, VIEWPORT, world_for,
)


class _FakeCdp:
    """Page.getFrameTree / Page.createIsolatedWorld / Runtime.evaluate, recorded."""

    def __init__(self, values=None, stale_once=False):
        self.calls, self.values, self.stale_once, self.next_ctx = [], list(values or []), stale_once, 7

    def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "MAIN"}}}
        if method == "Page.createIsolatedWorld":
            self.next_ctx += 1
            return {"executionContextId": self.next_ctx}
        if method == "Runtime.evaluate":
            if self.stale_once:
                self.stale_once = False
                raise RuntimeError("Cannot find context with specified id")
            return {"result": {"value": self.values.pop(0) if self.values else None}}
        raise AssertionError(method)


class _FakeContext:
    def __init__(self, cdp):
        self.cdp, self.sessions = cdp, 0

    def new_cdp_session(self, page):
        self.sessions += 1
        return self.cdp


class _FakePage:
    def __init__(self, cdp):
        self.context = _FakeContext(cdp)

    def evaluate(self, *a, **k):
        raise AssertionError("humanize must not evaluate in the page's world")


def test_evaluates_in_an_isolated_world_of_the_main_frame():
    cdp = _FakeCdp(values=[[1280, 720], True])
    world = IsolatedWorld(_FakePage(cdp))
    assert world.evaluate(VIEWPORT) == [1280, 720]
    assert world.evaluate(IS_FOCUSED, "#q") is True
    methods = [m for m, _ in cdp.calls]
    assert methods == ["Page.getFrameTree", "Page.createIsolatedWorld", "Runtime.evaluate", "Runtime.evaluate"]
    assert cdp.calls[1][1] == {"frameId": "MAIN"}
    ev = cdp.calls[3][1]
    assert ev["contextId"] == 8 and ev["returnByValue"] is True
    assert ev["expression"] == "(%s)(%s)" % (IS_FOCUSED, json.dumps("#q"))
    assert "Runtime.enable" not in methods  # never enables the Runtime domain


def test_recreates_the_world_after_a_navigation():
    cdp = _FakeCdp(values=[[800, 600]], stale_once=True)
    world = IsolatedWorld(_FakePage(cdp))
    assert world.evaluate(VIEWPORT) == [800, 600]
    assert [m for m, _ in cdp.calls].count("Page.createIsolatedWorld") == 2
    assert cdp.calls[-1][1]["contextId"] == 9  # the fresh context


def test_returns_none_instead_of_touching_the_page_world():
    class NoCdp:
        def __init__(self):
            self.context = type("C", (), {"new_cdp_session": lambda s, p: (_ for _ in ()).throw(RuntimeError("x"))})()

        def evaluate(self, *a, **k):
            raise AssertionError("must not fall back to page.evaluate")

    assert IsolatedWorld(NoCdp()).evaluate(VIEWPORT) is None


def test_exceptions_in_the_script_read_as_none():
    class Cdp(_FakeCdp):
        def send(self, method, params=None):
            if method == "Runtime.evaluate":
                return {"exceptionDetails": {"text": "SyntaxError"}}
            return super().send(method, params)

    assert IsolatedWorld(_FakePage(Cdp())).evaluate(SELECT_PLAN, {"sel": "!!", "by": "value", "want": "x"}) is None


def test_world_is_cached_per_page():
    page = _FakePage(_FakeCdp(values=[1, 2]))
    assert world_for(page) is world_for(page)
    world_for(page).evaluate(VIEWPORT)
    world_for(page).evaluate(VIEWPORT)
    assert page.context.sessions == 1


async def test_async_world():
    class ACdp:
        def __init__(self):
            self.sync = _FakeCdp(values=[[1, 2]])

        async def send(self, method, params=None):
            return self.sync.send(method, params)

    class ACtx:
        def __init__(self):
            self.cdp = ACdp()

        async def new_cdp_session(self, page):
            return self.cdp

    page = type("P", (), {})()
    page.context = ACtx()
    assert await AsyncIsolatedWorld(page).evaluate(VIEWPORT) == [1, 2]


def test_humanized_select_reads_through_the_isolated_world():
    # page.evaluate raises on this fake: the select planner must use the isolated world.
    from clearcote._humanize import _select_by_keyboard
    cdp = _FakeCdp(values=[{"to": 2, "from": 2, "ret": "b"}])
    page = _FakePage(cdp)
    assert _select_by_keyboard(page, "#s", "b", {}) == ["b"]
    ev = [p for m, p in cdp.calls if m == "Runtime.evaluate"][0]
    assert json.loads(ev["expression"][len(SELECT_PLAN) + 3:-1]) == {"sel": "#s", "by": "value", "want": "b"}
