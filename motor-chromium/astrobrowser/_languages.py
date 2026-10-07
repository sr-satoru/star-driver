"""Chrome's own language defaults, so a persona's languages look like a fresh Chrome profile.

Genuine Chrome derives everything from ONE value, the OS locale: it resolves that to a UI locale
it ships (de-AT -> ``de``, en-CA -> ``en-GB``, es-MX -> ``es-419``), then ``navigator.languages``
and the Accept-Language header are that UI locale's built-in default list (``IDS_ACCEPT_LANGUAGES``)
and ``Intl`` uses the UI locale itself. Measured on Chrome 154 (Windows) for 67 OS locales; the
lists below are the 153 tree's ``components/strings/components_locale_settings_<ui>.xtb`` values
and match every measurement.

Mirrors sdk/node/src/languages.ts and sdk/dotnet/src/Clearcote/Languages.cs.
"""

import re

# Desktop Chrome's UI locales (the .pak set Google Chrome ships on Windows/Linux/macOS).
CHROME_UI_LOCALES = frozenset((
    "af", "am", "ar", "bg", "bn", "ca", "cs", "da", "de", "el", "en-GB", "en-US", "es", "es-419",
    "et", "fa", "fi", "fil", "fr", "gu", "he", "hi", "hr", "hu", "id", "it", "ja", "kn", "ko", "lt",
    "lv", "ml", "mr", "ms", "nb", "nl", "pl", "pt-BR", "pt-PT", "ro", "ru", "sk", "sl", "sr", "sv",
    "sw", "ta", "te", "th", "tr", "uk", "ur", "vi", "zh-CN", "zh-TW",
))

# IDS_ACCEPT_LANGUAGES per UI locale. A UI locale missing here (af, ms, ur, en-US) has no
# translation and uses the source default "en-US,en" -- e.g. Malay Chrome sends en-US,en.
_ACCEPT_LANGUAGES = {
    "am": "am,en-GB,en", "ar": "ar,en-US,en", "bg": "bg-BG,bg", "bn": "bn-IN,bn,en-US,en",
    "ca": "ca-ES,ca", "cs": "cs-CZ,cs", "da": "da-DK,da,en-US,en", "de": "de-DE,de,en-US,en",
    "el": "el-GR,el", "en-GB": "en-GB,en-US,en", "es": "es-ES,es", "es-419": "es-419,es",
    "et": "et-EE,et,en-US,en", "fa": "fa,en-US,en", "fi": "fi-FI,fi,en-US,en",
    "fil": "fil,fil-PH,tl,en-US,en", "fr": "fr-FR,fr,en-US,en", "gu": "gu-IN,gu,hi-IN,hi,en-US,en",
    "he": "he-IL,he,en-US,en", "hi": "hi-IN,hi,en-US,en", "hr": "hr-HR,hr,en-US,en",
    "hu": "hu-HU,hu,en-US,en", "id": "id-ID,id,en-US,en", "it": "it-IT,it,en-US,en",
    "ja": "ja,en-US,en", "kn": "kn-IN,kn,en-US,en", "ko": "ko-KR,ko,en-US,en",
    "lt": "lt,en-US,en,ru,pl", "lv": "lv-LV,lv,en-US,en", "ml": "ml-IN,ml,en-US,en",
    "mr": "mr-IN,mr,hi-IN,hi,en-US,en", "nb": "nb-NO,nb,no,nn,en-US,en", "nl": "nl-NL,nl,en-US,en",
    "pl": "pl-PL,pl,en-US,en", "pt-BR": "pt-BR,pt,en-US,en", "pt-PT": "pt-PT,pt,en-US,en",
    "ro": "ro-RO,ro,en-US,en", "ru": "ru-RU,ru,en-US,en", "sk": "sk-SK,sk,cs,en-US,en",
    "sl": "sl-SI,sl,en-GB,en", "sr": "sr-RS,sr,en-US,en", "sv": "sv-SE,sv,en-US,en",
    "sw": "sw,en-GB,en", "ta": "ta-IN,ta,en-US,en", "te": "te-IN,te,hi-IN,hi,en-US,en",
    "th": "th-TH,th", "tr": "tr-TR,tr,en-US,en", "uk": "uk-UA,uk,en-US,en",
    "vi": "vi-VN,vi,fr-FR,fr,en-US,en", "zh-CN": "zh-CN,zh", "zh-TW": "zh-TW,zh,en-US,en",
}

# Legacy / macro codes Chrome's matcher folds into a shipped locale.
_LANGUAGE_ALIASES = {"iw": "he", "in": "id", "tl": "fil", "no": "nb", "nn": "nb"}
# English regions Chrome resolves to en-US (en-PH measured); every other region gets en-GB
# (en-CA/AU/NZ/IE/IN/ZA/SG measured).
_EN_US_REGIONS = frozenset(("", "US", "PH", "AS", "GU", "MH", "MP", "PR", "UM", "VI"))
# Spanish regions that stay "es"; the rest of the Spanish-speaking world gets es-419 (es-MX/AR/CL/
# CO/US measured).
_ES_ES_REGIONS = frozenset(("", "ES", "EA", "IC", "GQ"))


def _split_tag(tag):
    parts = [p for p in re.split(r"[-_]", str(tag).strip()) if p]
    if not parts:
        return "", "", ""
    lang, script, region = parts[0].lower(), "", ""
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            script = part.title()
        elif (len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit()):
            region = part.upper()
    return _LANGUAGE_ALIASES.get(lang, lang), script, region


def chrome_ui_locale(tag):
    """The UI locale Chrome picks for an OS locale ``tag`` (``de-AT`` -> ``de``), or None when Chrome
    ships no UI in that language (it would fall back to the OS's other languages / en-US)."""
    lang, script, region = _split_tag(tag)
    if not lang:
        return None
    if lang == "en":
        return "en-US" if region in _EN_US_REGIONS else "en-GB"
    if lang == "es":
        return "es" if region in _ES_ES_REGIONS else "es-419"
    if lang == "pt":
        return "pt-BR" if region in ("", "BR") else "pt-PT"
    if lang == "zh":
        traditional = script == "Hant" or (script != "Hans" and region in ("TW", "HK", "MO"))
        return "zh-TW" if traditional else "zh-CN"
    return lang if lang in CHROME_UI_LOCALES else None


def chrome_accept_languages(ui_locale):
    """Chrome's default navigator.languages for a UI locale, as a comma list (``de`` ->
    ``de-DE,de,en-US,en``)."""
    return _ACCEPT_LANGUAGES.get(ui_locale, "en-US,en")


def resolve_languages(clean):
    """Map a cleaned Accept-Language value to ``(accept_lang, lang_switch)`` for the engine.

    * ONE tag (``de-AT``) is read as the OS locale: the result is exactly what a fresh Chrome
      profile on that OS shows -- ``("de-DE,de,en-US,en", "de")``.
    * A LIST (``de-AT,de,en``) is the caller's own language list and is kept verbatim; only the
      UI locale (``--lang``, which drives Intl and the browser UI) is resolved from its first tag.
    * A tag Chrome has no UI for (``is-IS``) is passed through unchanged, as before.
    """
    tags = [t for t in clean.split(",") if t]
    if not tags:
        return clean, None
    ui = chrome_ui_locale(tags[0])
    if len(tags) == 1 and ui:
        return chrome_accept_languages(ui), ui
    return clean, ui or tags[0]
