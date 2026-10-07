"""Persona font lists (Fix 1) and the unreachable-persona warning (Fix 4)."""

import json
import os

import pytest

from clearcote import _fontpersona as fpm
from clearcote._warnings import coherence_warnings


@pytest.fixture(autouse=True)
def _clear_process_caches():
    fpm._picked.clear()
    fpm._noted.clear()
    yield
    fpm._picked.clear()
    fpm._noted.clear()


def _corpus(tmp_path, donors):
    for pid, fonts in donors.items():
        (tmp_path / (pid + ".json")).write_text(json.dumps({
            "navigator": {"platform": "Linux x86_64",
                          "userAgent": "Mozilla/5.0 (X11; Linux x86_64)"},
            "fonts": {"detected": fonts},
        }), encoding="utf-8")
    return str(tmp_path)


def _with_corpus(monkeypatch, directory):
    import clearcote._profileauto as pa
    monkeypatch.setattr(pa, "DEFAULT_LOCAL_DIR", directory)
    # index cache is keyed by directory+mtime, but pin it into the tmp dir anyway so a developer's
    # real ~/.clearcote cache is never read or written by the tests.
    monkeypatch.setattr(pa, "_local_index_cache_path",
                        lambda: os.path.join(directory, "index-cache.json"))


LINUXY = ["DejaVu Sans", "DejaVu Serif", "Liberation Sans", "Liberation Mono", "FreeSans"]
DONOR = LINUXY + ["Fake Family %d" % i for i in range(60)]


# --------------------------------------------------------------------------- Fix 1: injection

def test_injects_a_real_font_list_for_a_seeded_persona(tmp_path, monkeypatch):
    _with_corpus(monkeypatch, _corpus(tmp_path, {"d1": DONOR}))
    monkeypatch.setattr(fpm, "host_font_families", lambda: None)
    fp = {"fingerprint": "seed-1", "platform": "linux"}
    fpm.ensure_persona_fonts(fp, quiet=True)
    assert fp["fingerprint_profile"] == {"fonts": {"detected": DONOR}}


def test_pick_is_deterministic_per_seed(tmp_path, monkeypatch):
    _with_corpus(monkeypatch, _corpus(tmp_path, {"d%d" % i: DONOR + ["Only%d" % i]
                                                 for i in range(12)}))
    monkeypatch.setattr(fpm, "host_font_families", lambda: None)
    picks = []
    for _ in range(3):
        fpm._picked.clear()
        fp = {"fingerprint": "stable", "platform": "linux"}
        fpm.ensure_persona_fonts(fp, quiet=True)
        picks.append(fp["fingerprint_profile"]["fonts"]["detected"])
    assert picks[0] == picks[1] == picks[2]


def test_different_seeds_can_draw_different_donors(tmp_path, monkeypatch):
    _with_corpus(monkeypatch, _corpus(tmp_path, {"d%d" % i: DONOR + ["Only%d" % i]
                                                 for i in range(40)}))
    monkeypatch.setattr(fpm, "host_font_families", lambda: None)
    seen = set()
    for seed in ("a", "b", "c", "d", "e", "f", "g", "h"):
        fpm._picked.clear()
        fp = {"fingerprint": seed, "platform": "linux"}
        fpm.ensure_persona_fonts(fp, quiet=True)
        seen.add(tuple(fp["fingerprint_profile"]["fonts"]["detected"]))
    assert len(seen) > 1, "every seed drew the same donor - no per-persona diversity"


def test_prefers_a_donor_this_host_can_actually_render(tmp_path, monkeypatch):
    reachable = LINUXY * 12          # 60 names, all installed
    unreachable = ["Nope %d" % i for i in range(60)]
    _with_corpus(monkeypatch, _corpus(tmp_path, {"poor": unreachable, "good": reachable}))
    monkeypatch.setattr(fpm, "host_font_families",
                        lambda: frozenset(x.lower() for x in LINUXY))
    fp = {"fingerprint": "whatever", "platform": "linux"}
    fpm.ensure_persona_fonts(fp, quiet=True)
    assert fp["fingerprint_profile"]["fonts"]["detected"] == reachable


# ------------------------------------------------------------------------------ Fix 1: no-ops

@pytest.mark.parametrize("fp,why", [
    ({}, "no seed at all - a caller passing nothing keeps the untouched host path"),
    ({"fingerprint": "s", "light_stealth": True}, "light_stealth emits no persona by design"),
    ({"fingerprint": "s", "platform": "android"}, "android has no canonical desktop font set"),
])
def test_no_op_cases(tmp_path, monkeypatch, fp, why):
    _with_corpus(monkeypatch, _corpus(tmp_path, {"d1": DONOR}))
    monkeypatch.setattr(fpm, "host_font_families", lambda: None)
    before = dict(fp)
    fpm.ensure_persona_fonts(fp, quiet=True)
    assert fp == before, why


def test_explicit_profile_is_never_overwritten(tmp_path, monkeypatch):
    _with_corpus(monkeypatch, _corpus(tmp_path, {"d1": DONOR}))
    monkeypatch.setattr(fpm, "host_font_families", lambda: None)
    mine = {"fonts": {"detected": ["Mine Only"]}}
    fp = {"fingerprint": "s", "platform": "linux", "fingerprint_profile": mine}
    fpm.ensure_persona_fonts(fp, quiet=True)
    assert fp["fingerprint_profile"] is mine


def test_missing_corpus_never_raises(monkeypatch):
    import clearcote._profileauto as pa
    monkeypatch.setattr(pa, "DEFAULT_LOCAL_DIR", "/nonexistent/clearcote/corpus")
    fp = {"fingerprint": "s", "platform": "linux"}
    fpm.ensure_persona_fonts(fp, quiet=True)
    assert "fingerprint_profile" not in fp


# ------------------------------------------------------------------- Fix 4: coherence warning

def _codes(opts):
    return {w["code"] for w in coherence_warnings(opts)}


def test_warns_when_the_persona_font_identity_is_unreachable():
    warns = [w for w in coherence_warnings({"_font_reach": (1513, 65)})
             if w["code"] == "persona-fonts-unreachable"]
    assert len(warns) == 1
    assert "1513" in warns[0]["message"] and "65" in warns[0]["message"]
    assert warns[0]["severity"] == "warn"


@pytest.mark.parametrize("reach", [None, (300, 280), (39, 1), (100, 50)])
def test_no_warning_when_reachable_or_unknown_or_tiny(reach):
    assert "persona-fonts-unreachable" not in _codes({"_font_reach": reach})


def test_font_reachability_is_none_without_a_font_list(monkeypatch):
    monkeypatch.setattr(fpm, "host_font_families",
                        lambda: frozenset(x.lower() for x in LINUXY))
    assert fpm.font_reachability(None) is None
    assert fpm.font_reachability({"navigator": {}}) is None
    assert fpm.font_reachability({"fonts": {"detected": LINUXY}}) == (5, 5)


def test_basic_families_always_count_as_reachable():
    # IsBasicFont() (font_cache.cc:207) lets these through whatever the reference list says.
    assert fpm.reachable_count(["Arial", "Times New Roman", "monospace"], frozenset()) == 3
