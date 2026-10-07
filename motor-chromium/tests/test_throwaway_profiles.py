"""Throwaway profiles: ``launch()`` and ``launch_agent()`` create a temp profile the caller never
names, so the SDK owns its removal -- when the context closes, and when the launch fails.

WHY THIS FILE EXISTS: both created the directory BEFORE launching and never removed it when the
launch failed, so every failed launch left a profile in %TEMP%; and a successful
``launch_agent()`` without ``user_data_dir`` kept its profile forever, although the caller has no
way to learn its path.

Only the Playwright driver and the humanizer are stubbed: option resolution (``_prepare``), the
binary guard and the temp-directory handling all run for real.
"""
import os
import tempfile

import pytest

import clearcote
from clearcote import _profile, async_api

BROWSER_FAILED = "browser failed to start"


@pytest.fixture
def temp(tmp_path, monkeypatch):
    """Every temp profile lands in this directory; anything left in it after a test leaked.

    No licence (it would select the PRO binary and take a real lease), no saved profiles and no
    warnings from this machine are visible."""
    temp = tmp_path / "temp"
    home = tmp_path / "home"
    temp.mkdir()
    home.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    for k in ("CLEARCOTE_LICENSE_KEY", "CLEARCOTE_BINARY", "CLEARCOTE_BROWSER_VERSION"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLEARCOTE_NO_WARN", "1")
    monkeypatch.setattr(_profile, "PROFILE_DIR", str(tmp_path / "no-saved-profiles"))
    assert clearcote.resolve_license_key(None) is None  # harness: no launch here takes a real lease
    return temp


@pytest.fixture
def exe(tmp_path):
    """A real file used as the explicit executable_path, so nothing is downloaded. Kept outside the
    watched temp directory."""
    d = tmp_path / "engine"
    d.mkdir()
    p = d / ("chrome.exe" if os.name == "nt" else "chrome")
    p.write_bytes(b"\x00")
    return str(p)


class _FakeContext:
    def __init__(self):
        self.handlers = {}
        self.pages = []

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def new_page(self, **kw):
        return kw


@pytest.fixture
def pw(monkeypatch):
    """Sync Playwright stand-in: records each persistent launch; ``pw["fail"]`` makes it raise."""
    state = {"fail": None, "dirs": []}

    class _Chromium:
        def launch_persistent_context(self, user_data_dir, **kw):
            state["dirs"].append(user_data_dir)
            if state["fail"]:
                raise state["fail"]
            return _FakeContext()

    class _PW:
        chromium = _Chromium()

    monkeypatch.setattr(clearcote, "_playwright", lambda: _PW())
    monkeypatch.setattr(clearcote, "install_humanize_on_context", lambda *a, **k: None)
    return state


def _close(context):
    for fn in context.handlers.get("close", []):
        fn(context)


# --------------------------------------------------------------------------------------- launch()
def test_launch_removes_its_profile_when_the_browser_fails(temp, exe, pw):
    pw["fail"] = RuntimeError(BROWSER_FAILED)
    with pytest.raises(RuntimeError, match=BROWSER_FAILED):
        clearcote.launch(executable_path=exe, headless=False)
    assert os.path.basename(pw["dirs"][-1]).startswith("clearcote-run-")
    assert os.listdir(temp) == []


def test_launch_removes_its_profile_when_options_fail_before_any_browser(temp, exe, pw):
    with pytest.raises(FileNotFoundError):
        clearcote.launch(executable_path=exe, headless=False, profile="no-such-profile")
    assert pw["dirs"] == []
    assert os.listdir(temp) == []


def test_launch_still_removes_its_profile_on_close(temp, exe, pw):
    browser = clearcote.launch(executable_path=exe, headless=False)
    assert len(os.listdir(temp)) == 1
    _close(browser)
    assert os.listdir(temp) == []


# --------------------------------------------------------------------------------- launch_agent()
def test_launch_agent_removes_its_profile_when_the_launch_fails(temp, exe, pw):
    pw["fail"] = RuntimeError(BROWSER_FAILED)
    with pytest.raises(RuntimeError, match=BROWSER_FAILED):
        clearcote.launch_agent(executable_path=exe, headless=False)
    assert os.path.basename(pw["dirs"][-1]).startswith("clearcote-agent-")
    assert os.listdir(temp) == []


def test_launch_agent_removes_the_profile_it_created_on_close(temp, exe, pw):
    context = clearcote.launch_agent(executable_path=exe, headless=False)
    udd = pw["dirs"][-1]
    assert os.path.isdir(udd)
    _close(context)
    assert not os.path.exists(udd)
    assert os.listdir(temp) == []


def test_launch_agent_keeps_a_profile_the_caller_named(tmp_path, temp, exe, pw):
    keep = tmp_path / "keep-me"
    keep.mkdir()
    context = clearcote.launch_agent(str(keep), executable_path=exe, headless=False)
    assert pw["dirs"][-1] == str(keep)
    assert "close" not in context.handlers  # nothing registered to delete it
    assert keep.is_dir()


# --------------------------------------------------------------------------- async launch_agent()
@pytest.fixture
def async_pw(monkeypatch):
    state = {"fail": None, "dirs": []}

    class _Chromium:
        async def launch_persistent_context(self, user_data_dir, **kw):
            state["dirs"].append(user_data_dir)
            if state["fail"]:
                raise state["fail"]
            return _FakeContext()

    class _PW:
        chromium = _Chromium()

        async def stop(self):
            pass

    async def _start():
        return _PW()

    async def _no_humanize(*a, **k):
        return None

    monkeypatch.setattr(async_api, "_start_driver", _start)
    monkeypatch.setattr(async_api, "_bind_driver", lambda *a, **k: None)
    monkeypatch.setattr(async_api, "install_humanize_on_context", _no_humanize)
    return state


async def test_async_launch_agent_removes_its_profile_when_the_launch_fails(temp, exe, async_pw):
    async_pw["fail"] = RuntimeError(BROWSER_FAILED)
    with pytest.raises(RuntimeError, match=BROWSER_FAILED):
        await async_api.launch_agent(executable_path=exe, headless=False)
    assert os.path.basename(async_pw["dirs"][-1]).startswith("clearcote-agent-")
    assert os.listdir(temp) == []


async def test_async_launch_agent_removes_the_profile_it_created_on_close(temp, exe, async_pw):
    context = await async_api.launch_agent(executable_path=exe, headless=False)
    udd = async_pw["dirs"][-1]
    assert os.path.isdir(udd)
    for fn in context.handlers.get("close", []):
        await fn(context)
    assert not os.path.exists(udd)
    assert os.listdir(temp) == []
