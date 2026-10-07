"""
Engine Resolver para Chromium / Clearcote / AstroBrowser.
Detecta binários locais pré-instalados na máquina (Linux, Windows, macOS),
especialmente no diretório padrão do Star Multlogin, evitando downloads
desnecessários de 400 MB.
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path
from typing import Optional


def get_default_star_data_dir() -> Optional[Path]:
    """Retorna o diretório base de dados do Star Multlogin por sistema operacional."""
    custom = os.environ.get("STAR_DATA_DIR")
    if custom and Path(custom).exists():
        return Path(custom)

    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "star-multlogin"
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "star-multlogin"
    elif sys.platform == "darwin":
        home = Path.home()
        return home / "Library" / "Application Support" / "star-multlogin"
    else:
        # Linux / BSD
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            return Path(xdg_data) / "star-multlogin"
        return Path.home() / ".local" / "share" / "star-multlogin"

    return None


def get_default_clearcote_cache_dir() -> Optional[Path]:
    """Retorna o diretório base de cache local do Clearcote."""
    custom = os.environ.get("CLEARCOTE_CACHE")
    if custom and Path(custom).exists():
        return Path(custom)

    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app) / "clearcote" / "Cache"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "clearcote"
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            return Path(xdg_cache) / "clearcote"
        return Path.home() / ".cache" / "clearcote"

    return None


def resolve_system_engine() -> Optional[str]:
    """
    Busca o binário do Chromium na máquina nas seguintes prioridades:
    1. Variáveis de ambiente explícitas (STAR_CHROMIUM_PATH, CLEARCOTE_BINARY, etc.)
    2. Binário nativo instalado pelo aplicativo Star Multlogin (binaries/.engine-chromium)
    3. Binário em modo desenvolvimento local (app-desktop/backend/app-data/binaries/.engine-chromium)
    4. Cache prévio do Clearcote (~/.cache/clearcote/.../browser/chrome)
    """
    # 1. Overrides de variáveis de ambiente
    for env_var in ("STAR_CHROMIUM_PATH", "STAR_ENGINE_CHROMIUM_PATH", "CLEARCOTE_BINARY"):
        val = os.environ.get(env_var)
        if val and os.path.isfile(val) and os.access(val, os.X_OK if sys.platform != "win32" else os.F_OK):
            return str(Path(val).resolve())

    # Nomes de executáveis comuns por OS
    if sys.platform == "win32":
        exe_names = ["chrome.exe", "clearcote.exe", "star-chromium.exe"]
    elif sys.platform == "darwin":
        exe_names = [
            "chrome",
            "clearcote",
            "Clearcote.app/Contents/MacOS/Clearcote",
            "Chromium.app/Contents/MacOS/Chromium",
            "star-chromium",
        ]
    else:
        exe_names = ["chrome", "clearcote", "star-chromium"]

    # 2. Pastas dentro de Star Multlogin
    star_base = get_default_star_data_dir()
    candidate_subdirs = [
        "binaries/.engine-chromium",
        "binaries/.engine-chromium/browser",
        "binaries/astrobrowser",
        "binaries/astrobrowser/browser",
        "binaries/chromium",
    ]

    if star_base and star_base.exists():
        for subdir in candidate_subdirs:
            target_dir = star_base / subdir
            if target_dir.exists():
                for name in exe_names:
                    p = target_dir / name
                    if p.is_file():
                        return str(p.resolve())

    # 3. Caminho de desenvolvimento local (se rodando dentro do workspace)
    try:
        repo_root = Path(__file__).resolve().parents[3]
        dev_base = repo_root / "app-desktop" / "backend" / "app-data"
        if dev_base.exists():
            for subdir in candidate_subdirs:
                target_dir = dev_base / subdir
                if target_dir.exists():
                    for name in exe_names:
                        p = target_dir / name
                        if p.is_file():
                            return str(p.resolve())
    except Exception:
        pass

    # 4. Fallback: Cache local do Clearcote
    cache_base = get_default_clearcote_cache_dir()
    if cache_base and cache_base.exists():
        # Ex: ~/.cache/clearcote/v0.1.0-pre.23/browser/chrome
        pattern_1 = str(cache_base / "*" / "browser" / "*")
        for match in glob.glob(pattern_1):
            p = Path(match)
            if p.is_file() and p.name in exe_names:
                if sys.platform == "win32" or os.access(str(p), os.X_OK):
                    return str(p.resolve())

        # Subpasta direta
        for name in exe_names:
            for match in glob.glob(str(cache_base / "*" / name)):
                p = Path(match)
                if p.is_file():
                    return str(p.resolve())

    return None
