"""``profile="auto"`` — resolve a persona from the best source available.

Port of the Node SDK's ``profileauto.ts``.

ORDER: the licensed clearcote service first, a local imported directory as backup.

The service is preferred because its corpus is fresher, larger and curated. But it can be
unreachable for reasons that are none of the caller's fault — no license, an exhausted daily
budget, a rate limit, a network blip, an outage — and in every one of those a local profile is
enormously better than no persona. So the fallback is by design, not a patch.

THE FALLBACK IS ALWAYS ANNOUNCED. A silent downgrade would leave a caller believing they are on a
fresh service profile while they are on a stale local one, and this product is about knowing
exactly what identity you present. ``source`` is on every result and a one-line reason goes to
stderr unless ``quiet``.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

from ._profileimport import import_directory, load_imported_profile
from ._profilelib import select_profile
from ._profilesource import fetch_index, fetch_profile, profile_cache_dir

#: Where an imported (e.g. converted chrome-fingerprints) directory is looked for by default.
DEFAULT_LOCAL_DIR = os.environ.get("CLEARCOTE_PROFILE_SOURCE") or os.path.join(
    os.path.expanduser("~"), ".clearcote", "profiles", "imported"
)


def local_setup_hint(directory: str) -> str:
    """How to populate a local backup directory.

    OPTIONAL, AND NOT SOMETHING WE SHIP. Clearcote redistributes no third-party fingerprints; the
    dataset is fetched by the user from its own repository under its own terms, and we only read
    the converted format. Any directory of clearcote-profile JSON works just as well, which is
    why this is phrased as one example rather than the required path.
    """
    return "\n".join([
        'A local profile directory is optional, and gives "auto" a backup when the service is',
        "unavailable. Any folder of clearcote-profile JSON works — your own captures, or a",
        "third-party dataset you convert yourself. For example, using the open chrome-fingerprints",
        "dataset (fetched by you, from its own repo, under its own licence):",
        "",
        "    pip install chrome-fingerprints",
        f'    python tools/fingerprint-collect/convert_dataset.py --out "{directory}" --count 500',
        "",
        "Or capture your own with tools/fingerprint-collect (collect.html), or point",
        "CLEARCOTE_PROFILE_SOURCE at a directory you already have.",
    ])


def _note(quiet: Optional[bool], msg: str) -> None:
    if not quiet:
        sys.stderr.write(f"[clearcote] [profile] {msg}\n")


# The optional-backup tip is worth saying once, not on every launch.
_hinted_this_process = False


def _reset_hint() -> None:
    """Test seam: reset the once-per-process tip."""
    global _hinted_this_process
    _hinted_this_process = False


def _local_index_cache_path() -> str:
    return os.path.join(profile_cache_dir(), "local-index.json")


def load_local_index(directory: str) -> list[dict[str, Any]]:
    """Index a directory, cached and invalidated by its mtime.

    Reading every file for a 10k-profile directory is seconds of I/O — far too slow to repeat per
    launch. Keying on mtime means newly converted profiles are picked up without a manual step.
    """
    mtime = os.stat(directory).st_mtime
    path = _local_index_cache_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                c = json.load(f)
            if c.get("dir") == directory and c.get("mtime") == mtime:
                return c["index"]
        except (OSError, ValueError, KeyError):
            pass  # fall through and re-index

    index = import_directory(directory)["index"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"dir": directory, "mtime": mtime, "index": index}, f)
    except OSError:
        pass  # a cache write failure must never fail a launch
    return index


def resolve_local(host: dict[str, Any], local_dir: Optional[str] = None, **select) -> dict[str, Any]:
    """Resolve a persona from the local imported directory."""
    directory = local_dir or DEFAULT_LOCAL_DIR
    if not os.path.isdir(directory):
        raise ValueError(
            f"no local profile directory at {directory}\n\n{local_setup_hint(directory)}"
        )
    index = load_local_index(directory)
    if not index:
        raise ValueError(
            f"local profile directory {directory} contains no usable clearcote-profile JSON"
        )
    selection = select_profile(index, host, **select)
    return {
        "profile": load_imported_profile(directory, selection["entry"]["id"]),
        "selection": selection,
        "source": "local",
    }


def resolve_auto(
    host: dict[str, Any],
    license_key: Optional[str] = None,
    api_base: Optional[str] = None,
    local_dir: Optional[str] = None,
    prefer_local: bool = False,
    offline: bool = False,
    quiet: Optional[bool] = None,
    **select,
) -> dict[str, Any]:
    """Resolve ``profile="auto"``: service first, local directory as backup.

    Any service failure falls back — including 401 (no license) and 429 (rate limited or daily
    distinct budget exhausted) — because in all of those a local persona is better than none and
    far better than a hard launch failure.
    """
    global _hinted_this_process

    if prefer_local:
        r = resolve_local(host, local_dir, **select)
        _note(quiet, f"using local profile {r['selection']['entry']['id']} (prefer_local)")
        return r

    try:
        index = fetch_index(host, license_key=license_key, api_base=api_base, offline=offline,
                            os_override=select.get("os_override"))
        if not index:
            raise ValueError("service returned no candidates for this host")
        selection = select_profile(index, host, **select)
        profile = fetch_profile(selection["entry"]["id"], license_key=license_key,
                                api_base=api_base, offline=offline)
        _note(
            quiet,
            f"using service profile {selection['entry']['id']} "
            f"(score {selection['score']:.2f}, pool {selection['pool_size']}, mode {selection['mode']})",
        )
        # Mention the optional backup ONCE per process, and only while there is none. The useful
        # moment to learn about a fallback is before you need it; repeating it every launch would
        # be noise, and nagging someone who already set one up would be wrong.
        directory = local_dir or DEFAULT_LOCAL_DIR
        if not _hinted_this_process and not os.path.isdir(directory):
            _hinted_this_process = True
            _note(quiet, 'tip: no local backup directory configured. If the service is ever rate '
                         'limited or unreachable, "auto" fails rather than falling back. Setting '
                         f"one up is optional:\n{local_setup_hint(directory)}")
        return {"profile": profile, "selection": selection, "source": "service"}
    except Exception as service_err:  # noqa: BLE001 - any service failure is a fallback trigger
        reason = str(service_err)[:200]
        directory = local_dir or DEFAULT_LOCAL_DIR
        no_local_dir = not os.path.isdir(directory)
        try:
            r = resolve_local(host, local_dir, **select)
            _note(quiet, f"service unavailable ({reason}) — falling back to local profile "
                         f"{r['selection']['entry']['id']}")
            r["fallback_reason"] = reason
            return r
        except Exception as local_err:  # noqa: BLE001
            # Report BOTH failures. Surfacing only the local one would send the caller to fix a
            # directory when the real problem was an expired license.
            local_msg = f"no directory at {directory}" if no_local_dir else str(local_err)
            hint = f"\n\n{local_setup_hint(directory)}" if no_local_dir else ""
            raise ValueError(
                'profile="auto" could not resolve a persona.\n'
                f"  service: {reason}\n"
                f"  local:   {local_msg}{hint}"
            ) from local_err
