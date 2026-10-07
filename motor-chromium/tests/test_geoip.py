from clearcote.geoip import accept_language_for_country, resolve_geo


def test_accept_language_for_country():
    # one OS-locale tag per country; launch() expands it to Chrome's own list (see test_languages)
    assert accept_language_for_country("US") == "en-US"
    assert accept_language_for_country("de") == "de-DE"  # case-insensitive
    assert accept_language_for_country("BR") == "pt-BR"
    assert accept_language_for_country("JP") == "ja-JP"


def test_accept_language_fallback():
    assert accept_language_for_country("ZZ") == "en-US"
    assert accept_language_for_country("") == "en-US"
    assert accept_language_for_country(None) == "en-US"


def test_accept_language_has_no_q_weights():
    # A ';q=' in --accept-lang trips a Chromium DCHECK; the map must never contain one.
    for cc in ("US", "DE", "FR", "CA", "BR", "JP", "ZZ"):
        assert ";" not in accept_language_for_country(cc)


def test_resolve_geo_dead_socks_returns_none():
    # The lookup now goes THROUGH a SOCKS proxy, and must never fall back to the local IP under a
    # proxy (wrong region): a dead SOCKS proxy returns None within the budget.
    assert resolve_geo({"server": "socks5://127.0.0.1:9"}, quiet=True, timeout=1.5) is None
