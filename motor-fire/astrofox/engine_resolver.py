"""
Star Driver Engine Resolver
Detects pre-installed Star Engine / Camoufox binaries on user machines across
Windows, Linux, and macOS without requiring redundant 600MB downloads.
"""

import os
import sys
from pathlib import Path
from typing import List, Optional


def get_system_candidate_dirs() -> List[Path]:
    """
    Returns candidate directories where the Star Multlogin / Star Engine
    is canonically installed on Windows, Linux, and macOS.
    """
    candidates: List[Path] = []

    # 1. Custom / Overridden Environment Variables
    if os.getenv("STAR_DATA_DIR"):
        p = Path(os.getenv("STAR_DATA_DIR", "")).expanduser()
        candidates.append(p / "binaries" / ".engine")
        candidates.append(p / "binaries")
        candidates.append(p)

    if os.getenv("STAR_ENGINE_DIR"):
        candidates.append(Path(os.getenv("STAR_ENGINE_DIR", "")).expanduser())

    home = Path.home()

    # 2. OS-Specific Standard Application Data Directories
    if sys.platform.startswith("win"):
        # Windows (%LOCALAPPDATA% and %APPDATA%)
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            lad = Path(local_app_data)
            candidates.append(lad / "star-multlogin" / "binaries" / ".engine")
            candidates.append(lad / "star-multlogin" / "binaries")
            candidates.append(lad / "Star" / "binaries" / ".engine")

        app_data = os.getenv("APPDATA")
        if app_data:
            ad = Path(app_data)
            candidates.append(ad / "star-multlogin" / "binaries" / ".engine")
            candidates.append(ad / "star-multlogin" / "binaries")

        prog_files = os.getenv("ProgramFiles")
        if prog_files:
            pf = Path(prog_files)
            candidates.append(pf / "Star Multlogin" / "resources" / "binaries" / ".engine")
            candidates.append(pf / "star-multlogin" / "binaries" / ".engine")

    elif sys.platform == "darwin":
        # macOS (~/Library/Application Support)
        app_support = home / "Library" / "Application Support"
        candidates.append(app_support / "star-multlogin" / "binaries" / ".engine")
        candidates.append(app_support / "star-multlogin" / "binaries")
        candidates.append(app_support / "Star" / "binaries" / ".engine")
        candidates.append(Path("/Applications/Star Multlogin.app/Contents/Resources/binaries/.engine"))
        candidates.append(Path("/Applications/Star.app/Contents/Resources/binaries/.engine"))

    else:
        # Linux / Unix (~/.local/share and /opt)
        xdg_data_home = os.getenv("XDG_DATA_HOME")
        if xdg_data_home:
            xdg_p = Path(xdg_data_home)
            candidates.append(xdg_p / "star-multlogin" / "binaries" / ".engine")
            candidates.append(xdg_p / "star-multlogin" / "binaries")

        candidates.append(home / ".local" / "share" / "star-multlogin" / "binaries" / ".engine")
        candidates.append(home / ".local" / "share" / "star-multlogin" / "binaries")
        candidates.append(home / ".local" / "share" / "star" / "binaries" / ".engine")
        candidates.append(Path("/opt/star-multlogin/binaries/.engine"))
        candidates.append(Path("/opt/star-multlogin/binaries"))

    # 3. Development / Local Repository Fallbacks
    cwd = Path.cwd()
    candidates.append(cwd / "app-data" / "binaries" / ".engine")
    candidates.append(cwd / "binaries" / ".engine")
    candidates.append(cwd / ".." / "app-desktop" / "backend" / "app-data" / "binaries" / ".engine")

    return candidates


def get_binary_names_for_os() -> List[str]:
    """
    Returns prioritized executable names for the current OS.
    """
    if sys.platform.startswith("win"):
        return [
            "star-engine.exe",
            "camoufox.exe",
            "firefox.exe",
        ]
    elif sys.platform == "darwin":
        return [
            "Camoufox.app/Contents/MacOS/camoufox",
            "Contents/MacOS/camoufox",
            "star-engine",
            "camoufox",
            "firefox",
        ]
    else:  # Linux / Unix
        return [
            "star-engine",
            "camoufox-bin",
            "camoufox",
            "firefox",
        ]


def resolve_system_engine() -> Optional[str]:
    """
    Searches the host machine for an existing Star Multlogin / Camoufox binary.
    Returns the absolute path to the executable if found and valid, otherwise None.
    """
    # 1. Explicit environment variable has top priority
    for env_var in ("STAR_ENGINE_PATH", "CAMOUFOX_EXECUTABLE_PATH"):
        custom_path = os.getenv(env_var, "").strip()
        if custom_path:
            p = Path(custom_path).expanduser().resolve()
            if p.is_file() and os.access(p, os.X_OK if not sys.platform.startswith("win") else os.F_OK):
                return str(p)

    candidate_dirs = get_system_candidate_dirs()
    bin_names = get_binary_names_for_os()

    for directory in candidate_dirs:
        if not directory.exists() or not directory.is_dir():
            continue

        for name in bin_names:
            target = (directory / name).resolve()
            if target.is_file():
                # Check executable permission on Unix
                if not sys.platform.startswith("win"):
                    try:
                        if not os.access(target, os.X_OK):
                            # Tenta dar permissão de execução caso tenha sido extraído sem +x
                            os.chmod(target, os.stat(target).st_mode | 0o755)
                    except Exception:
                        pass
                return str(target)

    return None
