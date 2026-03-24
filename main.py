"""
Flipper — MAC Address Scanner + IPTV Player
Plain Tkinter — Windows compatible.
Features: mpv embedded player, proxy rotation with full retry,
session persistence, profiles with naming, channel search,
navigation stack, progress bar, account info tab, settings tab,
channel count filter, channel cache.
"""

import os
import sys
import platform
import shutil
import subprocess
import ctypes
import traceback
import zipfile
import urllib.request
import urllib.error
import base64
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional

_WIN_DLL_HANDLES = []

# Debug console mode (Windows): can be enabled from Settings (persists in session.json)
# or via env var FLIPPER_DEBUG=1.
_EARLY_DEBUG_ENABLED = False
_DEBUG_CONSOLE_ENABLED = False


def _read_debug_console_flag() -> bool:
    env = os.environ.get("FLIPPER_DEBUG", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    try:
        import json as _json

        candidate_paths = []
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "").strip()
            if appdata:
                candidate_paths.append(os.path.join(appdata, "Flipper", "session.json"))
        desktop = os.path.join(str(Path.home()), "Desktop")
        candidate_paths.append(os.path.join(desktop, "flipper-config", "session.json"))

        for session_path in candidate_paths:
            if os.path.isfile(session_path):
                with open(session_path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                return bool(data.get("debug_console", False))
    except Exception:
        return False
    return False


def _dpapi_protect_bytes(raw: bytes) -> Optional[bytes]:
    if sys.platform != "win32" or not raw:
        return None
    try:

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        data_buf = ctypes.create_string_buffer(raw, len(raw))
        entropy = b"FlipperPATv1"
        entropy_buf = ctypes.create_string_buffer(entropy, len(entropy))

        in_blob = DATA_BLOB(
            len(raw), ctypes.cast(data_buf, ctypes.POINTER(ctypes.c_char))
        )
        ent_blob = DATA_BLOB(
            len(entropy), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_char))
        )
        out_blob = DATA_BLOB()

        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            None,
            ctypes.byref(ent_blob),
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        if not ok:
            return None
        out = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        return out
    except Exception:
        return None


def _dpapi_unprotect_bytes(raw: bytes) -> Optional[bytes]:
    if sys.platform != "win32" or not raw:
        return None
    try:

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.c_uint32),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        data_buf = ctypes.create_string_buffer(raw, len(raw))
        entropy = b"FlipperPATv1"
        entropy_buf = ctypes.create_string_buffer(entropy, len(entropy))

        in_blob = DATA_BLOB(
            len(raw), ctypes.cast(data_buf, ctypes.POINTER(ctypes.c_char))
        )
        ent_blob = DATA_BLOB(
            len(entropy), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_char))
        )
        out_blob = DATA_BLOB()

        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            ctypes.byref(ent_blob),
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        if not ok:
            return None
        out = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        return out
    except Exception:
        return None


def _encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    raw = plain.encode("utf-8")
    protected = _dpapi_protect_bytes(raw)
    if protected is not None:
        return "dpapi:" + base64.b64encode(protected).decode("ascii")

    key = (os.environ.get("USERNAME", "") + "|" + platform.node()).encode("utf-8")
    if not key:
        key = b"flipper"
    obf = bytes([b ^ key[i % len(key)] for i, b in enumerate(raw)])
    return "xor:" + base64.b64encode(obf).decode("ascii")


def _decrypt_secret(cipher: str) -> str:
    if not cipher:
        return ""
    try:
        if cipher.startswith("dpapi:"):
            payload = base64.b64decode(cipher[6:].encode("ascii"))
            plain = _dpapi_unprotect_bytes(payload)
            return plain.decode("utf-8", errors="ignore") if plain else ""
        if cipher.startswith("xor:"):
            payload = base64.b64decode(cipher[4:].encode("ascii"))
            key = (os.environ.get("USERNAME", "") + "|" + platform.node()).encode(
                "utf-8"
            )
            if not key:
                key = b"flipper"
            raw = bytes([b ^ key[i % len(key)] for i, b in enumerate(payload)])
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    return ""


def _enable_windows_console() -> None:
    """Allocate a Windows console and redirect stdout/stderr to it."""
    global _DEBUG_CONSOLE_ENABLED
    if sys.platform != "win32" or _DEBUG_CONSOLE_ENABLED:
        return
    try:
        ctypes.windll.kernel32.AllocConsole()
    except Exception:
        # If already attached or not allowed, continue best-effort.
        pass
    try:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
    except Exception:
        pass
    _DEBUG_CONSOLE_ENABLED = True


def _debug_print(msg: str) -> None:
    if not (_EARLY_DEBUG_ENABLED or _DEBUG_CONSOLE_ENABLED):
        return
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


_EARLY_DEBUG_ENABLED = _read_debug_console_flag()
if sys.platform == "win32" and _EARLY_DEBUG_ENABLED:
    _enable_windows_console()
    _debug_print("[Flipper] Debug console enabled (early).")

# Suppress Windows "not a valid Win32 application" popup dialogs
# MUST be done before ANY DLL loading attempt (ctypes, import mpv, etc.)
if sys.platform == "win32":
    try:
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        ctypes.windll.kernel32.SetErrorMode(0x8003)
    except Exception:
        pass

import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
import threading
import time
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor


def _get_flipper_data_dir() -> str:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            path = os.path.join(appdata, "Flipper")
        else:
            path = os.path.join(str(Path.home()), "AppData", "Roaming", "Flipper")
    else:
        desktop = os.path.join(str(Path.home()), "Desktop")
        path = os.path.join(desktop, "flipper-config")
    os.makedirs(path, exist_ok=True)

    # One-time best-effort migration from legacy Windows locations:
    # %LOCALAPPDATA%\Flipper and Desktop\flipper-config -> %APPDATA%\Flipper
    if sys.platform == "win32":
        try:
            legacy_base = os.environ.get("LOCALAPPDATA")
            if legacy_base:
                legacy_path = os.path.join(legacy_base, "Flipper")
                if os.path.isdir(legacy_path):
                    _migrate_legacy_flipper_data(legacy_path, path)
            old_desktop_path = os.path.join(
                str(Path.home()), "Desktop", "flipper-config"
            )
            if os.path.isdir(old_desktop_path):
                _migrate_legacy_flipper_data(old_desktop_path, path)
        except Exception:
            pass

    return path


def _migrate_legacy_flipper_data(legacy_path: str, new_path: str) -> None:
    if not legacy_path or not new_path:
        return
    if os.path.abspath(legacy_path) == os.path.abspath(new_path):
        return

    # Copy common data files if missing in new location.
    for filename in (
        "session.json",
        "results.txt",
        "config.ini",
        "channels_cache.json",
    ):
        src = os.path.join(legacy_path, filename)
        dst = os.path.join(new_path, filename)
        try:
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
        except Exception:
            pass

    # Copy mpv runtime DLLs to the new location if they exist in legacy.
    legacy_mpv = os.path.join(legacy_path, "mpv")
    new_mpv = os.path.join(new_path, "mpv")
    try:
        os.makedirs(new_mpv, exist_ok=True)
    except Exception:
        return

    # If new mpv dir already has DLLs, do nothing.
    try:
        if any(name.lower().endswith(".dll") for name in os.listdir(new_mpv)):
            return
    except Exception:
        pass

    if not os.path.isdir(legacy_mpv):
        return

    # Find the directory containing libmpv in legacy (it may be nested).
    src_dir = None
    try:
        for root, _dirs, files in os.walk(legacy_mpv):
            lower_files = {f.lower() for f in files}
            if "libmpv-2.dll" in lower_files or "libmpv.dll" in lower_files:
                src_dir = root
                break
    except Exception:
        src_dir = None

    if not src_dir:
        src_dir = legacy_mpv

    # Copy all DLLs from the discovered directory.
    try:
        for name in os.listdir(src_dir):
            if not name.lower().endswith(".dll"):
                continue
            src = os.path.join(src_dir, name)
            dst = os.path.join(new_mpv, name)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
    except Exception:
        pass

    # After migration, DELETE libmpv from legacy to prevent find_library from finding it there
    try:
        for name in ("libmpv-2.dll", "libmpv.dll", "mpv-2.dll", "mpv-1.dll"):
            legacy_dll = os.path.join(src_dir, name)
            if os.path.isfile(legacy_dll):
                os.remove(legacy_dll)
    except Exception:
        pass


def _get_flipper_mpv_dir() -> str:
    path = os.path.join(_get_flipper_data_dir(), "mpv")
    os.makedirs(path, exist_ok=True)
    return path


def _set_dll_directory(path: str) -> bool:
    """Set the default DLL search directory using SetDllDirectoryW.
    This is CRITICAL for loading DLLs with dependencies on Windows."""
    if sys.platform != "win32" or not path:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        # SetDllDirectoryW takes a wide string (Unicode)
        kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel32.SetDllDirectoryW.restype = ctypes.c_bool
        result = kernel32.SetDllDirectoryW(os.path.abspath(path))
        return bool(result)
    except Exception:
        return False


def _load_dll_safe(dll_path: str) -> Optional[ctypes.CDLL]:
    """Load a DLL with proper dependency search on Windows 10+.
    Uses multiple strategies to ensure dependencies can be found."""
    if not os.path.isfile(dll_path):
        return None

    abs_path = os.path.abspath(dll_path)
    dll_dir = os.path.dirname(abs_path)

    # Strategy 1: Set DLL directory for dependency resolution
    _set_dll_directory(dll_dir)

    # Strategy 2: Use add_dll_directory (Python 3.8+, Windows 10+)
    _add_windows_dll_directory(dll_dir)

    # Strategy 3: Prepend to PATH
    _prepend_to_path(dll_dir)

    # Try loading with different methods
    load_errors = []

    # Method 1: winmode=0 (Python 3.8+) - enables default search path
    if sys.version_info >= (3, 8):
        try:
            return ctypes.CDLL(abs_path, winmode=0)
        except OSError as e:
            load_errors.append(f"winmode=0: {e}")

    # Method 2: Standard CDLL with absolute path
    try:
        return ctypes.CDLL(abs_path)
    except OSError as e:
        load_errors.append(f"CDLL: {e}")

    # Method 3: Try with LoadLibraryExW and LOAD_WITH_ALTERED_SEARCH_PATH
    if sys.platform == "win32":
        try:
            LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
            kernel32 = ctypes.windll.kernel32
            kernel32.LoadLibraryExW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            kernel32.LoadLibraryExW.restype = ctypes.c_void_p
            handle = kernel32.LoadLibraryExW(
                abs_path, None, LOAD_WITH_ALTERED_SEARCH_PATH
            )
            if handle:
                # Wrap in CDLL
                return ctypes.CDLL(abs_path)
        except Exception as e:
            load_errors.append(f"LoadLibraryExW: {e}")

    return None


def _process_expected_machine() -> Optional[int]:
    """Return expected PE Machine value for current process on Windows."""
    if sys.platform != "win32":
        return None
    # 0x8664 = AMD64, 0x14c = I386
    try:
        import struct as _struct

        bits = _struct.calcsize("P") * 8
        return 0x8664 if bits == 64 else 0x14C
    except Exception:
        return None


def _pe_machine(dll_path: str) -> Optional[int]:
    """Read PE Machine from a Windows DLL/EXE. Returns None if not PE."""
    try:
        with open(dll_path, "rb") as f:
            mz = f.read(2)
            if mz != b"MZ":
                return None
            f.seek(0x3C)
            pe_off = int.from_bytes(f.read(4), "little", signed=False)
            f.seek(pe_off)
            sig = f.read(4)
            if sig != b"PE\x00\x00":
                return None
            machine = int.from_bytes(f.read(2), "little", signed=False)
            return machine
    except Exception:
        return None


def _dll_matches_process_arch(dll_path: str) -> bool:
    expected = _process_expected_machine()
    if not expected:
        return True
    actual = _pe_machine(dll_path)
    if actual is None:
        return True
    return actual == expected


def _mark_bad_mpv_dll(dll_path: str, reason: str) -> None:
    """Rename a bad mpv DLL so we don't keep trying to load it."""
    try:
        if not os.path.isfile(dll_path):
            return
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = os.path.basename(dll_path)
        bad_name = f"{base}.bad-{ts}"
        bad_path = os.path.join(os.path.dirname(dll_path), bad_name)
        os.replace(dll_path, bad_path)
        _debug_print(
            f"[Flipper] Renamed bad mpv DLL: {dll_path} -> {bad_path} ({reason})"
        )
    except Exception:
        pass


def _copy_mpv_dll_to_runtime_dir() -> Optional[str]:
    """Copy libmpv-2.dll AND all its dependencies to runtime directory"""
    target_dir = _get_flipper_mpv_dir()

    # IMPORTANT: Register target_dir for DLL dependency search BEFORE loading
    # On Windows 10+, DLL dependencies are NOT searched from PATH or cwd
    _add_windows_dll_directory(target_dir)
    _prepend_to_path(target_dir)

    for dll_name in ("libmpv-2.dll", "libmpv.dll"):
        # Prefer stable sources first; _MEIPASS only as last-resort fallback.
        candidate_paths = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            # If bundled, prefer bundled mpv directory first
            candidate_paths.append(os.path.join(meipass, "mpv", dll_name))
            candidate_paths.append(os.path.join(meipass, dll_name))

        candidate_paths.append(os.path.join(target_dir, dll_name))
        candidate_paths.append(os.path.join(_get_flipper_data_dir(), dll_name))
        candidate_paths.append(os.path.join(os.path.dirname(__file__), dll_name))

        for src in candidate_paths:
            if not os.path.isfile(src):
                continue

            # Architecture guard: WinError 193 is almost always 32/64-bit mismatch
            if sys.platform == "win32" and not _dll_matches_process_arch(src):
                _debug_print(f"[Flipper] libmpv candidate has wrong arch: {src}")
                # If the user put wrong DLL into runtime dir, rename it so we stop picking it up.
                if os.path.abspath(os.path.dirname(src)) == os.path.abspath(target_dir):
                    _mark_bad_mpv_dll(src, "arch-mismatch")
                continue

            # Found libmpv DLL - copy it AND all other DLLs from same directory
            src_dir = os.path.dirname(src)
            dst = os.path.join(target_dir, dll_name)

            try:
                # Copy main libmpv DLL
                if not os.path.exists(dst) or os.path.getsize(src) != os.path.getsize(
                    dst
                ):
                    shutil.copy2(src, dst)

                # Copy ALL other DLL files from source directory (dependencies!)
                # This includes avcodec, avformat, swscale, etc.
                if src_dir != target_dir:
                    try:
                        for item in os.listdir(src_dir):
                            if item.lower().endswith((".dll", ".dll.a")):
                                src_dep = os.path.join(src_dir, item)
                                dst_dep = os.path.join(target_dir, item)
                                if os.path.isfile(src_dep):
                                    if not os.path.exists(dst_dep) or os.path.getsize(
                                        src_dep
                                    ) != os.path.getsize(dst_dep):
                                        shutil.copy2(src_dep, dst_dep)
                    except Exception:
                        pass  # Non-critical if dependency copy fails

                # Verify the main DLL is actually loadable
                # Use winmode=0 to enable DLL directory search for dependencies
                dll_handle = _load_dll_safe(dst)
                if dll_handle:
                    _WIN_DLL_HANDLES.append(dll_handle)
                    return target_dir
                # If loading failed, continue to next candidate
                continue
            except Exception:
                continue
    return None


def _prepend_to_path(path: str):
    if not path:
        return
    # MUST use absolute path — python-mpv requires all PATH entries to be
    # absolute, otherwise ctypes.find_library returns a relative path which
    # causes CDLL to fail with LOAD_LIBRARY_SEARCH_DEFAULT_DIRS.
    abs_path = os.path.abspath(path)
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if abs_path not in parts:
        os.environ["PATH"] = abs_path + os.pathsep + current


def _add_windows_dll_directory(path: str):
    if sys.platform != "win32" or not path:
        return
    # Must use absolute path for add_dll_directory
    abs_path = os.path.abspath(path)
    add_dir = getattr(os, "add_dll_directory", None)
    if not add_dir:
        return
    try:
        handle = add_dir(abs_path)
        _WIN_DLL_HANDLES.append(handle)
    except OSError:
        # Already added or path doesn't exist, ignore
        pass


def _is_mpv_dll_loadable() -> bool:
    for dll_name in ("libmpv-2.dll", "libmpv.dll"):
        try:
            if sys.version_info >= (3, 8):
                ctypes.CDLL(dll_name, winmode=0)
            else:
                ctypes.CDLL(dll_name)
            return True
        except OSError:
            pass
    # Also try full path in runtime dir
    runtime_dir = _get_flipper_mpv_dir()
    for dll_name in ("libmpv-2.dll", "libmpv.dll"):
        full = os.path.join(runtime_dir, dll_name)
        if _load_dll_safe(full):
            return True
    return False


def _find_mpv_dll_dir() -> Optional[str]:
    candidates = []

    # Stable runtime location first (avoids Temp onefile extraction path)
    candidates.append(_get_flipper_mpv_dir())
    candidates.append(_get_flipper_data_dir())

    mpv_bin = shutil.which("mpv")
    if mpv_bin:
        candidates.append(os.path.dirname(mpv_bin))

    local_mpv_dir = os.path.join(os.path.dirname(__file__), ".mpv")
    candidates.append(local_mpv_dir)

    path_env = os.environ.get("PATH", "")
    if path_env:
        candidates.extend(path_env.split(os.pathsep))

    seen = set()
    for directory in candidates:
        if not directory or directory in seen:
            continue
        seen.add(directory)
        for dll_name in ("libmpv-2.dll", "libmpv.dll"):
            if os.path.isfile(os.path.join(directory, dll_name)):
                return directory
    return None


def _find_mpv_dll_under(root: str, max_depth: int = 5) -> Optional[str]:
    root_path = Path(root)
    if not root_path.exists():
        return None

    for current_root, dirs, files in os.walk(root):
        rel = Path(current_root).relative_to(root_path)
        if len(rel.parts) > max_depth:
            dirs[:] = []
            continue
        if "libmpv-2.dll" in files or "libmpv.dll" in files:
            return current_root
    return None


def _try_install_mpv_with_winget() -> bool:
    winget = shutil.which("winget")
    if not winget:
        return False

    candidate_ids = [
        "shinchiro.mpv",
        "MPV.MPV",
        "mpv.mpv",
    ]

    for package_id in candidate_ids:
        try:
            proc = subprocess.run(
                [
                    winget,
                    "install",
                    "--id",
                    package_id,
                    "-e",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                    "--silent",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
            )
            if proc.returncode == 0:
                return True
        except Exception:
            continue
    return False


def _ensure_mpv_runtime_windows():
    if sys.platform != "win32":
        return

    runtime_dir = _copy_mpv_dll_to_runtime_dir()
    if runtime_dir:
        _add_windows_dll_directory(runtime_dir)
        _prepend_to_path(runtime_dir)
        if _is_mpv_dll_loadable():
            return

    if _is_mpv_dll_loadable():
        return

    dll_dir = _find_mpv_dll_dir()
    if dll_dir:
        _add_windows_dll_directory(dll_dir)
        _prepend_to_path(dll_dir)
        if _is_mpv_dll_loadable():
            return

    installed = _try_install_mpv_with_winget()
    if not installed:
        return

    search_roots = [
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
    ]
    for root in search_roots:
        if not root:
            continue
        found = _find_mpv_dll_under(root)
        if found:
            _add_windows_dll_directory(found)
            _prepend_to_path(found)
            break


_ensure_mpv_runtime_windows()

# On Windows, verify DLL search paths are set up before importing mpv module.
# python-mpv uses ctypes.util.find_library + CDLL with special flags.
if sys.platform == "win32":
    runtime_dir = _get_flipper_mpv_dir()
    # Ensure absolute path is in PATH and DLL directories
    _add_windows_dll_directory(runtime_dir)
    _prepend_to_path(runtime_dir)
    # Also ensure the data dir itself is registered
    data_dir = _get_flipper_data_dir()
    _add_windows_dll_directory(data_dir)
    _prepend_to_path(data_dir)

    # IMPORTANT: Remove legacy LOCALAPPDATA\Flipper paths from PATH
    # to prevent ctypes.util.find_library from finding DLLs there
    try:
        legacy_base = os.environ.get("LOCALAPPDATA", "")
        if legacy_base:
            legacy_paths = [
                os.path.join(legacy_base, "Flipper", "mpv"),
                os.path.join(legacy_base, "Flipper"),
            ]
            current_path = os.environ.get("PATH", "")
            path_parts = current_path.split(os.pathsep)
            # Filter out legacy paths (case-insensitive on Windows)
            filtered = [
                p
                for p in path_parts
                if p.lower() not in {lp.lower() for lp in legacy_paths}
            ]
            os.environ["PATH"] = os.pathsep.join(filtered)
    except Exception:
        pass

    # CRITICAL: Monkey-patch ctypes.util.find_library to return absolute paths
    # python-mpv uses find_library internally, and relative paths fail with LOAD_LIBRARY_SEARCH_DEFAULT_DIRS
    import ctypes.util

    _original_find_library = ctypes.util.find_library

    def _patched_find_library(name):
        # Handle mpv-related library lookups
        mpv_names = ("mpv", "mpv-2", "mpv-1", "libmpv", "libmpv-2", "libmpv-1")
        if name in mpv_names or any(name.lower().startswith(n) for n in mpv_names):
            # Return absolute path to our DLL
            for dll_name in ("libmpv-2.dll", "libmpv.dll", "mpv-2.dll", "mpv.dll"):
                dll_path = os.path.join(runtime_dir, dll_name)
                if os.path.isfile(dll_path):
                    abs_path = os.path.abspath(dll_path)
                    # Pre-set DLL directory for dependency resolution
                    _set_dll_directory(runtime_dir)
                    return abs_path
            # Also check data_dir
            for dll_name in ("libmpv-2.dll", "libmpv.dll", "mpv-2.dll", "mpv.dll"):
                dll_path = os.path.join(data_dir, dll_name)
                if os.path.isfile(dll_path):
                    abs_path = os.path.abspath(dll_path)
                    _set_dll_directory(data_dir)
                    return abs_path
        # Fallback to original
        result = _original_find_library(name)
        # Ensure result is absolute path if it exists
        if result and not os.path.isabs(result):
            # Try to find it as a file
            if os.path.isfile(result):
                return os.path.abspath(result)
        return result

    ctypes.util.find_library = _patched_find_library

    # Pre-load libmpv DLL with proper dependency search
    for dll_name in ("libmpv-2.dll", "libmpv.dll"):
        dll_path = os.path.join(runtime_dir, dll_name)
        if os.path.isfile(dll_path):
            _set_dll_directory(runtime_dir)
            loaded = _load_dll_safe(dll_path)
            if loaded:
                _WIN_DLL_HANDLES.append(loaded)
                break

# Try importing mpv multiple times with aggressive path setup
HAS_MPV = False
MPV_IMPORT_ERROR = None

for _attempt in range(3):
    try:
        import mpv

        HAS_MPV = True
        break
    except Exception as e:
        MPV_IMPORT_ERROR = traceback.format_exc()
        _debug_print("[Flipper] import mpv failed (python-mpv):\n" + MPV_IMPORT_ERROR)
        if _attempt < 2 and sys.platform == "win32":
            # Retry: re-setup paths, copy deps again
            rt = _copy_mpv_dll_to_runtime_dir()
            if rt:
                _set_dll_directory(rt)
                _add_windows_dll_directory(rt)
                _prepend_to_path(rt)
            time.sleep(0.2)

from scanner import (
    generate_random_mac,
    check_mac,
    get_responding_endpoint,
    parse_url,
    get_handshake,
    get_genres,
    get_channels,
    get_stream_url,
    fetch_free_proxies,
    set_proxy_list,
    get_proxy_list,
    add_proxy,
    remove_proxy,
    get_current_proxy,
    rotate_proxy,
    report_proxy_fail,
    report_proxy_success,
    should_remove_proxy,
    make_cookies,
    make_params,
    random_user_agent,
    _request_get,
    count_channels_quick,
    test_proxy_latency,
    test_and_filter_proxies,
    # Proxy tag system
    PROXY_TAG_UNTESTED,
    PROXY_TAG_WORKING,
    PROXY_TAG_DEAD,
    PROXY_TAG_RATE_LIMITED,
    PROXY_TAG_COLORS,
    RATE_LIMIT_COOLDOWN_S,
    set_proxy_tag,
    get_proxy_tag,
    get_all_proxy_tags,
    get_proxy_rate_limited_at,
    get_all_rate_limited_times,
    mark_proxy_rate_limited,
    check_rate_limit_expired,
    get_usable_proxy_count,
    get_proxy_for_multiproxy,
    init_proxy_test_stats,
    update_proxy_test_stats,
    get_proxy_test_stats,
    save_proxy_state,
    load_proxy_state,
)
from constants import RESULTS_FILE, SESSION_FILE


def _diagnose_mpv_availability():
    """Return diagnostic info about mpv availability"""
    import ctypes.util

    info = []
    if sys.platform == "win32":
        info.append("=== MPV Diagnostyka ===")
        info.append(f"Python: {sys.version.split()[0]} ({platform.architecture()[0]})")
        info.append(f"Executable: {sys.executable}")
        expected = _process_expected_machine()
        if expected:
            exp_label = "x64" if expected == 0x8664 else "x86"
            info.append(f"Expected DLL arch: {exp_label} (PE Machine=0x{expected:04x})")

        # python-mpv package info (best-effort)
        try:
            import importlib.metadata as _im

            try:
                info.append(f"python-mpv version: {_im.version('python-mpv')}")
            except Exception:
                # some installs use 'mpv' distribution name
                info.append(f"python-mpv version: {_im.version('mpv')}")
        except Exception:
            pass

        # Check what find_library returns
        for name in ("mpv-2", "libmpv-2", "mpv-1", "libmpv", "mpv"):
            found = ctypes.util.find_library(name)
            if found:
                info.append(f"find_library({name}): {found}")

        # Check runtime dir
        runtime_dir = _get_flipper_mpv_dir()
        info.append(f"Runtime dir: {runtime_dir}")

        # Set DLL directory before trying to load
        _set_dll_directory(runtime_dir)
        _add_windows_dll_directory(runtime_dir)
        _prepend_to_path(runtime_dir)

        # Check for main DLL files
        for dll_name in ("libmpv-2.dll", "libmpv.dll", "mpv-2.dll", "mpv.dll"):
            dll_path = os.path.join(runtime_dir, dll_name)
            if os.path.exists(dll_path):
                size = os.path.getsize(dll_path)
                mach = _pe_machine(dll_path)
                if mach:
                    info.append(
                        f"✓ {dll_name}: {size:,} bytes (PE Machine=0x{mach:04x})"
                    )
                else:
                    info.append(f"✓ {dll_name}: {size:,} bytes")
                # Try multiple loading methods
                loaded = False
                errors = []

                # Method 1: winmode=0
                if sys.version_info >= (3, 8):
                    try:
                        ctypes.CDLL(dll_path, winmode=0)
                        info.append(f"  → winmode=0: OK ✓")
                        loaded = True
                    except Exception as e:
                        errors.append(f"winmode=0: {e}")

                # Method 2: Standard CDLL
                if not loaded:
                    try:
                        ctypes.CDLL(dll_path)
                        info.append(f"  → CDLL: OK ✓")
                        loaded = True
                    except Exception as e:
                        errors.append(f"CDLL: {e}")

                # Method 3: LoadLibraryExW
                if not loaded:
                    try:
                        LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
                        kernel32 = ctypes.windll.kernel32
                        handle = kernel32.LoadLibraryExW(
                            dll_path, None, LOAD_WITH_ALTERED_SEARCH_PATH
                        )
                        if handle:
                            info.append(f"  → LoadLibraryExW: OK ✓")
                            loaded = True
                        else:
                            err = ctypes.get_last_error()
                            errors.append(f"LoadLibraryExW: error {err}")
                    except Exception as e:
                        errors.append(f"LoadLibraryExW exception: {e}")

                if not loaded and errors:
                    info.append(f"  → NIE ładowalny:")
                    for err in errors:
                        info.append(f"    {err}")
            else:
                info.append(f"✗ {dll_name}: nie znaleziono")

        # Try importing mpv right here (shows exact import error)
        try:
            import mpv as _mpv

            info.append("✓ import mpv: OK")
            mod_path = getattr(_mpv, "__file__", None)
            if mod_path:
                info.append(f"mpv module file: {mod_path}")
        except Exception:
            info.append("✗ import mpv: FAIL")
            info.append(traceback.format_exc())

        # Count and list other DLLs (dependencies)
        try:
            dll_files = [
                f
                for f in os.listdir(runtime_dir)
                if f.lower().endswith(".dll")
                and f not in ("libmpv-2.dll", "libmpv.dll")
            ]
            if dll_files:
                info.append(f"✓ Znaleziono {len(dll_files)} zależności DLL")
                if len(dll_files) <= 10:
                    for dll in sorted(dll_files):
                        info.append(f"  - {dll}")
                else:
                    for dll in sorted(dll_files)[:5]:
                        info.append(f"  - {dll}")
                    info.append(f"  ... i {len(dll_files) - 5} więcej")
            else:
                info.append(f"⚠ Brak zależności DLL (może brakować ffmpeg itp.)")
        except Exception:
            pass

        # Check PATH (first 3 entries)
        path_env = os.environ.get("PATH", "")
        path_parts = path_env.split(os.pathsep)[:5]
        info.append(f"PATH (pierwsze 5):")
        for p in path_parts:
            info.append(f"  {p}")

    return "\n".join(info)


MAX_LOG_SAVE = 500
CONFIG_FILE = "config.ini"
CHANNELS_CACHE_FILE = "channels_cache.json"
APP_VERSION = "1.2.0"
BG_DARK = "#0a0a1e"
BG_SIDEBAR = "#1a1a2e"
BG_INPUT = "#12122a"
BG_BAR = "#16162a"
FG_DIM = "#888888"
ACCENT = "#2563eb"
MAX_PROXY_RETRIES = 15
PROXY_TEST_BATCH_SIZE = 5
DEFAULT_UPDATE_REPO = "FilipMichalkiewicz/flipper"
DEFAULT_UPDATE_BRANCH = "main"


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Flipper — MAC Scanner & Player")
        self.root.geometry("1300x800")
        self.root.minsize(1050, 650)

        # ── State ──────────────────────────────────────────
        self.is_running = False
        self.is_paused = False
        self.scan_thread = None
        self.executor = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()

        self.checked_count = 0
        self.found_count = 0
        self.active_macs = []  # [{url, mac, expiry, proxy}, ...]
        self.mac_proxy_map = {}  # {mac: proxy_str}
        self.profiles = []  # [{name, mac, url, proxy}, ...]
        self.active_profile = None  # currently selected profile dict
        self.log_history = []  # [(full_msg, tag), ...]

        # Player state
        self.player_token = None
        self.player_channels = []
        self.player_genres = []
        self.player_content_type = "itv"
        self.mpv_player = None
        self.current_tab = 0
        self.current_stream_url = None
        self._tree_item_to_channel = {}

        # Navigation stack
        self.nav_stack = []

        # Keep on top
        self.keep_on_top_var = tk.BooleanVar(value=False)

        # Settings
        self.verbose_logs_var = tk.BooleanVar(value=False)
        # Debug console: shows full exceptions/diagnostics in a Windows console
        # Note: takes effect immediately for logging, but mpv import happens on startup.
        self.debug_console_var = tk.BooleanVar(value=bool(_EARLY_DEBUG_ENABLED))
        self.use_proxy_var = tk.BooleanVar(value=True)
        self.player_use_proxy_var = tk.BooleanVar(value=True)
        self.max_proxy_latency = 4.0  # default max latency in seconds
        self._proxy_latencies = {}  # {proxy_str: latency_float}
        self.min_channels = 0
        self.save_folder = _get_flipper_data_dir()
        self.github_token = ""

        # Proxy testing control
        self._proxy_testing = False
        self._proxy_paused = threading.Event()
        self._proxy_paused.set()  # not paused initially
        self._proxy_stop = threading.Event()

        # Multi-proxy mode: each worker gets its own proxy
        self.multi_proxy_var = tk.BooleanVar(value=False)

        # Proxy fetching control
        self._proxy_fetching = False

        # Deep proxy testing (MAC-check based, 500 attempts)
        self._deep_proxy_testing = False
        self._deep_proxy_stop = threading.Event()
        self._deep_proxy_paused = threading.Event()
        self._deep_proxy_paused.set()

        # Rate-limit recheck timer
        self._rl_recheck_timer = None

        # MAC status in player: {mac_str: "green"|"red"}
        self.mac_status = {}

        # Account info
        self.account_info_text = ""
        self._is_closing = False

        self._setup_styles()
        self._build_gui()

        # If user enabled debug mode, allocate console (Windows) so logs go somewhere.
        if sys.platform == "win32" and self.debug_console_var.get():
            _enable_windows_console()
            _debug_print("[Flipper] Debug console enabled (App init).")

            # Show full tracebacks from Tkinter callbacks in console
            def _tk_report_callback_exception(exc, val, tb):
                try:
                    formatted = "".join(traceback.format_exception(exc, val, tb))
                    _debug_print("[Flipper] Tkinter callback exception:\n" + formatted)
                except Exception:
                    pass

            try:
                self.root.report_callback_exception = _tk_report_callback_exception
            except Exception:
                pass

            # Python 3.8+: show exceptions from threads
            try:
                import threading as _threading

                if hasattr(_threading, "excepthook"):

                    def _thread_excepthook(args):
                        try:
                            formatted = "".join(
                                traceback.format_exception(
                                    args.exc_type, args.exc_value, args.exc_traceback
                                )
                            )
                            _debug_print("[Flipper] Thread exception:\n" + formatted)
                        except Exception:
                            pass

                    _threading.excepthook = _thread_excepthook
            except Exception:
                pass

        # Log MPV diagnostics on Windows
        if sys.platform == "win32" and not HAS_MPV:
            diag = _diagnose_mpv_availability()
            for line in diag.split("\n"):
                self._log(line, "dim")
            if MPV_IMPORT_ERROR:
                self._log(f"Import mpv error: {MPV_IMPORT_ERROR}", "error")

        self._load_session()
        self._auto_fetch_proxies_on_startup()

        # Check for updates on startup (background, silent)
        if sys.platform == "win32":
            threading.Thread(target=self._check_version_on_startup, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Timeout helper ─────────────────────────────────────
    def _get_timeout(self):
        try:
            return int(self.timeout_entry.get().strip())
        except (ValueError, AttributeError):
            return 5

    # ── Styles ─────────────────────────────────────────────
    def _setup_styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Treeview",
            background="#1e1e3a",
            foreground="#d0d0e8",
            fieldbackground="#1e1e3a",
            rowheight=26,
            font=("Menlo", 11),
        )
        style.configure(
            "Treeview.Heading",
            background="#2a2a4a",
            foreground="#ffffff",
            font=("Menlo", 11, "bold"),
        )
        style.map("Treeview", background=[("selected", ACCENT)])
        style.configure(
            "green.Horizontal.TProgressbar",
            troughcolor="#1e1e3a",
            background="#00b359",
            darkcolor="#009945",
            lightcolor="#00ff88",
            bordercolor="#333355",
        )

    # ══════════════════════════════════════════════════════
    #  BUILD GUI
    # ══════════════════════════════════════════════════════

    def _build_gui(self):
        # LEFT sidebar container
        self.left = tk.Frame(
            self.root, width=270, bg=BG_SIDEBAR, highlightthickness=0, bd=0
        )
        self.left.pack(side=tk.LEFT, fill=tk.Y)
        self.left.pack_propagate(False)

        # Two sidebar modes
        self.sidebar_scanner = tk.Frame(self.left, bg=BG_SIDEBAR)
        self.sidebar_scanner.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.sidebar_player = tk.Frame(self.left, bg=BG_SIDEBAR)
        self.sidebar_player.place(relx=0, rely=0, relwidth=1, relheight=1)

        self._build_sidebar_scanner(self.sidebar_scanner)
        self._build_sidebar_player(self.sidebar_player)

        # RIGHT main area
        right = tk.Frame(self.root, bg="#0f0f23")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tab bar
        tab_bar = tk.Frame(right, bg=BG_BAR)
        tab_bar.pack(fill=tk.X)

        self.tab_btns = []
        self.tab_pages = []
        tab_labels = [
            "📋 Logi",
            "✅ Aktywne MAC",
            "🌐 Proxy",
            "📺 Player",
            "👤 Profile",
            "ℹ️ Info",
            "⚙️ Ustawienia",
        ]
        for i, label in enumerate(tab_labels):
            b = self._make_btn(
                tab_bar,
                label,
                "#333355",
                "#444466",
                lambda idx=i: self._switch_tab(idx),
            )
            b.pack(
                side=tk.LEFT, padx=(10 if i == 0 else 3, 3), pady=5, ipady=3, ipadx=8
            )
            self.tab_btns.append(b)

        # Pages container
        self.pages_frame = tk.Frame(right, bg="#0f0f23")
        self.pages_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self._build_page_logs(self.pages_frame)
        self._build_page_active(self.pages_frame)
        self._build_page_proxy(self.pages_frame)
        self._build_page_player(self.pages_frame)
        self._build_page_profiles(self.pages_frame)
        self._build_page_info(self.pages_frame)
        self._build_page_settings(self.pages_frame)

        # Progress bar at bottom
        progress_frame = tk.Frame(right, bg=BG_BAR, height=28)
        progress_frame.pack(fill=tk.X, side=tk.BOTTOM)
        progress_frame.pack_propagate(False)

        self.progress_bar = ttk.Progressbar(
            progress_frame,
            orient=tk.HORIZONTAL,
            mode="determinate",
            style="green.Horizontal.TProgressbar",
            maximum=100,
        )
        self.progress_bar.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 4), pady=4
        )

        self.progress_label = tk.Label(
            progress_frame,
            text="Gotowy",
            font=("Helvetica", 10),
            bg=BG_BAR,
            fg=FG_DIM,
            anchor=tk.W,
        )
        self.progress_label.pack(side=tk.LEFT, padx=(0, 10))

        self._switch_tab(0)

    # ── Sidebar: Scanner ───────────────────────────────────
    def _build_sidebar_scanner(self, left):
        tk.Label(
            left,
            text="⚡ FLIPPER",
            font=("Helvetica", 22, "bold"),
            bg=BG_SIDEBAR,
            fg="#00d4ff",
        ).pack(pady=(14, 1))
        tk.Label(
            left,
            text="MAC Address Scanner",
            font=("Helvetica", 10),
            bg=BG_SIDEBAR,
            fg=FG_DIM,
        ).pack(pady=(0, 8))
        self._sep(left)

        self._lbl(left, "URL serwera")
        self.url_entry = self._entry(left)

        self._lbl(left, "Pierwsze 3 bajty MAC")
        self.mac_entry = self._entry(left, "00:1B:79")

        self._lbl(left, "Proxy (wpisz aby nadpisać auto-proxy)")
        self.proxy_inline_entry = self._entry(left)

        self._lbl(left, "Ilość procesów")
        self.workers_entry = self._entry(left, "10")

        self._lbl(left, "Timeout (s)")
        self.timeout_entry = self._entry(left, "5")

        # Checkboxes row
        cb_frame = tk.Frame(left, bg=BG_SIDEBAR)
        cb_frame.pack(fill=tk.X, padx=16, pady=(2, 4))

        self.save_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            cb_frame,
            text="Zapisuj",
            variable=self.save_var,
            bg=BG_SIDEBAR,
            fg="#aaaaaa",
            selectcolor=BG_INPUT,
            activebackground=BG_SIDEBAR,
            activeforeground="#cccccc",
            font=("Helvetica", 10),
        ).pack(side=tk.LEFT)

        tk.Checkbutton(
            cb_frame,
            text="Na wierzchu",
            variable=self.keep_on_top_var,
            bg=BG_SIDEBAR,
            fg="#aaaaaa",
            selectcolor=BG_INPUT,
            activebackground=BG_SIDEBAR,
            activeforeground="#cccccc",
            font=("Helvetica", 10),
            command=self._toggle_keep_on_top,
        ).pack(side=tk.LEFT, padx=(6, 0))

        # Proxy info label (scanning is always via proxy)
        tk.Label(
            cb_frame,
            text="🔒 Proxy",
            font=("Helvetica", 10, "bold"),
            bg=BG_SIDEBAR,
            fg="#55aaff",
        ).pack(side=tk.LEFT, padx=(6, 0))

        # Min channels filter
        min_ch_frame = tk.Frame(left, bg=BG_SIDEBAR)
        min_ch_frame.pack(fill=tk.X, padx=16, pady=(0, 4))
        tk.Label(
            min_ch_frame,
            text="Min. kanałów:",
            font=("Helvetica", 10, "bold"),
            bg=BG_SIDEBAR,
            fg="#c8c8e0",
        ).pack(side=tk.LEFT)
        self.min_channels_entry = tk.Entry(
            min_ch_frame,
            font=("Helvetica", 10),
            width=6,
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        self.min_channels_entry.pack(side=tk.LEFT, padx=(4, 0), ipady=2)
        self.min_channels_entry.insert(0, "0")

        # Export button
        self._make_btn(
            left, "📁 Eksportuj wyniki", "#333355", "#444466", self._export_results
        ).pack(fill=tk.X, padx=16, pady=(2, 6), ipady=2)

        self._sep(left)

        self.start_btn = self._make_btn(
            left, "▶  START", "#00b359", "#009945", self._toggle_start
        )
        self.start_btn.pack(fill=tk.X, padx=16, pady=(4, 4), ipady=6)

        ps = tk.Frame(left, bg=BG_SIDEBAR)
        ps.pack(fill=tk.X, padx=16, pady=(0, 6))
        self.pause_btn = self._make_btn(
            ps, "⏸ PAUZA", "#c78d00", "#a87600", self._toggle_pause
        )
        self.pause_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3), ipady=4)
        self._btn_disable(self.pause_btn)

        self.stop_btn = self._make_btn(
            ps, "⏹ STOP", "#cc3333", "#aa2222", self._stop_scan
        )
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0), ipady=4)
        self._btn_disable(self.stop_btn)

        # Rotate proxy button
        self.rotate_proxy_btn = self._make_btn(
            left, "🔄 Zmień proxy", "#6d28d9", "#5b21b6", self._rotate_proxy_manual
        )
        self.rotate_proxy_btn.pack(fill=tk.X, padx=16, pady=(2, 2), ipady=3)

        # Multi-proxy checkbox
        tk.Checkbutton(
            left,
            text="Multi-proxy (wiele proxy naraz)",
            variable=self.multi_proxy_var,
            font=("Helvetica", 10),
            anchor=tk.W,
            bg=BG_SIDEBAR,
            fg="#c8c8e0",
            selectcolor=BG_INPUT,
            activebackground=BG_SIDEBAR,
            activeforeground="#c8c8e0",
        ).pack(fill=tk.X, padx=18, pady=(0, 4))

        self._sep(left)
        self.stat_checked = tk.Label(
            left,
            text="Sprawdzono:  0",
            font=("Helvetica", 12),
            anchor=tk.W,
            bg=BG_SIDEBAR,
            fg="#aaaaaa",
        )
        self.stat_checked.pack(fill=tk.X, padx=18, pady=(4, 0))
        self.stat_found = tk.Label(
            left,
            text="Znaleziono:    0",
            font=("Helvetica", 12),
            anchor=tk.W,
            bg=BG_SIDEBAR,
            fg="#00ff88",
        )
        self.stat_found.pack(fill=tk.X, padx=18)
        self.stat_status = tk.Label(
            left,
            text="Status: Bezczynny",
            font=("Helvetica", 11),
            anchor=tk.W,
            bg=BG_SIDEBAR,
            fg="#666666",
        )
        self.stat_status.pack(fill=tk.X, padx=18, pady=(4, 0))

    # ── Sidebar: Player (only MACs + Profiles) ────────────
    def _build_sidebar_player(self, left):
        tk.Label(
            left,
            text="📺 PLAYER",
            font=("Helvetica", 22, "bold"),
            bg=BG_SIDEBAR,
            fg="#00d4ff",
        ).pack(pady=(14, 1))
        self._sep(left)

        self.active_profile_label = tk.Label(
            left,
            text="Aktywny: (brak)",
            font=("Helvetica", 11, "bold"),
            bg=BG_SIDEBAR,
            fg="#ffaa00",
            anchor=tk.W,
            wraplength=240,
        )
        self.active_profile_label.pack(fill=tk.X, padx=14, pady=(2, 4))
        self._sep(left)

        # Sub-tab buttons
        sub_frame = tk.Frame(left, bg=BG_SIDEBAR)
        sub_frame.pack(fill=tk.X, padx=10, pady=(0, 4))

        self.player_sub_btns = []
        self.player_sub_pages = []

        b_macs = self._make_btn(
            sub_frame, "MAC-i", ACCENT, "#1d4ed8", lambda: self._switch_player_sub(0)
        )
        b_macs.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2), ipady=2)
        self.player_sub_btns.append(b_macs)

        b_prof = self._make_btn(
            sub_frame,
            "Profile",
            "#333355",
            "#444466",
            lambda: self._switch_player_sub(1),
        )
        b_prof.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0), ipady=2)
        self.player_sub_btns.append(b_prof)

        # Sub-page container
        sub_container = tk.Frame(left, bg=BG_SIDEBAR)
        sub_container.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))

        # -- Sub-page 0: MAC list (only MAC addresses, no URL) --
        sp0 = tk.Frame(sub_container, bg=BG_SIDEBAR)
        sp0.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.player_sub_pages.append(sp0)

        self.player_mac_listbox = tk.Listbox(
            sp0,
            font=("Menlo", 10),
            bg=BG_INPUT,
            fg="#d0d0e8",
            selectbackground=ACCENT,
            selectforeground="white",
            relief="flat",
            bd=2,
            highlightthickness=0,
        )
        mac_sb = tk.Scrollbar(sp0, command=self.player_mac_listbox.yview)
        self.player_mac_listbox.configure(yscrollcommand=mac_sb.set)
        mac_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.player_mac_listbox.pack(fill=tk.BOTH, expand=True)
        self.player_mac_listbox.bind("<<ListboxSelect>>", self._on_player_mac_select)

        # -- Sub-page 1: Profile list --
        sp1 = tk.Frame(sub_container, bg=BG_SIDEBAR)
        sp1.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.player_sub_pages.append(sp1)

        self.player_profile_listbox = tk.Listbox(
            sp1,
            font=("Menlo", 10),
            bg=BG_INPUT,
            fg="#d0d0e8",
            selectbackground=ACCENT,
            selectforeground="white",
            relief="flat",
            bd=2,
            highlightthickness=0,
        )
        prof_sb = tk.Scrollbar(sp1, command=self.player_profile_listbox.yview)
        self.player_profile_listbox.configure(yscrollcommand=prof_sb.set)
        prof_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.player_profile_listbox.pack(fill=tk.BOTH, expand=True)
        self.player_profile_listbox.bind(
            "<<ListboxSelect>>", self._on_player_profile_select
        )

        self._switch_player_sub(0)

        # Bottom buttons
        bot = tk.Frame(left, bg=BG_SIDEBAR)
        bot.pack(fill=tk.X, padx=10, pady=(0, 6))
        self._make_btn(
            bot, "🗑 Usuń MAC", "#cc3333", "#aa2222", self._delete_selected_player_mac
        ).pack(fill=tk.X, ipady=3, pady=(2, 2))
        self._make_btn(
            bot,
            "✏️ Edytuj profil",
            "#c78d00",
            "#a87600",
            self._edit_selected_player_profile,
        ).pack(fill=tk.X, ipady=3, pady=(0, 2))
        self._make_btn(
            bot,
            "🗑 Usuń profil",
            "#cc3333",
            "#aa2222",
            self._delete_selected_player_profile,
        ).pack(fill=tk.X, ipady=3, pady=(0, 2))

    # ── Page 0: Logs ──────────────────────────────────────
    def _build_page_logs(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        self.log_text = tk.Text(
            page,
            font=("Menlo", 11),
            bg=BG_DARK,
            fg="#c8c8e0",
            wrap=tk.WORD,
            state=tk.DISABLED,
            relief="flat",
            bd=4,
            insertbackground="#ffffff",
        )
        sb = tk.Scrollbar(page, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for tag, color in [
            ("success", "#00ff88"),
            ("error", "#ff4444"),
            ("info", "#55aaff"),
            ("warning", "#ffaa00"),
            ("dim", "#555577"),
        ]:
            self.log_text.tag_config(tag, foreground=color)

    # ── Page 1: Active MACs (with search) ───────────────
    def _build_page_active(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        search_frame = tk.Frame(page, bg=BG_DARK)
        search_frame.pack(fill=tk.X, padx=4, pady=(4, 2))
        tk.Label(
            search_frame, text="🔍", font=("Helvetica", 12), bg=BG_DARK, fg="#aaaaaa"
        ).pack(side=tk.LEFT, padx=(4, 2))
        self.mac_search_var = tk.StringVar()
        self.mac_search_entry = tk.Entry(
            search_frame,
            textvariable=self.mac_search_var,
            font=("Helvetica", 11),
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        self.mac_search_entry.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4), ipady=3
        )
        self.mac_search_var.trace_add("write", self._filter_active_macs)

        # Add MAC manually row
        add_mac_frame = tk.Frame(page, bg=BG_DARK)
        add_mac_frame.pack(fill=tk.X, padx=4, pady=(2, 2))

        tk.Label(
            add_mac_frame,
            text="MAC:",
            font=("Helvetica", 10, "bold"),
            bg=BG_DARK,
            fg="#c8c8e0",
        ).pack(side=tk.LEFT, padx=(4, 2))
        self.add_mac_entry = tk.Entry(
            add_mac_frame,
            font=("Helvetica", 11),
            width=20,
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        self.add_mac_entry.pack(side=tk.LEFT, padx=(0, 4), ipady=3)
        self.add_mac_entry.insert(0, "00:1A:79:")

        tk.Label(
            add_mac_frame,
            text="URL:",
            font=("Helvetica", 10, "bold"),
            bg=BG_DARK,
            fg="#c8c8e0",
        ).pack(side=tk.LEFT, padx=(4, 2))
        self.add_mac_url_entry = tk.Entry(
            add_mac_frame,
            font=("Helvetica", 11),
            width=30,
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        self.add_mac_url_entry.pack(side=tk.LEFT, padx=(0, 4), ipady=3)

        self._make_btn(
            add_mac_frame, "➕ Dodaj", "#00b359", "#009945", self._add_mac_manually
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            add_mac_frame, "🎲 Losowy", "#6d28d9", "#5b21b6", self._add_random_mac
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

        tf = tk.Frame(page, bg=BG_DARK)
        tf.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(
            tf, columns=("url", "mac", "expiry", "channels", "proxy"), show="headings"
        )
        self.tree.heading("url", text="URL")
        self.tree.heading("mac", text="Adres MAC")
        self.tree.heading("expiry", text="Data ważności")
        self.tree.heading("channels", text="Kanały")
        self.tree.heading("proxy", text="Proxy")
        self.tree.column("url", width=220, minwidth=120)
        self.tree.column("mac", width=160, minwidth=120)
        self.tree.column("expiry", width=200, minwidth=120)
        self.tree.column("channels", width=70, minwidth=50)
        self.tree.column("proxy", width=160, minwidth=100)

        tsb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(fill=tk.BOTH, expand=True)

        bot = tk.Frame(page, bg=BG_DARK)
        bot.pack(fill=tk.X, pady=(4, 0))
        self._make_btn(
            bot, "📋 Kopiuj zaznaczony", ACCENT, "#1d4ed8", self._copy_selected_mac
        ).pack(side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)
        self._make_btn(
            bot, "📋 Kopiuj wszystkie", "#333355", "#444466", self._copy_all_macs
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            bot, "🧬 Klonuj MAC", "#6d28d9", "#5b21b6", self._clone_selected_mac
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            bot, "🗑 Usuń MAC", "#cc3333", "#aa2222", self._delete_selected_active_mac
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            bot,
            "💾 Zapisz profil",
            "#00b359",
            "#009945",
            self._save_selected_as_profile,
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            bot,
            "📂 Import MAC z pliku",
            "#6d28d9",
            "#5b21b6",
            self._import_macs_from_file,
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self.mac_count_label = tk.Label(
            bot, text="Znaleziono: 0", font=("Helvetica", 11), bg=BG_DARK, fg=FG_DIM
        )
        self.mac_count_label.pack(side=tk.RIGHT, padx=8)

    # ── Page 2: Proxy ─────────────────────────────────────
    def _build_page_proxy(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        # Row 1: Fetch + Pause + Import + Clear
        top = tk.Frame(page, bg=BG_DARK)
        top.pack(fill=tk.X, pady=(4, 2))

        self._make_btn(
            top, "🔄 Pobierz proxy", ACCENT, "#1d4ed8", self._fetch_proxies
        ).pack(side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)

        tk.Label(
            top, text="Maks:", font=("Helvetica", 10), bg=BG_DARK, fg="#aaaaaa"
        ).pack(side=tk.LEFT, padx=(2, 2))
        self.max_proxy_count_entry = tk.Entry(
            top,
            font=("Helvetica", 11),
            width=6,
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        self.max_proxy_count_entry.pack(side=tk.LEFT, padx=(0, 4), ipady=2)
        self.max_proxy_count_entry.insert(0, "500")

        # Deep test button — toggles to Pause when testing
        self.deep_test_btn = self._make_btn(
            top, "🧪 Testuj proxy", "#00b359", "#009945", self._toggle_deep_proxy_test
        )
        self.deep_test_btn.pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

        self.proxy_pause_btn = self._make_btn(
            top, "⏸ Pauza", "#c78d00", "#a87600", self._toggle_proxy_pause
        )
        self.proxy_pause_btn.pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._btn_disable(self.proxy_pause_btn)

        self._make_btn(
            top,
            "📂 Import z pliku",
            "#6d28d9",
            "#5b21b6",
            self._import_proxies_from_file,
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            top, "🗑 Wyczyść listę", "#cc3333", "#aa2222", self._clear_proxies
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

        self.proxy_count_label = tk.Label(
            top, text="Proxy: 0", font=("Helvetica", 11), bg=BG_DARK, fg=FG_DIM
        )
        self.proxy_count_label.pack(side=tk.RIGHT, padx=8)

        # Row 2: Add proxy + max latency
        row2 = tk.Frame(page, bg=BG_DARK)
        row2.pack(fill=tk.X, pady=(2, 2))

        tk.Label(
            row2, text="Dodaj:", font=("Helvetica", 11), bg=BG_DARK, fg="#aaaaaa"
        ).pack(side=tk.LEFT, padx=(4, 4))
        self.proxy_add_entry = tk.Entry(
            row2,
            font=("Helvetica", 11),
            width=24,
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        self.proxy_add_entry.pack(side=tk.LEFT, padx=(0, 4), ipady=3)
        self._make_btn(row2, "➕", "#00b359", "#009945", self._add_custom_proxy).pack(
            side=tk.LEFT, padx=(0, 8), ipady=3, ipadx=4
        )

        tk.Label(
            row2,
            text="⏱ Maks. opóźnienie (s):",
            font=("Helvetica", 10, "bold"),
            bg=BG_DARK,
            fg="#c8c8e0",
        ).pack(side=tk.LEFT, padx=(4, 4))
        self.max_latency_entry = tk.Entry(
            row2,
            font=("Helvetica", 11),
            width=5,
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        self.max_latency_entry.pack(side=tk.LEFT, padx=(0, 4), ipady=2)
        self.max_latency_entry.insert(0, str(self.max_proxy_latency))

        # Proxy test progress label
        self.proxy_test_progress_label = tk.Label(
            page, text="", font=("Helvetica", 10), bg=BG_DARK, fg="#55aaff"
        )
        self.proxy_test_progress_label.pack(fill=tk.X, padx=4)

        # Proxy Treeview with tag colors
        tf = tk.Frame(page, bg=BG_DARK)
        tf.pack(fill=tk.BOTH, expand=True)
        self.proxy_tree = ttk.Treeview(
            tf, columns=("proxy", "latency", "status"), show="headings"
        )
        self.proxy_tree.heading("proxy", text="Adres proxy")
        self.proxy_tree.heading("latency", text="Opóźnienie")
        self.proxy_tree.heading("status", text="Status")
        self.proxy_tree.column("proxy", width=350, minwidth=200)
        self.proxy_tree.column("latency", width=100, minwidth=60)
        self.proxy_tree.column("status", width=120, minwidth=80)

        # Configure tag colors for Treeview rows
        self.proxy_tree.tag_configure(
            PROXY_TAG_UNTESTED, foreground=PROXY_TAG_COLORS[PROXY_TAG_UNTESTED]
        )
        self.proxy_tree.tag_configure(
            PROXY_TAG_WORKING, foreground=PROXY_TAG_COLORS[PROXY_TAG_WORKING]
        )
        self.proxy_tree.tag_configure(
            PROXY_TAG_DEAD, foreground=PROXY_TAG_COLORS[PROXY_TAG_DEAD]
        )
        self.proxy_tree.tag_configure(
            PROXY_TAG_RATE_LIMITED, foreground=PROXY_TAG_COLORS[PROXY_TAG_RATE_LIMITED]
        )

        psb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.proxy_tree.yview)
        self.proxy_tree.configure(yscrollcommand=psb.set)
        psb.pack(side=tk.RIGHT, fill=tk.Y)
        self.proxy_tree.pack(fill=tk.BOTH, expand=True)

        bot = tk.Frame(page, bg=BG_DARK)
        bot.pack(fill=tk.X, pady=(4, 0))
        self._make_btn(
            bot, "🗑 Usuń zaznaczony", "#cc3333", "#aa2222", self._remove_selected_proxy
        ).pack(side=tk.LEFT, padx=(4, 4), ipady=3, ipadx=6)

    # ── Page 3: Player (embedded mpv + channel panel) ─────
    def _build_page_player(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        # RIGHT channel panel
        right_panel = tk.Frame(page, bg=BG_DARK, width=330)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y)
        right_panel.pack_propagate(False)

        # Content type buttons
        type_frame = tk.Frame(right_panel, bg=BG_DARK)
        type_frame.pack(fill=tk.X, padx=4, pady=(4, 2))

        self.content_type_btns = []
        for ctype, lbl in [
            ("itv", "📺 TV"),
            ("vod", "🎬 VOD"),
            ("series", "📚 Series"),
        ]:
            btn = self._make_btn(
                type_frame,
                lbl,
                ACCENT if ctype == "itv" else "#333355",
                "#1d4ed8" if ctype == "itv" else "#444466",
                lambda t=ctype: self._switch_content_type(t),
            )
            btn.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X, ipady=2)
            self.content_type_btns.append((ctype, btn))

        # Player proxy checkbox
        proxy_player_frame = tk.Frame(right_panel, bg=BG_DARK)
        proxy_player_frame.pack(fill=tk.X, padx=4, pady=(0, 2))
        tk.Checkbutton(
            proxy_player_frame,
            text="Używaj proxy w Playerze",
            variable=self.player_use_proxy_var,
            bg=BG_DARK,
            fg="#aaaaaa",
            selectcolor=BG_INPUT,
            activebackground=BG_DARK,
            activeforeground="#cccccc",
            font=("Helvetica", 10),
        ).pack(anchor=tk.W)

        # Genre dropdown
        genre_frame = tk.Frame(right_panel, bg=BG_DARK)
        genre_frame.pack(fill=tk.X, padx=4, pady=(2, 2))
        tk.Label(
            genre_frame,
            text="Kategoria:",
            font=("Helvetica", 10),
            bg=BG_DARK,
            fg="#aaaaaa",
        ).pack(side=tk.LEFT, padx=(0, 4))
        self.genre_var = tk.StringVar(value="Wszystkie")
        self.genre_menu = tk.OptionMenu(genre_frame, self.genre_var, "Wszystkie")
        self.genre_menu.configure(
            bg=BG_INPUT,
            fg="#e0e0e0",
            font=("Helvetica", 10),
            activebackground=ACCENT,
            activeforeground="white",
            highlightthickness=0,
            relief="flat",
            bd=1,
        )
        self.genre_menu["menu"].configure(
            bg=BG_INPUT,
            fg="#e0e0e0",
            font=("Helvetica", 10),
            activebackground=ACCENT,
            activeforeground="white",
        )
        self.genre_menu.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.genre_var.trace_add("write", self._on_genre_change)

        # Channel search bar
        ch_search_frame = tk.Frame(right_panel, bg=BG_DARK)
        ch_search_frame.pack(fill=tk.X, padx=4, pady=(2, 2))
        tk.Label(
            ch_search_frame, text="🔍", font=("Helvetica", 11), bg=BG_DARK, fg="#aaaaaa"
        ).pack(side=tk.LEFT, padx=(0, 2))
        self.channel_search_var = tk.StringVar()
        ch_search_entry = tk.Entry(
            ch_search_frame,
            textvariable=self.channel_search_var,
            font=("Helvetica", 10),
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        ch_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2), ipady=2)
        self.channel_search_var.trace_add("write", self._filter_channel_list)

        # Sort + Go Back buttons
        nav_frame = tk.Frame(right_panel, bg=BG_DARK)
        nav_frame.pack(fill=tk.X, padx=4, pady=(0, 2))
        self.go_back_btn = self._make_btn(
            nav_frame, "← Wróć", "#555577", "#666688", self._nav_go_back
        )
        self.go_back_btn.pack(side=tk.LEFT, padx=(0, 2), ipady=1, ipadx=4)
        self._btn_disable(self.go_back_btn)
        self._make_btn(
            nav_frame, "A→Z Sortuj", "#333355", "#444466", self._sort_channel_list
        ).pack(side=tk.LEFT, padx=2, ipady=1, ipadx=4)
        self.nav_label = tk.Label(
            nav_frame,
            text="",
            font=("Helvetica", 9),
            bg=BG_DARK,
            fg=FG_DIM,
            anchor=tk.E,
        )
        self.nav_label.pack(side=tk.RIGHT, padx=4)

        # Channel list
        ch_frame = tk.Frame(right_panel, bg=BG_DARK)
        ch_frame.pack(fill=tk.BOTH, expand=True, padx=4)

        self.channel_tree = ttk.Treeview(
            ch_frame, columns=("num", "name"), show="headings"
        )
        self.channel_tree.heading("num", text="#")
        self.channel_tree.heading("name", text="Kanał / Tytuł")
        self.channel_tree.column("num", width=45, minwidth=35)
        self.channel_tree.column("name", width=260, minwidth=120)
        ch_sb = ttk.Scrollbar(
            ch_frame, orient=tk.VERTICAL, command=self.channel_tree.yview
        )
        self.channel_tree.configure(yscrollcommand=ch_sb.set)
        ch_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.channel_tree.pack(fill=tk.BOTH, expand=True)
        self.channel_tree.bind("<Double-1>", self._on_channel_double_click)

        self.channel_count_label = tk.Label(
            right_panel, text="Kanały: 0", font=("Helvetica", 10), bg=BG_DARK, fg=FG_DIM
        )
        self.channel_count_label.pack(pady=(2, 4))

        # CENTER: embedded player + controls
        center = tk.Frame(page, bg="#000000")
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Player area
        self.player_frame = tk.Frame(center, bg="#000000")
        self.player_frame.pack(fill=tk.BOTH, expand=True)

        if not HAS_MPV:
            error_text = "mpv niedostępny.\n"
            if MPV_IMPORT_ERROR:
                error_text += f"Błąd: {MPV_IMPORT_ERROR}\n\n"
            error_text += (
                "Aplikacja próbuje instalacji automatycznej (winget).\n"
                "Jeśli nadal nie działa: zainstaluj mpv i python-mpv ręcznie."
            )
            tk.Label(
                self.player_frame,
                text=error_text,
                font=("Helvetica", 12),
                bg="#000000",
                fg="#555577",
                justify=tk.CENTER,
                wraplength=600,
            ).place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Controls bar
        controls = tk.Frame(center, bg=BG_BAR, height=46)
        controls.pack(fill=tk.X, side=tk.BOTTOM)
        controls.pack_propagate(False)

        self._make_btn(controls, "⏮", "#333355", "#444466", self._player_prev).pack(
            side=tk.LEFT, padx=(6, 2), ipady=2, ipadx=4
        )
        self.play_pause_btn = self._make_btn(
            controls, "▶", "#00b359", "#009945", self._player_play_pause
        )
        self.play_pause_btn.pack(side=tk.LEFT, padx=2, ipady=2, ipadx=6)
        self._make_btn(controls, "⏭", "#333355", "#444466", self._player_next).pack(
            side=tk.LEFT, padx=2, ipady=2, ipadx=4
        )
        self._make_btn(controls, "⏹", "#cc3333", "#aa2222", self._player_stop).pack(
            side=tk.LEFT, padx=2, ipady=2, ipadx=4
        )

        tk.Label(
            controls, text="🔊", font=("Helvetica", 12), bg=BG_BAR, fg="#aaaaaa"
        ).pack(side=tk.LEFT, padx=(12, 2))
        self.volume_scale = tk.Scale(
            controls,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            bg=BG_BAR,
            fg="#ffffff",
            troughcolor="#333355",
            highlightthickness=0,
            sliderrelief="flat",
            length=100,
            showvalue=0,
            command=self._on_volume_change,
        )
        self.volume_scale.set(80)
        self.volume_scale.pack(side=tk.LEFT, padx=2)

        self._make_btn(
            controls, "⛶ Fullscreen", "#333355", "#444466", self._player_fullscreen
        ).pack(side=tk.RIGHT, padx=(2, 6), ipady=2, ipadx=4)
        self._make_btn(
            controls, "📋 Kopiuj URL", "#333355", "#444466", self._copy_channel_url
        ).pack(side=tk.RIGHT, padx=2, ipady=2, ipadx=4)

        self.player_status_label = tk.Label(
            controls,
            text="",
            font=("Helvetica", 10),
            bg=BG_BAR,
            fg="#00ff88",
            anchor=tk.W,
        )
        self.player_status_label.pack(
            side=tk.LEFT, padx=(12, 0), fill=tk.X, expand=True
        )

    # ── Page 4: Profiles (with naming + rename) ───────────
    def _build_page_profiles(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        # Add profile form
        form = tk.Frame(page, bg=BG_DARK)
        form.pack(fill=tk.X, padx=10, pady=(10, 6))

        for lbl_text, attr in [
            ("Nazwa:", "profile_name_entry"),
            ("MAC:", "profile_mac_entry"),
            ("URL:", "profile_url_entry"),
            ("Proxy:", "profile_proxy_entry"),
        ]:
            tk.Label(
                form, text=lbl_text, font=("Helvetica", 11), bg=BG_DARK, fg="#aaaaaa"
            ).pack(side=tk.LEFT, padx=(0, 2))
            e = tk.Entry(
                form,
                font=("Helvetica", 11),
                width=16,
                bg=BG_INPUT,
                fg="#e0e0e0",
                insertbackground="#ffffff",
                relief="flat",
                highlightthickness=1,
                highlightcolor=ACCENT,
                highlightbackground="#333355",
            )
            e.pack(side=tk.LEFT, padx=(0, 8), ipady=3)
            setattr(self, attr, e)

        self._make_btn(
            form, "💾 Zapisz profil", "#00b359", "#009945", self._save_profile_from_form
        ).pack(side=tk.LEFT, padx=4, ipady=3, ipadx=6)

        # Profile list
        tf = tk.Frame(page, bg=BG_DARK)
        tf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 6))

        self.profile_tree = ttk.Treeview(
            tf, columns=("name", "mac", "url", "proxy"), show="headings"
        )
        self.profile_tree.heading("name", text="Nazwa")
        self.profile_tree.heading("mac", text="MAC")
        self.profile_tree.heading("url", text="URL")
        self.profile_tree.heading("proxy", text="Proxy")
        self.profile_tree.column("name", width=150, minwidth=80)
        self.profile_tree.column("mac", width=180, minwidth=120)
        self.profile_tree.column("url", width=250, minwidth=120)
        self.profile_tree.column("proxy", width=180, minwidth=80)

        prof_sb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.profile_tree.yview)
        self.profile_tree.configure(yscrollcommand=prof_sb.set)
        prof_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.profile_tree.pack(fill=tk.BOTH, expand=True)

        bot = tk.Frame(page, bg=BG_DARK)
        bot.pack(fill=tk.X, padx=10, pady=(0, 10))
        self._make_btn(
            bot, "✅ Ustaw aktywny", ACCENT, "#1d4ed8", self._set_active_profile
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            bot, "✏️ Zmień nazwę", "#c78d00", "#a87600", self._rename_profile
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            bot, "✏️ Edytuj profil", "#c78d00", "#a87600", self._edit_profile
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)
        self._make_btn(
            bot, "🗑 Usuń profil", "#cc3333", "#aa2222", self._delete_profile
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

    # ── Page 5: Info ──────────────────────────────────────
    def _build_page_info(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        tk.Label(
            page,
            text="ℹ️ Informacje o koncie",
            font=("Helvetica", 16, "bold"),
            bg=BG_DARK,
            fg="#00d4ff",
        ).pack(padx=14, pady=(14, 6), anchor=tk.W)

        self.info_text = tk.Text(
            page,
            font=("Menlo", 12),
            bg=BG_DARK,
            fg="#d0d0e8",
            wrap=tk.WORD,
            state=tk.DISABLED,
            relief="flat",
            bd=8,
            insertbackground="#ffffff",
        )
        info_sb = tk.Scrollbar(page, command=self.info_text.yview)
        self.info_text.configure(yscrollcommand=info_sb.set)
        info_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.info_text.pack(fill=tk.BOTH, expand=True)

        for tag, color in [
            ("label", "#55aaff"),
            ("value", "#e0e0e0"),
            ("highlight", "#00ff88"),
            ("warning", "#ffaa00"),
        ]:
            self.info_text.tag_config(tag, foreground=color)

        bot = tk.Frame(page, bg=BG_DARK)
        bot.pack(fill=tk.X, padx=10, pady=(4, 10))
        self._make_btn(
            bot, "🔄 Odśwież info", ACCENT, "#1d4ed8", self._fetch_account_info
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=3, ipadx=6)

    # ── Page 6: Settings ──────────────────────────────────
    def _build_page_settings(self, pages):
        page = tk.Frame(pages, bg=BG_DARK)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.tab_pages.append(page)

        tk.Label(
            page,
            text="⚙️ Ustawienia",
            font=("Helvetica", 16, "bold"),
            bg=BG_DARK,
            fg="#00d4ff",
        ).pack(padx=14, pady=(14, 10), anchor=tk.W)

        # Verbose logs checkbox
        cb_frame = tk.Frame(page, bg=BG_DARK)
        cb_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        tk.Checkbutton(
            cb_frame,
            text="Pokaż pełne zapytania i odpowiedzi w logach",
            variable=self.verbose_logs_var,
            bg=BG_DARK,
            fg="#d0d0e8",
            selectcolor=BG_INPUT,
            activebackground=BG_DARK,
            activeforeground="#ffffff",
            font=("Helvetica", 12),
        ).pack(anchor=tk.W)
        tk.Label(
            cb_frame,
            text="Gdy włączone, logi będą zawierać pełne URL zapytań "
            "oraz treść odpowiedzi serwera.",
            font=("Helvetica", 10),
            bg=BG_DARK,
            fg=FG_DIM,
            wraplength=600,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 0))

        # Debug console checkbox
        dbg_frame = tk.Frame(page, bg=BG_DARK)
        dbg_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        tk.Checkbutton(
            dbg_frame,
            text="Tryb debug (konsola) — pokazuj wyjątki mpv/DLL w konsoli",
            variable=self.debug_console_var,
            command=self._on_debug_console_toggle,
            bg=BG_DARK,
            fg="#d0d0e8",
            selectcolor=BG_INPUT,
            activebackground=BG_DARK,
            activeforeground="#ffffff",
            font=("Helvetica", 12),
        ).pack(anchor=tk.W)
        tk.Label(
            dbg_frame,
            text=(
                "Windows: otwiera okno konsoli i wypisuje pełne tracebacks. "
                "Włączenie może wymagać restartu, żeby złapać błędy importu mpv."
            ),
            font=("Helvetica", 10),
            bg=BG_DARK,
            fg=FG_DIM,
            wraplength=750,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 0))

        self._sep_dark(page)

        # Proxy info section (proxy-only mode)
        proxy_cb_frame = tk.Frame(page, bg=BG_DARK)
        proxy_cb_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        tk.Label(
            proxy_cb_frame,
            text="🔒 Skanowanie TYLKO przez proxy",
            bg=BG_DARK,
            fg="#55aaff",
            font=("Helvetica", 12, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            proxy_cb_frame,
            text="Skaner zawsze używa proxy. Przed skanowaniem "
            "proxy są automatycznie pobierane i testowane. "
            "Wolne proxy (powyżej ustawionego limitu opóźnienia) "
            "są automatycznie usuwane.",
            font=("Helvetica", 10),
            bg=BG_DARK,
            fg=FG_DIM,
            wraplength=600,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 0))

        self._sep_dark(page)

        # Save folder
        folder_frame = tk.Frame(page, bg=BG_DARK)
        folder_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        tk.Label(
            folder_frame,
            text="📁 Folder zapisu danych:",
            font=("Helvetica", 12, "bold"),
            bg=BG_DARK,
            fg="#d0d0e8",
        ).pack(anchor=tk.W)

        row = tk.Frame(folder_frame, bg=BG_DARK)
        row.pack(fill=tk.X, pady=(4, 0))
        self.save_folder_entry = tk.Entry(
            row,
            font=("Helvetica", 11),
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        self.save_folder_entry.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6), ipady=4
        )
        if self.save_folder:
            self.save_folder_entry.insert(0, self.save_folder)
        self._make_btn(
            row, "📂 Wybierz", ACCENT, "#1d4ed8", self._choose_save_folder
        ).pack(side=tk.LEFT, ipady=3, ipadx=6)

        tk.Label(
            folder_frame,
            text="Puste = bieżący katalog. Sesja, wyniki i eksporty "
            "będą zapisywane w wybranym folderze.",
            font=("Helvetica", 10),
            bg=BG_DARK,
            fg=FG_DIM,
            wraplength=600,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))

        self._sep_dark(page)

        # Clear channel cache button
        cache_frame = tk.Frame(page, bg=BG_DARK)
        cache_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        self._make_btn(
            cache_frame,
            "🗑 Wyczyść cache kanałów",
            "#cc3333",
            "#aa2222",
            self._clear_channels_cache,
        ).pack(anchor=tk.W, ipady=3, ipadx=6)
        tk.Label(
            cache_frame,
            text="Usuwa zapisane listy kanałów. Następnym razem "
            "kanały zostaną pobrane z serwera.",
            font=("Helvetica", 10),
            bg=BG_DARK,
            fg=FG_DIM,
            wraplength=600,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))

        self._sep_dark(page)

        # Auto-update section (Windows)
        update_frame = tk.Frame(page, bg=BG_DARK)
        update_frame.pack(fill=tk.X, padx=20, pady=(4, 6))
        form = tk.Frame(update_frame, bg=BG_DARK)
        form.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            form,
            text="GitHub token:",
            font=("Helvetica", 11, "bold"),
            bg=BG_DARK,
            fg="#d0d0e8",
        ).grid(row=0, column=0, sticky="w", pady=(6, 0))
        self.github_token_entry = tk.Entry(
            form,
            font=("Helvetica", 11),
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
            show="*",
        )
        self.github_token_entry.grid(
            row=0, column=1, sticky="we", padx=(6, 0), pady=(6, 0), ipady=2
        )
        if self.github_token:
            self.github_token_entry.insert(0, self.github_token)

        save_tok_btn = tk.Button(
            form,
            text="💾 Zapisz",
            font=("Helvetica", 10, "bold"),
            bg=ACCENT,
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            command=self._save_github_token,
        )
        save_tok_btn.grid(row=0, column=2, padx=(6, 0), pady=(6, 0), ipady=1, ipadx=4)

        self.token_status_label = tk.Label(
            form, text="", font=("Helvetica", 10), bg=BG_DARK, fg="#4ade80"
        )
        self.token_status_label.grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(2, 0)
        )

        form.grid_columnconfigure(1, weight=1)

        self._make_btn(
            update_frame,
            "⬇️ Auto aktualizacja (GitHub)",
            ACCENT,
            "#1d4ed8",
            self._auto_update_from_github,
        ).pack(anchor=tk.W, ipady=3, ipadx=6)
        tk.Label(
            update_frame,
            text=(
                "Windows: prywatny update wymaga tokena (read-only). "
                "Pobiera ZIP z GitHuba na Pulpit, rozpakowuje, uruchamia "
                "build_windows.bat i po buildzie usuwa folder źródłowy oraz ZIP."
            ),
            font=("Helvetica", 10),
            bg=BG_DARK,
            fg=FG_DIM,
            wraplength=700,
            anchor=tk.W,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))

    def _sep_dark(self, parent):
        tk.Frame(parent, height=1, bg="#333355").pack(fill=tk.X, padx=20, pady=8)

    def _choose_save_folder(self):
        folder = filedialog.askdirectory(
            title="Wybierz folder zapisu", initialdir=self.save_folder or os.getcwd()
        )
        if folder:
            self.save_folder = folder
            self.save_folder_entry.delete(0, tk.END)
            self.save_folder_entry.insert(0, folder)
            self._log(f"Folder zapisu: {folder}", "info")

    def _save_github_token(self):
        """Encrypt and persist the GitHub token immediately."""
        token = self.github_token_entry.get().strip()
        if not token:
            self.token_status_label.config(text="⚠️ Token jest pusty", fg="#facc15")
            return
        self.github_token = token
        self._save_session()
        self.token_status_label.config(
            text="✅ Klucz zaszyfrowany i zapisany", fg="#4ade80"
        )
        self._log("GitHub token zapisany (zaszyfrowany).", "success")

    # ══════════════════════════════════════════════════════
    #  CHANNEL CACHE
    # ══════════════════════════════════════════════════════

    def _channels_cache_path(self):
        return (
            os.path.join(self.save_folder, CHANNELS_CACHE_FILE)
            if self.save_folder
            else CHANNELS_CACHE_FILE
        )

    def _load_channels_cache(self):
        path = self._channels_cache_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_channels_cache(self, cache):
        try:
            with open(self._channels_cache_path(), "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _clear_channels_cache(self):
        path = self._channels_cache_path()
        if os.path.exists(path):
            os.remove(path)
        self._log("Cache kanałów wyczyszczony.", "info")

    def _check_version_on_startup(self):
        """Check version.txt on GitHub; if newer, fetch changes.txt and show update dialog."""
        import time as _time

        _time.sleep(3)  # wait for session to load and UI to settle
        try:
            token = self._get_github_token()
            if not token:
                return

            # Fetch remote version
            ver_url = (
                f"https://api.github.com/repos/{DEFAULT_UPDATE_REPO}"
                f"/contents/version.txt?ref={DEFAULT_UPDATE_BRANCH}"
            )
            req = urllib.request.Request(
                ver_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github.v3.raw",
                    "User-Agent": "Flipper-Updater",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                remote_version = resp.read().decode("utf-8").strip()

            local_version = APP_VERSION
            if not remote_version or remote_version == local_version:
                self._log_safe(f"Wersja aktualna: {local_version}", "dim")
                return

            # Fetch changes.txt
            changes_text = ""
            try:
                changes_url = (
                    f"https://api.github.com/repos/{DEFAULT_UPDATE_REPO}"
                    f"/contents/changes.txt?ref={DEFAULT_UPDATE_BRANCH}"
                )
                req2 = urllib.request.Request(
                    changes_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github.v3.raw",
                        "User-Agent": "Flipper-Updater",
                    },
                )
                with urllib.request.urlopen(req2, timeout=10) as resp2:
                    changes_text = resp2.read().decode("utf-8").strip()
            except Exception:
                changes_text = "(Nie udało się pobrać listy zmian)"

            self._log_safe(
                f"Nowa wersja dostępna: {remote_version} (obecna: {local_version})",
                "warning",
            )
            # Show update dialog on UI thread
            self.root.after(
                0, self._show_update_dialog, local_version, remote_version, changes_text
            )
        except Exception:
            pass

    def _show_update_dialog(self, local_ver, remote_ver, changes_text):
        """Show a modal dialog asking the user to update."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Dostępna aktualizacja")
        dialog.configure(bg=BG_DARK)
        dialog.geometry("520x420")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # Center on parent
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - 520) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - 420) // 2
        dialog.geometry(f"+{x}+{y}")

        tk.Label(
            dialog,
            text="🔄 Nowa wersja Flipper!",
            font=("Helvetica", 16, "bold"),
            bg=BG_DARK,
            fg="#00d4ff",
        ).pack(pady=(16, 4))

        tk.Label(
            dialog,
            text=f"Obecna: {local_ver}  →  Nowa: {remote_ver}",
            font=("Helvetica", 12),
            bg=BG_DARK,
            fg="#aaaaaa",
        ).pack(pady=(0, 8))

        tk.Label(
            dialog,
            text="Co nowego:",
            font=("Helvetica", 11, "bold"),
            bg=BG_DARK,
            fg="#c8c8e0",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=20, pady=(4, 2))

        # Changes text area
        changes_frame = tk.Frame(dialog, bg=BG_DARK)
        changes_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 8))

        changes_box = tk.Text(
            changes_frame,
            font=("Helvetica", 10),
            bg=BG_INPUT,
            fg="#d0d0e8",
            wrap=tk.WORD,
            relief="flat",
            bd=2,
            highlightthickness=0,
            padx=8,
            pady=6,
        )
        ch_sb = tk.Scrollbar(changes_frame, command=changes_box.yview)
        changes_box.configure(yscrollcommand=ch_sb.set)
        ch_sb.pack(side=tk.RIGHT, fill=tk.Y)
        changes_box.pack(fill=tk.BOTH, expand=True)
        changes_box.insert("1.0", changes_text or "(brak informacji)")
        changes_box.configure(state=tk.DISABLED)

        # Buttons
        btn_frame = tk.Frame(dialog, bg=BG_DARK)
        btn_frame.pack(fill=tk.X, padx=20, pady=(0, 16))

        def _do_update():
            dialog.destroy()
            self._auto_update_from_github()

        def _skip():
            dialog.destroy()
            self._log("Aktualizacja pominięta.", "dim")

        self._make_btn(
            btn_frame, "✅ Aktualizuj", "#00b359", "#009945", _do_update
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4), ipady=6)
        self._make_btn(btn_frame, "❌ Pomiń", "#cc3333", "#aa2222", _skip).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0), ipady=6
        )

    def _get_github_token(self) -> str:
        """Get GitHub token: prefer entry widget, fallback to self.github_token."""
        token = ""
        if hasattr(self, "github_token_entry"):
            try:
                token = self.github_token_entry.get().strip()
            except Exception:
                pass
        if not token:
            token = self.github_token or ""
        return token

    def _auto_update_from_github(self):
        if sys.platform != "win32":
            self._log("Auto aktualizacja jest dostępna tylko na Windows.", "warning")
            return
        token = self._get_github_token()
        if not token:
            self._log("To repo prywatne: podaj GitHub token (read-only).", "error")
            return
        self._log("Start auto aktualizacji z GitHuba...", "info")
        self._set_progress(5, "Auto aktualizacja...")
        threading.Thread(target=self._auto_update_worker, daemon=True).start()

    def _get_update_zip_url(self) -> str:
        return f"https://api.github.com/repos/{DEFAULT_UPDATE_REPO}/zipball/{DEFAULT_UPDATE_BRANCH}"

    def _auto_update_worker(self):
        desktop = Path.home() / "Desktop"
        zip_path = desktop / "flipper-main.zip"
        runner_bat = desktop / "flipper_update_runner.bat"
        extract_dir = None

        try:
            self._set_progress(15, "Pobieranie ZIP...")
            zip_url = self._get_update_zip_url()
            token = self._get_github_token()
            self.github_token = token
            self._log_safe(f"Pobieram: {zip_url}", "info")

            if zip_path.exists():
                zip_path.unlink(missing_ok=True)

            req = urllib.request.Request(
                zip_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Flipper-Updater",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(zip_path, "wb") as out:
                    out.write(resp.read())

            # Hide ZIP on Windows
            if sys.platform == "win32":
                try:
                    subprocess.run(
                        ["attrib", "+h", str(zip_path)],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        timeout=5,
                    )
                except Exception:
                    pass

            self._set_progress(35, "Rozpakowywanie ZIP...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                top_dirs = []
                for member in zf.namelist():
                    parts = member.split("/")
                    if parts and parts[0]:
                        top_dirs.append(parts[0])
                zf.extractall(desktop)

            if top_dirs:
                extract_dir = desktop / top_dirs[0]
                # Hide extracted folder on Windows
                if sys.platform == "win32":
                    try:
                        subprocess.run(
                            ["attrib", "+h", str(extract_dir)],
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            timeout=5,
                        )
                    except Exception:
                        pass
            else:
                self._log_safe("Błąd paczki ZIP: brak katalogu źródłowego.", "error")
                self._set_progress(100, "Błąd aktualizacji")
                return

            build_bat = extract_dir / "build_windows.bat"
            if not build_bat.exists():
                self._log_safe("Brak build_windows.bat w pobranej paczce.", "error")
                self._set_progress(100, "Błąd aktualizacji")
                return

            self._set_progress(55, "Przygotowanie build runner...")
            runner_content = (
                "@echo off\n"
                "setlocal\n"
                f'cd /d "{extract_dir}"\n'
                "call build_windows.bat\n"
                f'cd /d "{desktop}"\n'
                f'attrib -h "{extract_dir}" >nul 2>nul\n'
                f'rmdir /s /q "{extract_dir}"\n'
                f'attrib -h "{zip_path}" >nul 2>nul\n'
                f'del /f /q "{zip_path}"\n'
                "endlocal\n"
                '(goto) 2>nul & del /q "%~f0"\n'
            )
            with open(runner_bat, "w", encoding="utf-8") as f:
                f.write(runner_content)

            self._set_progress(75, "Uruchamianie build_windows.bat...")
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(runner_bat)],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )

            self._log_safe(
                "Auto aktualizacja uruchomiona. Zamykam stare okno...",
                "success",
            )
            self._set_progress(100, "Aktualizacja uruchomiona")
            self.root.after(500, self.root.destroy)

        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                self._log_safe(
                    "GitHub auth failed (401/403). Sprawdź token i uprawnienia.",
                    "error",
                )
            elif e.code == 404:
                self._log_safe(
                    "Repo/branch nie istnieje lub brak dostępu (404).", "error"
                )
            else:
                self._log_safe(f"HTTP error podczas aktualizacji: {e.code}", "error")
            self._set_progress(100, "Błąd aktualizacji")
        except Exception as e:
            self._log_safe(f"Błąd auto aktualizacji: {e}", "error")
            self._set_progress(100, "Błąd aktualizacji")

    # ══════════════════════════════════════════════════════
    #  WIDGET HELPERS
    # ══════════════════════════════════════════════════════

    def _entry(self, parent, default=""):
        e = tk.Entry(
            parent,
            font=("Helvetica", 11),
            bg=BG_INPUT,
            fg="#e0e0e0",
            insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightcolor=ACCENT,
            highlightbackground="#333355",
        )
        e.pack(fill=tk.X, padx=16, pady=(2, 6), ipady=4)
        if default:
            e.insert(0, default)
        return e

    def _lbl(self, parent, text):
        tk.Label(
            parent,
            text=text,
            font=("Helvetica", 11, "bold"),
            bg=BG_SIDEBAR,
            fg="#c8c8e0",
            anchor=tk.W,
        ).pack(fill=tk.X, padx=18, pady=(2, 0))

    def _sep(self, parent):
        tk.Frame(parent, height=1, bg="#333355").pack(fill=tk.X, padx=14, pady=6)

    def _make_btn(self, parent, text, bg_color, hover_color, command):
        lbl = tk.Label(
            parent,
            text=text,
            font=("Helvetica", 11, "bold"),
            bg=bg_color,
            fg="white",
            cursor="hand2",
            anchor=tk.CENTER,
            padx=6,
            pady=2,
        )
        lbl._normal_bg = bg_color
        lbl._hover_bg = hover_color
        lbl._command = command
        lbl._enabled = True
        lbl.bind("<Button-1>", lambda e: lbl._command() if lbl._enabled else None)
        lbl.bind(
            "<Enter>",
            lambda e: lbl.configure(bg=lbl._hover_bg) if lbl._enabled else None,
        )
        lbl.bind(
            "<Leave>",
            lambda e: lbl.configure(bg=lbl._normal_bg) if lbl._enabled else None,
        )
        return lbl

    def _btn_enable(self, btn):
        btn._enabled = True
        btn.configure(bg=btn._normal_bg, fg="white", cursor="hand2")

    def _btn_disable(self, btn):
        btn._enabled = False
        btn.configure(bg="#444444", fg="#888888", cursor="arrow")

    def _switch_tab(self, idx):
        self.current_tab = idx
        for i, (btn, pg) in enumerate(zip(self.tab_btns, self.tab_pages)):
            if i == idx:
                btn._normal_bg = ACCENT
                btn.configure(bg=ACCENT)
                pg.lift()
            else:
                btn._normal_bg = "#333355"
                btn.configure(bg="#333355")
        if idx == 3:
            self.sidebar_player.lift()
            self._refresh_player_mac_list()
            self._refresh_player_profile_list()
        else:
            self.sidebar_scanner.lift()

    def _switch_player_sub(self, idx):
        for i, (btn, pg) in enumerate(zip(self.player_sub_btns, self.player_sub_pages)):
            if i == idx:
                btn._normal_bg = ACCENT
                btn.configure(bg=ACCENT)
                pg.lift()
            else:
                btn._normal_bg = "#333355"
                btn.configure(bg="#333355")

    # ══════════════════════════════════════════════════════
    #  PROGRESS BAR
    # ══════════════════════════════════════════════════════

    def _set_progress(self, value, text=""):
        self.root.after(0, self._do_set_progress, value, text)

    def _do_set_progress(self, value, text):
        self.progress_bar["value"] = min(max(value, 0), 100)
        if text:
            self.progress_label.configure(text=text)

    # ══════════════════════════════════════════════════════
    #  KEEP ON TOP
    # ══════════════════════════════════════════════════════

    def _toggle_keep_on_top(self):
        self.root.attributes("-topmost", self.keep_on_top_var.get())

    # ══════════════════════════════════════════════════════
    #  LOGGING
    # ══════════════════════════════════════════════════════

    def _log(self, message, tag="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{ts}] {message}"
        self.log_history.append((full_msg, tag))
        if len(self.log_history) > MAX_LOG_SAVE:
            self.log_history = self.log_history[-MAX_LOG_SAVE:]
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{ts}] ", "dim")
        self.log_text.insert(tk.END, f"{message}\n", tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

        # Optional console output (debug mode)
        if sys.platform == "win32" and (
            self.debug_console_var.get() or _DEBUG_CONSOLE_ENABLED
        ):
            try:
                print(full_msg, flush=True)
            except Exception:
                pass

    def _on_debug_console_toggle(self):
        enabled = bool(self.debug_console_var.get())
        if sys.platform == "win32" and enabled:
            _enable_windows_console()
            _debug_print("[Flipper] Debug console enabled from Settings.")
        self._log(f"Debug (konsola): {'ON' if enabled else 'OFF'}", "warning")
        if enabled and sys.platform == "win32" and not HAS_MPV:
            # Re-run diagnostics into UI log (and console, because debug is enabled)
            try:
                diag = _diagnose_mpv_availability()
                for line in diag.split("\n"):
                    self._log(line, "dim")
                if MPV_IMPORT_ERROR:
                    self._log("Import mpv error (traceback):", "error")
                    for line in str(MPV_IMPORT_ERROR).split("\n"):
                        self._log(line, "error")
            except Exception:
                self._log("Nie udało się uruchomić diagnostyki MPV.", "error")

    def _log_safe(self, message, tag="info"):
        self.root.after(0, self._log, message, tag)

    # ══════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════

    def _update_stats(self):
        self.stat_checked.configure(text=f"Sprawdzono:  {self.checked_count}")
        self.stat_found.configure(text=f"Znaleziono:    {self.found_count}")

    def _update_stats_safe(self):
        self.root.after(0, self._update_stats)

    def _set_status(self, text, color="#666666"):
        self.root.after(
            0, lambda: self.stat_status.configure(text=f"Status: {text}", fg=color)
        )

    # ══════════════════════════════════════════════════════
    #  ACTIVE MAC MANAGEMENT
    # ══════════════════════════════════════════════════════

    def _filter_active_macs(self, *args):
        query = self.mac_search_var.get().strip().lower()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for m in self.active_macs:
            if query:
                haystack = (
                    f"{m['url']} {m['mac']} {m['expiry']} {m.get('proxy', '')}".lower()
                )
                if query not in haystack:
                    continue
            self.tree.insert(
                "",
                tk.END,
                values=(
                    m["url"],
                    m["mac"],
                    m["expiry"],
                    m.get("channels", "?"),
                    m.get("proxy", ""),
                ),
            )

    def _add_active_mac(self, url, mac, expiry, proxy=None, channels=0):
        entry = {
            "url": url,
            "mac": mac,
            "expiry": expiry,
            "proxy": proxy or "",
            "channels": channels,
        }
        self.active_macs.append(entry)
        if proxy:
            self.mac_proxy_map[mac] = proxy
        self.root.after(0, self._insert_mac_row, entry)

    def _insert_mac_row(self, entry):
        self.tree.insert(
            "",
            tk.END,
            values=(
                entry["url"],
                entry["mac"],
                entry["expiry"],
                entry.get("channels", "?"),
                entry["proxy"],
            ),
        )
        self.mac_count_label.configure(text=f"Znaleziono: {len(self.active_macs)}")

    def _copy_selected_mac(self):
        sel = self.tree.selection()
        if not sel:
            self._log("Nie zaznaczono wiersza.", "warning")
            return
        mac = self.tree.item(sel[0], "values")[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(mac)
        self._log(f"Skopiowano MAC: {mac}", "info")

    def _copy_all_macs(self):
        if not self.active_macs:
            self._log("Brak aktywnych MAC.", "warning")
            return
        text = "\n".join(
            f"{m['mac']} | {m['expiry']} | {m['url']}" for m in self.active_macs
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._log(f"Skopiowano {len(self.active_macs)} MAC.", "info")

    def _delete_selected_active_mac(self):
        sel = self.tree.selection()
        if not sel:
            self._log("Zaznacz MAC do usunięcia.", "warning")
            return
        vals = self.tree.item(sel[0], "values")
        if len(vals) < 2:
            return
        url = vals[0]
        mac = vals[1]

        before = len(self.active_macs)
        self.active_macs = [
            m
            for m in self.active_macs
            if not (m.get("mac") == mac and m.get("url") == url)
        ]
        after = len(self.active_macs)
        if after == before:
            self._log("Nie znaleziono rekordu do usunięcia.", "warning")
            return

        self.mac_proxy_map.pop(mac, None)
        self.tree.delete(sel[0])
        self.mac_count_label.configure(text=f"Znaleziono: {after}")
        self._refresh_player_mac_list()
        self._auto_save()
        self._log(f"Usunięto MAC: {mac}", "info")

    def _clone_selected_mac(self):
        sel = self.tree.selection()
        if not sel:
            self._log("Zaznacz MAC do sklonowania.", "warning")
            return
        mac = self.tree.item(sel[0], "values")[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(mac)
        self._log(f"🧬 Sklonowano MAC: {mac}", "success")

    def _save_selected_as_profile(self):
        """Save selected MAC as profile — ask for name via dialog."""
        sel = self.tree.selection()
        if not sel:
            self._log("Zaznacz MAC do zapisania jako profil.", "warning")
            return
        vals = self.tree.item(sel[0], "values")
        url, mac, expiry, proxy = vals[0], vals[1], vals[2], vals[3]

        name = simpledialog.askstring(
            "Nazwa profilu",
            "Podaj nazwę dla profilu:",
            initialvalue=f"Profil {len(self.profiles) + 1}",
            parent=self.root,
        )
        if not name:
            return
        self.profiles.append({"name": name, "mac": mac, "url": url, "proxy": proxy})
        self._refresh_profile_tree()
        self._log(f"Zapisano profil: {name} ({mac})", "success")

    # ══════════════════════════════════════════════════════
    #  EXPORT
    # ══════════════════════════════════════════════════════

    def _export_results(self):
        if not self.active_macs:
            self._log("Brak wyników do eksportu.", "warning")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Tekst", "*.txt"), ("CSV", "*.csv"), ("Wszystkie", "*.*")],
            initialfile="flipper_results.txt",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Flipper — wyniki skanowania\n")
            f.write(f"# {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for m in self.active_macs:
                f.write(
                    f"{m['mac']} | {m['expiry']} | {m['url']} | "
                    f"ch={m.get('channels', '?')} | "
                    f"{m.get('proxy', '')}\n"
                )
        self._log(f"Wyeksportowano {len(self.active_macs)} wyników.", "success")

    def _auto_save(self):
        if not self.save_var.get() or not self.active_macs:
            return
        try:
            save_path = (
                os.path.join(self.save_folder, RESULTS_FILE)
                if self.save_folder
                else RESULTS_FILE
            )
            with open(save_path, "w", encoding="utf-8") as f:
                f.write("# Flipper — auto-zapis\n")
                for m in self.active_macs:
                    f.write(
                        f"{m['mac']} | {m['expiry']} | {m['url']} | "
                        f"ch={m.get('channels', '?')} | "
                        f"{m.get('proxy', '')}\n"
                    )
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  SESSION PERSISTENCE
    # ══════════════════════════════════════════════════════

    def _save_session(self):
        token_plain = self._get_github_token()
        # Always save to canonical data dir to avoid split-brain
        canonical = _get_flipper_data_dir()
        data = {
            "url": self.url_entry.get(),
            "mac_prefix": self.mac_entry.get(),
            "workers": self.workers_entry.get(),
            "timeout": self.timeout_entry.get(),
            "save_results": self.save_var.get(),
            "proxy_inline": self.proxy_inline_entry.get(),
            "active_macs": self.active_macs,
            "mac_proxy_map": self.mac_proxy_map,
            "mac_status": self.mac_status,
            "logs": self.log_history[-MAX_LOG_SAVE:],
            "proxies": get_proxy_list(),
            "profiles": self.profiles,
            "active_profile": self.active_profile,
            "checked_count": self.checked_count,
            "found_count": self.found_count,
            "verbose_logs": self.verbose_logs_var.get(),
            "debug_console": self.debug_console_var.get(),
            "save_folder": self.save_folder,
            "use_proxy": self.use_proxy_var.get(),
            "player_use_proxy": self.player_use_proxy_var.get(),
            "min_channels": self.min_channels_entry.get(),
            "max_proxy_latency": self._get_max_latency(),
            "multi_proxy": self.multi_proxy_var.get(),
            "github_token_enc": _encrypt_secret(token_plain),
        }
        # Also save proxy state (tags, rate-limits)
        self._save_proxy_state()
        try:
            save_path = os.path.join(canonical, SESSION_FILE)
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_session(self):
        session_paths = []
        # 1. Canonical data dir (AppData on Windows)
        canonical = _get_flipper_data_dir()
        session_paths.append(os.path.join(canonical, SESSION_FILE))
        # 2. Current save_folder (if different from canonical)
        if self.save_folder and os.path.normpath(self.save_folder) != os.path.normpath(
            canonical
        ):
            session_paths.append(os.path.join(self.save_folder, SESSION_FILE))
        # 3. Legacy Desktop/flipper-config
        legacy = os.path.join(
            str(Path.home()), "Desktop", "flipper-config", SESSION_FILE
        )
        if legacy not in session_paths:
            session_paths.append(legacy)
        # 4. CWD
        session_paths.append(SESSION_FILE)

        # Find the best session: prefer one that has active_macs data.
        session_path = None
        data = None
        for path in session_paths:
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    candidate = json.load(f)
                if data is None:
                    # Use first valid session as baseline
                    session_path = path
                    data = candidate
                # If baseline has no MACs but this one does, upgrade to this one
                if not data.get("active_macs") and candidate.get("active_macs"):
                    session_path = path
                    data = candidate
                    break  # found one with MACs, use it
            except Exception:
                continue

        if not data:
            # Try loading MACs from results.txt as last resort
            self._load_macs_from_results()
            return

        for key, widget in [
            ("url", self.url_entry),
            ("mac_prefix", self.mac_entry),
            ("workers", self.workers_entry),
            ("timeout", self.timeout_entry),
            ("proxy_inline", self.proxy_inline_entry),
        ]:
            val = data.get(key, "")
            if val:
                widget.delete(0, tk.END)
                widget.insert(0, val)
        if "save_results" in data:
            self.save_var.set(data["save_results"])

        self.checked_count = data.get("checked_count", 0)
        self.found_count = data.get("found_count", 0)
        self._update_stats()

        for m in data.get("active_macs", []):
            self.active_macs.append(m)
            self._insert_mac_row(m)

        self.mac_proxy_map = data.get("mac_proxy_map", {})
        self.mac_status = data.get("mac_status", {})

        for msg, tag in data.get("logs", []):
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"{msg}\n", tag)
            self.log_text.configure(state=tk.DISABLED)
            self.log_history.append((msg, tag))
        if self.log_history:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, "── Sesja przywrócona ──\n", "warning")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        saved_proxies = data.get("proxies", [])
        if saved_proxies:
            set_proxy_list(saved_proxies)
            self._refresh_proxy_tree()

        self.profiles = data.get("profiles", [])
        self._refresh_profile_tree()

        self.active_profile = data.get("active_profile", None)
        if self.active_profile:
            self.active_profile_label.configure(
                text=f"Aktywny: {self.active_profile.get('name', '?')}"
            )

        if "verbose_logs" in data:
            self.verbose_logs_var.set(data["verbose_logs"])
        if "debug_console" in data:
            self.debug_console_var.set(bool(data["debug_console"]))
            if sys.platform == "win32" and self.debug_console_var.get():
                _enable_windows_console()
        if "use_proxy" in data:
            self.use_proxy_var.set(data["use_proxy"])
        if "player_use_proxy" in data:
            self.player_use_proxy_var.set(data["player_use_proxy"])
        if "min_channels" in data:
            self.min_channels_entry.delete(0, tk.END)
            self.min_channels_entry.insert(0, data["min_channels"])
        if "max_proxy_latency" in data:
            self.max_proxy_latency = float(data["max_proxy_latency"])
            if hasattr(self, "max_latency_entry"):
                self.max_latency_entry.delete(0, tk.END)
                self.max_latency_entry.insert(0, str(self.max_proxy_latency))
        if "multi_proxy" in data:
            self.multi_proxy_var.set(data["multi_proxy"])
        # Load proxy state (tags, rate-limits)
        self._load_proxy_state()
        # Schedule rate-limit recheck if needed
        rl_times = get_all_rate_limited_times()
        if rl_times:
            self._schedule_rate_limit_recheck()
        token_loaded = ""
        if "github_token_enc" in data:
            token_loaded = _decrypt_secret(data.get("github_token_enc") or "")
        elif "github_token" in data:
            # Backward compatibility with old plaintext sessions
            token_loaded = data.get("github_token") or ""

        if token_loaded:
            self.github_token = token_loaded
            if hasattr(self, "github_token_entry"):
                self.github_token_entry.delete(0, tk.END)
                self.github_token_entry.insert(0, self.github_token)

        # Restore save_folder, but ALWAYS prefer the canonical data dir
        # to prevent legacy Desktop/flipper-config paths from persisting.
        canonical = _get_flipper_data_dir()
        saved_folder = data.get("save_folder", "")
        if saved_folder and os.path.isdir(saved_folder):
            # Accept saved_folder only if it matches canonical or is a custom path
            # that is NOT a legacy Desktop/flipper-config
            norm_saved = os.path.normpath(os.path.abspath(saved_folder))
            norm_canon = os.path.normpath(os.path.abspath(canonical))
            legacy_desktop = os.path.normpath(
                os.path.join(str(Path.home()), "Desktop", "flipper-config")
            )
            if norm_saved == legacy_desktop and norm_saved != norm_canon:
                # Migrate: use canonical instead of legacy Desktop path
                self.save_folder = canonical
            else:
                self.save_folder = saved_folder
        else:
            self.save_folder = canonical
        if hasattr(self, "save_folder_entry"):
            self.save_folder_entry.delete(0, tk.END)
            self.save_folder_entry.insert(0, self.save_folder)

        # If session had no MACs, try to recover from results.txt
        if not self.active_macs:
            self._load_macs_from_results()

    def _load_macs_from_results(self):
        """Try to import MACs from results.txt files in known locations."""
        import re

        results_paths = []
        canonical = _get_flipper_data_dir()
        results_paths.append(os.path.join(canonical, RESULTS_FILE))
        if self.save_folder and os.path.normpath(self.save_folder) != os.path.normpath(
            canonical
        ):
            results_paths.append(os.path.join(self.save_folder, RESULTS_FILE))
        legacy = os.path.join(
            str(Path.home()), "Desktop", "flipper-config", RESULTS_FILE
        )
        if legacy not in results_paths:
            results_paths.append(legacy)
        results_paths.append(RESULTS_FILE)

        for rpath in results_paths:
            if not os.path.isfile(rpath):
                continue
            try:
                with open(rpath, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                count = 0
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Format: MAC | expiry | url | ch=N | proxy
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        mac = parts[0]
                        expiry = parts[1]
                        url = parts[2]
                        channels = "?"
                        proxy = ""
                        for p in parts[3:]:
                            if p.startswith("ch="):
                                channels = p[3:]
                            elif p:
                                proxy = p
                        entry = {
                            "mac": mac,
                            "expiry": expiry,
                            "url": url,
                            "channels": channels,
                            "proxy": proxy,
                        }
                        # Avoid duplicates
                        if not any(
                            m["mac"] == mac and m["url"] == url
                            for m in self.active_macs
                        ):
                            self.active_macs.append(entry)
                            self._insert_mac_row(entry)
                            count += 1
                if count > 0:
                    self._log(f"Odzyskano {count} MAC z {rpath}", "success")
                    return  # stop after first successful file
            except Exception:
                continue

    # ══════════════════════════════════════════════════════
    #  PROXY TAB LOGIC
    # ══════════════════════════════════════════════════════

    def _get_active_proxy(self):
        """Always returns a proxy — scanning is proxy-only."""
        inline = self.proxy_inline_entry.get().strip()
        if inline:
            if not inline.startswith("http"):
                inline = "http://" + inline
            return inline
        return get_current_proxy()

    def _get_proxy_for_mac(self, mac):
        return self.mac_proxy_map.get(mac)

    def _get_max_latency(self) -> float:
        """Read max proxy latency from the entry widget."""
        try:
            val = float(self.max_latency_entry.get().strip())
            if val > 0:
                self.max_proxy_latency = val
                return val
        except (ValueError, AttributeError):
            pass
        return self.max_proxy_latency

    def _auto_fetch_proxies_on_startup(self):
        if not get_proxy_list():
            self._log("Auto-pobieranie proxy przy starcie...", "info")
            threading.Thread(target=self._fetch_only_worker, daemon=True).start()
        else:
            self._log(f"Załadowano {len(get_proxy_list())} proxy z sesji.", "info")

    def _fetch_proxies(self):
        if self._proxy_fetching or self._proxy_testing:
            self._log("Pobieranie/test proxy już trwa.", "warning")
            return
        self._log("Pobieranie listy proxy z API...", "info")
        self._set_progress(10, "Pobieranie proxy...")
        threading.Thread(target=self._fetch_only_worker, daemon=True).start()

    def _toggle_proxy_pause(self):
        """Toggle pause/resume for proxy testing."""
        if not self._proxy_testing:
            return
        if self._proxy_paused.is_set():
            # Currently running → pause
            self._proxy_paused.clear()
            self.root.after(0, lambda: self.proxy_pause_btn.configure(text="▶ Wznów"))
            self._log("Testowanie proxy wstrzymane.", "warning")
        else:
            # Currently paused → resume
            self._proxy_paused.set()
            self.root.after(0, lambda: self.proxy_pause_btn.configure(text="⏸ Pauza"))
            self._log("Testowanie proxy wznowione.", "info")

    def _stop_proxy_testing(self):
        """Stop proxy testing."""
        self._proxy_stop.set()
        self._proxy_paused.set()  # unblock if paused

    def _save_proxies_to_file(self):
        """Save current proxy list to proxy.txt in data dir."""
        try:
            data_dir = _get_flipper_data_dir()
            path = os.path.join(data_dir, "proxy.txt")
            proxies = get_proxy_list()
            with open(path, "w", encoding="utf-8") as f:
                for p in proxies:
                    f.write(p + "\n")
        except Exception:
            pass

    def _import_proxies_from_file(self):
        """Import proxies from a text file (one per line)."""
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Importuj proxy z pliku",
            filetypes=[("Pliki tekstowe", "*.txt"), ("Wszystkie", "*.*")],
        )
        if not path:
            return
        try:
            count = 0
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Normalize
                    if line.startswith("http://"):
                        line = line[7:]
                    elif line.startswith("https://"):
                        line = line[8:]
                    elif line.startswith("socks4://"):
                        line = line[9:]
                    elif line.startswith("socks5://"):
                        line = line[9:]
                    line = line.strip().rstrip("/")
                    if ":" in line and len(line) > 7:
                        proxy = f"http://{line}"
                        add_proxy(proxy)
                        count += 1
            self._refresh_proxy_tree()
            self._save_proxies_to_file()
            self._log(f"Zaimportowano {count} proxy z pliku.", "success")
        except Exception as e:
            self._log(f"Błąd importu proxy: {e}", "error")

    def _add_mac_manually(self):
        """Add a MAC address manually from the entry fields."""
        mac = self.add_mac_entry.get().strip().upper()
        url = self.add_mac_url_entry.get().strip()
        if not url:
            url = self.url_entry.get().strip()
        if not mac:
            self._log("Wpisz adres MAC.", "warning")
            return
        # Validate MAC format (basic check)
        mac = mac.replace("-", ":")
        parts = mac.split(":")
        if len(parts) != 6 or not all(len(p) == 2 for p in parts):
            self._log("Nieprawidłowy format MAC (XX:XX:XX:XX:XX:XX).", "error")
            return
        if not url:
            self._log("Podaj URL serwera w polu URL lub obok MAC.", "warning")
            return
        # Check for duplicates
        existing = {m["mac"] for m in self.active_macs}
        if mac in existing:
            self._log(f"MAC {mac} już jest na liście.", "warning")
            return
        self._add_active_mac(url, mac, "ręczny", proxy="", channels="?")
        self._refresh_player_mac_list()
        self._auto_save()
        self._log(f"Dodano MAC: {mac}", "success")

    def _add_random_mac(self):
        """Generate a random MAC and add it."""
        url = self.add_mac_url_entry.get().strip()
        if not url:
            url = self.url_entry.get().strip()
        if not url:
            self._log("Podaj URL serwera.", "warning")
            return
        prefix = self.add_mac_entry.get().strip().upper().replace("-", ":")
        # Use prefix if it looks like first bytes, otherwise default
        if prefix and len(prefix) >= 8 and prefix.count(":") >= 2:
            # Take first N complete bytes as prefix
            parts = prefix.split(":")
            prefix_bytes = ":".join(p for p in parts if len(p) == 2)
            colon_count = prefix_bytes.count(":")
            if colon_count < 5:
                # Generate remaining bytes
                remaining = 5 - colon_count
                from random import randint

                tail = ":".join(f"{randint(0, 255):02X}" for _ in range(remaining))
                mac = prefix_bytes + ":" + tail
            else:
                mac = prefix_bytes
        else:
            mac = generate_random_mac()
        # Check for duplicates
        existing = {m["mac"] for m in self.active_macs}
        if mac in existing:
            mac = generate_random_mac()  # try once more
        if mac in existing:
            self._log("Nie udało się wygenerować unikalnego MAC.", "warning")
            return
        self.add_mac_entry.delete(0, tk.END)
        self.add_mac_entry.insert(0, mac)
        self._add_active_mac(url, mac, "losowy", proxy="", channels="?")
        self._refresh_player_mac_list()
        self._auto_save()
        self._log(f"Dodano losowy MAC: {mac}", "success")

    def _import_macs_from_file(self):
        """Import MAC addresses from a text file."""
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="Importuj MAC z pliku",
            filetypes=[("Pliki tekstowe", "*.txt"), ("Wszystkie", "*.*")],
        )
        if not path:
            return
        try:
            import re

            mac_pattern = re.compile(
                r"([0-9A-Fa-f]{2}(?:[:\-])[0-9A-Fa-f]{2}"
                r"(?:[:\-])[0-9A-Fa-f]{2}(?:[:\-])[0-9A-Fa-f]{2}"
                r"(?:[:\-])[0-9A-Fa-f]{2}(?:[:\-])[0-9A-Fa-f]{2})"
            )
            count = 0
            url = self.url_entry.get().strip()
            existing = {m["mac"] for m in self.active_macs}
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Try to find MAC pattern in line
                    match = mac_pattern.search(line)
                    if match:
                        mac = match.group(1).upper().replace("-", ":")
                    else:
                        # Try raw line as MAC (may lack colons)
                        clean = line.split("|")[0].split(",")[0].strip()
                        if len(clean) == 17 and clean.count(":") == 5:
                            mac = clean.upper()
                        else:
                            continue
                    if mac in existing:
                        continue
                    existing.add(mac)
                    # Try to extract expiry and URL from line
                    parts = [p.strip() for p in line.split("|")]
                    mac_url = url
                    expiry = "imported"

                    if (
                        len(parts) >= 2
                        and parts[1]
                        and not parts[1].lower().startswith("http")
                    ):
                        expiry = parts[1]

                    for p in parts[1:]:
                        if p.lower().startswith("http"):
                            mac_url = p
                            break

                    if expiry == "imported":
                        csv_parts = [p.strip() for p in line.split(",")]
                        if (
                            len(csv_parts) >= 2
                            and csv_parts[1]
                            and not csv_parts[1].lower().startswith("http")
                        ):
                            expiry = csv_parts[1]
                        for p in csv_parts[1:]:
                            if p.lower().startswith("http"):
                                mac_url = p
                                break

                    self.active_macs.append(
                        {
                            "url": mac_url,
                            "mac": mac,
                            "expiry": expiry,
                            "channels": "?",
                            "proxy": "",
                        }
                    )
                    count += 1
            if count > 0:
                self._filter_active_macs()
                self._refresh_player_mac_list()
                self.mac_count_label.configure(
                    text=f"Znaleziono: {len(self.active_macs)}"
                )
                self._auto_save()
                self._log(f"Zaimportowano {count} MAC z pliku.", "success")
            else:
                self._log("Nie znaleziono MAC adresów w pliku.", "warning")
        except Exception as e:
            self._log(f"Błąd importu MAC: {e}", "error")

    def _get_max_proxy_count(self) -> int:
        """Read max proxy count from the entry widget."""
        try:
            val = int(self.max_proxy_count_entry.get().strip())
            if val > 0:
                return val
        except (ValueError, AttributeError):
            pass
        return 500  # default

    def _fetch_only_worker(self):
        """Fetch proxies from APIs and add them with 'untested' tag.
        Stops once max proxy count is reached."""
        self._proxy_fetching = True
        self._proxy_testing = True
        max_count = self._get_max_proxy_count()
        self._log_safe(f"Pobieranie proxy z API (maks: {max_count})...", "info")

        def _fetch_cb(source, new, total):
            self.root.after(
                0,
                lambda: self.proxy_test_progress_label.configure(
                    text=f"Pobieranie: +{new} z {source} (łącznie: {total})"
                ),
            )

        proxies = fetch_free_proxies(callback=_fetch_cb, max_count=max_count)

        if not proxies:
            self._log_safe("Nie udało się pobrać proxy.", "error")
            self._set_progress(100, "Błąd pobierania proxy")
            self._proxy_testing = False
            self._proxy_fetching = False
            return

        # Add fetched proxies to the list with "untested" tag
        added = 0
        existing = set(get_proxy_list())
        for p in proxies:
            if p not in existing:
                add_proxy(p)
                existing.add(p)
                added += 1

        self.root.after(0, self._refresh_proxy_tree)
        self._save_proxies_to_file()
        self._save_proxy_state()
        total_now = len(get_proxy_list())
        self._log_safe(
            f"Pobrano {len(proxies)} proxy, dodano {added} nowych. "
            f"Łącznie: {total_now}. Użyj 'Testuj proxy' aby zweryfikować.",
            "success",
        )
        self._set_progress(100, f"{total_now} proxy pobrano")
        self.root.after(0, lambda: self.proxy_test_progress_label.configure(text=""))
        self._proxy_testing = False
        self._proxy_fetching = False

    def _fetch_proxies_worker(self):
        max_lat = self._get_max_latency()
        self._proxy_testing = True
        self._proxy_stop.clear()
        self._proxy_paused.set()
        self.root.after(0, lambda: self._btn_enable(self.proxy_pause_btn))
        self._log_safe(
            f"Pobieranie proxy z API (maks. opóźnienie: {max_lat}s)...", "info"
        )

        def _fetch_cb(source, new, total):
            self.root.after(
                0,
                lambda: self.proxy_test_progress_label.configure(
                    text=f"Pobieranie: +{new} z {source} (łącznie: {total})"
                ),
            )

        proxies = fetch_free_proxies(callback=_fetch_cb)
        if not proxies:
            self._log_safe("Nie udało się pobrać proxy.", "error")
            self._set_progress(100, "Błąd pobierania proxy")
            self._proxy_testing = False
            self.root.after(0, lambda: self._btn_disable(self.proxy_pause_btn))
            return

        total = len(proxies)
        self._log_safe(f"Pobrano {total} proxy. Testowanie pojedynczo...", "info")
        self._set_progress(25, f"Testowanie {total} proxy...")
        self.root.after(
            0,
            lambda: self.proxy_test_progress_label.configure(
                text=f"Testowanie 0/{total} proxy..."
            ),
        )

        set_proxy_list([])
        self._proxy_latencies = {}
        accepted_count = 0
        tested_count = 0

        for i, proxy in enumerate(proxies):
            # Check stop
            if self._proxy_stop.is_set():
                self._log_safe("Testowanie proxy przerwane.", "warning")
                break
            # Check pause — block until resumed
            self._proxy_paused.wait()
            if self._proxy_stop.is_set():
                break

            tested_count += 1
            latency = test_proxy_latency(proxy, timeout=max_lat + 1)
            lat_str = f"{latency:.2f}s" if latency != float("inf") else "timeout"

            pct = int(25 + (tested_count / total) * 65)
            self._set_progress(pct, f"Test proxy {tested_count}/{total}")
            self.root.after(
                0,
                lambda p=proxy, ls=lat_str, t=tested_count: (
                    self.proxy_test_progress_label.configure(
                        text=f"{t}/{total} — {p} → {ls}"
                    )
                ),
            )

            if latency <= max_lat:
                accepted_count += 1
                add_proxy(proxy)
                self._proxy_latencies[proxy] = latency
                # Add to tree immediately
                self.root.after(0, self._add_proxy_to_tree, proxy, latency)
                self.root.after(
                    0,
                    lambda c=accepted_count: self.proxy_count_label.configure(
                        text=f"Proxy: {c}"
                    ),
                )
                # Save to file every 10 accepted proxies
                if accepted_count % 10 == 0:
                    self._save_proxies_to_file()

        # Final save
        self._save_proxies_to_file()

        if accepted_count > 0:
            self._log_safe(
                f"✅ {accepted_count}/{tested_count} proxy OK "
                f"(opóźnienie ≤ {max_lat}s).",
                "success",
            )
            self._set_progress(100, f"{accepted_count} proxy gotowych")
        else:
            self._log_safe(f"❌ Żadne proxy nie spełnia limitu {max_lat}s!", "error")
            set_proxy_list([])
            self._proxy_latencies = {}
            self.root.after(0, self._refresh_proxy_tree)
            self._set_progress(100, "Brak dobrych proxy")

        self._proxy_testing = False
        self.root.after(0, lambda: self.proxy_test_progress_label.configure(text=""))
        self.root.after(0, lambda: self._btn_disable(self.proxy_pause_btn))
        self.root.after(0, lambda: self.proxy_pause_btn.configure(text="⏸ Pauza"))

    def _add_proxy_to_tree(self, proxy, latency):
        """Add a single proxy to the tree (called on UI thread)."""
        lat_str = f"{latency:.2f}s"
        tag = get_proxy_tag(proxy)
        status_text = tag if tag != PROXY_TAG_UNTESTED else "untested"
        self.proxy_tree.insert(
            "", tk.END, values=(proxy, lat_str, status_text), tags=(tag,)
        )

    def _refresh_proxy_tree(self):
        for item in self.proxy_tree.get_children():
            self.proxy_tree.delete(item)
        latencies = getattr(self, "_proxy_latencies", {})
        all_tags = get_all_proxy_tags()
        rl_times = get_all_rate_limited_times()
        now = time.time()
        for p in get_proxy_list():
            lat = latencies.get(p)
            lat_str = f"{lat:.2f}s" if lat is not None else "?"
            tag = all_tags.get(p, PROXY_TAG_UNTESTED)
            # Build display status
            if tag == PROXY_TAG_RATE_LIMITED:
                rl_at = rl_times.get(p, 0)
                remaining = max(0, RATE_LIMIT_COOLDOWN_S - (now - rl_at))
                mins = int(remaining // 60)
                status_text = f"rate-limited ({mins}m)"
            elif tag == PROXY_TAG_WORKING:
                status_text = "working"
            elif tag == PROXY_TAG_DEAD:
                status_text = "dead"
            else:
                status_text = "untested"
            self.proxy_tree.insert(
                "", tk.END, values=(p, lat_str, status_text), tags=(tag,)
            )
        total = len(get_proxy_list())
        usable = get_usable_proxy_count()
        self.proxy_count_label.configure(text=f"Proxy: {usable}/{total}")

    def _clear_proxies(self):
        if self._proxy_testing:
            self._stop_proxy_testing()
        if self._deep_proxy_testing:
            self._stop_deep_proxy_test()
        set_proxy_list([])
        self._proxy_latencies = {}
        self._refresh_proxy_tree()
        self._save_proxies_to_file()
        self._save_proxy_state()
        self._log("Wyczyszczono listę proxy.", "info")

    def _add_custom_proxy(self):
        val = self.proxy_add_entry.get().strip()
        if not val:
            return
        if not val.startswith("http"):
            val = "http://" + val
        add_proxy(val)
        self.proxy_add_entry.delete(0, tk.END)
        self._refresh_proxy_tree()
        self._save_proxies_to_file()
        self._log(f"Dodano proxy: {val}", "info")

    def _remove_selected_proxy(self):
        sel = self.proxy_tree.selection()
        if not sel:
            self._log("Zaznacz proxy do usunięcia.", "warning")
            return
        val = self.proxy_tree.item(sel[0], "values")[0]
        remove_proxy(val)
        self._refresh_proxy_tree()
        self._log(f"Usunięto proxy: {val}", "info")

    def _handle_proxy_fail(self, proxy, status_code=0):
        if not proxy:
            return
        # Handle 429 — rate-limited, don't remove, just tag it
        if status_code == 429:
            mark_proxy_rate_limited(proxy)
            self._log_safe(
                f"Proxy rate-limited (429): {proxy} — cooldown 1h", "warning"
            )
            self.root.after(0, self._refresh_proxy_tree)
            self._save_proxy_state()
            self._schedule_rate_limit_recheck()
            new_proxy = rotate_proxy()
            if new_proxy:
                self._log_safe(f"Zmiana proxy → {new_proxy}", "info")
            return
        if status_code and should_remove_proxy(status_code):
            set_proxy_tag(proxy, PROXY_TAG_DEAD)
            remove_proxy(proxy)
            self._log_safe(f"Proxy usunięty (HTTP {status_code}): {proxy}", "warning")
            self.root.after(0, self._refresh_proxy_tree)
        else:
            removed = report_proxy_fail(proxy)
            if removed:
                self._log_safe(
                    f"Proxy usunięty (zbyt wiele błędów): {proxy}", "warning"
                )
                self.root.after(0, self._refresh_proxy_tree)
        new_proxy = rotate_proxy()
        if new_proxy:
            self._log_safe(f"Zmiana proxy → {new_proxy}", "info")

    # ══════════════════════════════════════════════════════
    #  ROTATE PROXY (manual button)
    # ══════════════════════════════════════════════════════

    def _rotate_proxy_manual(self):
        """Manually rotate to the next proxy for all pending requests."""
        new_proxy = rotate_proxy()
        if new_proxy:
            self._log(f"Ręczna zmiana proxy → {new_proxy}", "info")
        else:
            self._log("Brak proxy do rotacji.", "warning")

    # ══════════════════════════════════════════════════════
    #  DEEP PROXY TESTING (MAC-check based, 500 attempts)
    # ══════════════════════════════════════════════════════

    def _toggle_deep_proxy_test(self):
        """Toggle deep proxy testing. When running, button becomes Pause."""
        if self._deep_proxy_testing:
            # Currently testing — toggle pause
            if self._deep_proxy_paused.is_set():
                self._deep_proxy_paused.clear()
                self.root.after(
                    0, lambda: self.deep_test_btn.configure(text="▶ Wznów test")
                )
                self._log("Deep test proxy wstrzymany.", "warning")
            else:
                self._deep_proxy_paused.set()
                self.root.after(
                    0, lambda: self.deep_test_btn.configure(text="⏸ Pauza testu")
                )
                self._log("Deep test proxy wznowiony.", "info")
            return

        # Not running — start it
        url_raw = self.url_entry.get().strip()
        mac_prefix = self.mac_entry.get().strip()
        if not url_raw:
            self._log("Podaj URL serwera aby testować proxy!", "error")
            return
        if len(mac_prefix) < 8:
            self._log("Podaj prefix MAC (XX:XX:XX) aby testować proxy!", "error")
            return

        proxies = get_proxy_list()
        untested = [p for p in proxies if get_proxy_tag(p) in (PROXY_TAG_UNTESTED,)]
        if not untested:
            # Also allow re-testing working proxies
            untested = [p for p in proxies if get_proxy_tag(p) != PROXY_TAG_DEAD]
        if not untested:
            self._log("Brak proxy do przetestowania.", "warning")
            return

        self._deep_proxy_testing = True
        self._deep_proxy_stop.clear()
        self._deep_proxy_paused.set()
        self.root.after(0, lambda: self.deep_test_btn.configure(text="⏸ Pauza testu"))
        self.root.after(0, lambda: self.deep_test_btn._normal_bg == "#c78d00")

        server_address = parse_url(url_raw)
        threading.Thread(
            target=self._deep_proxy_test_worker,
            args=(server_address, mac_prefix, untested),
            daemon=True,
        ).start()

    def _stop_deep_proxy_test(self):
        """Stop deep proxy testing."""
        self._deep_proxy_stop.set()
        self._deep_proxy_paused.set()

    def _deep_proxy_test_worker(self, server_address, mac_prefix, proxies_to_test):
        """Test each proxy by doing real MAC checks. 500 attempts per proxy.
        If a proxy finds a working MAC → tag as 'working', stop testing it.
        If 500 attempts with zero working MACs → tag as 'dead', remove it."""
        timeout = self._get_timeout()
        total_proxies = len(proxies_to_test)
        max_attempts = 500

        # First find the endpoint
        self._log_safe(f"Deep test: szukam endpoint-u na {server_address}...", "info")
        endpoint, _ = self._find_endpoint_with_proxy_retry(server_address, timeout)
        if not endpoint:
            self._log_safe("Deep test: nie znaleziono endpoint-u!", "error")
            self._deep_proxy_testing = False
            self.root.after(
                0, lambda: self.deep_test_btn.configure(text="🧪 Testuj proxy")
            )
            return

        url = server_address + endpoint

        for pi, proxy in enumerate(proxies_to_test):
            if self._deep_proxy_stop.is_set():
                self._log_safe("Deep test przerwany.", "warning")
                break
            self._deep_proxy_paused.wait()
            if self._deep_proxy_stop.is_set():
                break

            init_proxy_test_stats(proxy)
            self._log_safe(f"Deep test [{pi + 1}/{total_proxies}]: {proxy}", "info")

            proxy_working = False
            for attempt in range(max_attempts):
                if self._deep_proxy_stop.is_set():
                    break
                self._deep_proxy_paused.wait()
                if self._deep_proxy_stop.is_set():
                    break

                mac = generate_random_mac(mac_prefix)
                result = check_mac(url, mac, timeout=timeout, proxy=proxy)
                codes = result.get("codes", [])

                # Check for 429 rate limit
                for code in codes:
                    if code == 429:
                        mark_proxy_rate_limited(proxy)
                        self._log_safe(
                            f"Deep test: {proxy} → 429 rate-limited, skip", "warning"
                        )
                        self.root.after(0, self._refresh_proxy_tree)
                        self._save_proxy_state()
                        proxy_working = None  # signal to skip
                        break
                if proxy_working is None:
                    break

                found = result.get("found", False)
                checked, found_total = update_proxy_test_stats(proxy, found)

                if found:
                    set_proxy_tag(proxy, PROXY_TAG_WORKING)
                    report_proxy_success(proxy)
                    self._log_safe(
                        f"Deep test: {proxy} → WORKING "
                        f"(znaleziono MAC po {checked} próbach)",
                        "success",
                    )
                    self.root.after(0, self._refresh_proxy_tree)
                    self._save_proxy_state()

                    # Also add the found MAC
                    ch_count = 0
                    try:
                        ch_count = count_channels_quick(
                            url, mac, timeout=timeout, proxy=proxy
                        )
                    except Exception:
                        pass
                    self.found_count += 1
                    self._update_stats_safe()
                    self._add_active_mac(
                        url, mac, result["expiry"], proxy, channels=ch_count
                    )
                    self._auto_save()
                    proxy_working = True
                    break
                else:
                    # Handle bad codes — mark dead immediately
                    for code in codes:
                        if code and should_remove_proxy(code):
                            set_proxy_tag(proxy, PROXY_TAG_DEAD)
                            self._log_safe(
                                f"Deep test: {proxy} → DEAD (HTTP {code})", "warning"
                            )
                            self.root.after(0, self._refresh_proxy_tree)
                            proxy_working = None
                            break
                    if proxy_working is None:
                        break

                # Progress update every 50 attempts
                if checked % 50 == 0:
                    pct = int((pi / total_proxies) * 100)
                    self._set_progress(pct, f"Deep test {pi + 1}/{total_proxies}")
                    self.root.after(
                        0,
                        lambda p=proxy, c=checked: (
                            self.proxy_test_progress_label.configure(
                                text=f"Deep test: {p} — {c}/{max_attempts} prób"
                            )
                        ),
                    )

            # After all attempts for this proxy
            if proxy_working is None:
                # Was rate-limited or dead, already handled
                continue
            if not proxy_working:
                # 500 attempts, zero MACs found → dead
                set_proxy_tag(proxy, PROXY_TAG_DEAD)
                self._log_safe(
                    f"Deep test: {proxy} → DEAD "
                    f"(0 znalezionych w {max_attempts} próbach)",
                    "warning",
                )
                self.root.after(0, self._refresh_proxy_tree)
                self._save_proxy_state()

        self._deep_proxy_testing = False
        self._set_progress(100, "Deep test zakończony")
        self.root.after(0, lambda: self.deep_test_btn.configure(text="🧪 Testuj proxy"))
        self.root.after(0, lambda: self.proxy_test_progress_label.configure(text=""))
        self._save_proxy_state()
        self._log_safe("Deep test zakończony.", "info")

    # ══════════════════════════════════════════════════════
    #  RATE-LIMIT RECHECK (auto-recheck after 1hr)
    # ══════════════════════════════════════════════════════

    def _schedule_rate_limit_recheck(self):
        """Schedule a periodic check for rate-limited proxies whose cooldown expired."""
        if self._rl_recheck_timer is not None:
            return  # Already scheduled
        self._rl_recheck_timer = self.root.after(60000, self._recheck_rate_limited)

    def _recheck_rate_limited(self):
        """Check if any rate-limited proxies have expired cooldown and test them."""
        self._rl_recheck_timer = None
        rl_times = get_all_rate_limited_times()
        now = time.time()
        rechecked = False
        for proxy, ts in rl_times.items():
            if now - ts >= RATE_LIMIT_COOLDOWN_S:
                # Cooldown expired — mark as untested so it gets picked up
                set_proxy_tag(proxy, PROXY_TAG_UNTESTED)
                self._log(f"Rate-limit wygasł: {proxy} → do ponownego testu", "info")
                rechecked = True
        if rechecked:
            self._refresh_proxy_tree()
            self._save_proxy_state()

        # Reschedule if there are still rate-limited proxies
        remaining = get_all_rate_limited_times()
        if remaining:
            self._rl_recheck_timer = self.root.after(60000, self._recheck_rate_limited)

    # ══════════════════════════════════════════════════════
    #  PROXY STATE PERSISTENCE
    # ══════════════════════════════════════════════════════

    def _save_proxy_state(self):
        """Save proxy tags/state to proxy_state.json."""
        try:
            data_dir = _get_flipper_data_dir()
            save_proxy_state(data_dir)
        except Exception:
            pass

    def _load_proxy_state(self):
        """Load proxy tags/state from proxy_state.json."""
        try:
            data_dir = _get_flipper_data_dir()
            load_proxy_state(data_dir)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  PROXY RETRY HELPER — try all proxies before giving up
    # ══════════════════════════════════════════════════════

    def _find_endpoint_with_proxy_retry(self, server_address, timeout):
        """Try to find a responding endpoint, cycling through all proxies.
        Proxy-only: will NOT fall back to direct connection.
        Returns (endpoint, proxy_used) or (None, None).
        """
        proxy = self._get_active_proxy()

        if not proxy:
            # No proxy available — cannot scan without proxy
            self._log_safe(
                "❌ Brak proxy! Skanowanie wymaga proxy. "
                "Pobierz proxy w zakładce Proxy.",
                "error",
            )
            return None, None

        endpoint, ep_code = get_responding_endpoint(
            server_address, timeout=timeout, proxy=proxy
        )

        if endpoint:
            return endpoint, proxy

        # First proxy failed — iterate through all available proxies
        tried = {proxy} if proxy else set()
        for attempt in range(MAX_PROXY_RETRIES):
            if proxy:
                self._handle_proxy_fail(proxy, ep_code)
            proxy = self._get_active_proxy()

            if not proxy or proxy in tried:
                # Try rotating to get a fresh one
                proxy = rotate_proxy()
            if not proxy or proxy in tried:
                break

            tried.add(proxy)
            self._log_safe(f"Próba {attempt + 2} z proxy: {proxy}", "info")
            endpoint, ep_code = get_responding_endpoint(
                server_address, timeout=timeout, proxy=proxy
            )
            if endpoint:
                return endpoint, proxy

        self._log_safe(
            f"❌ Serwer nie odpowiada przez żadne proxy (HTTP {ep_code})! "
            f"Pobierz nowe proxy.",
            "error",
        )
        return None, None

    # ══════════════════════════════════════════════════════
    #  PROFILES
    # ══════════════════════════════════════════════════════

    def _refresh_profile_tree(self):
        for item in self.profile_tree.get_children():
            self.profile_tree.delete(item)
        for p in self.profiles:
            self.profile_tree.insert(
                "", tk.END, values=(p["name"], p["mac"], p["url"], p.get("proxy", ""))
            )

    def _save_profile_from_form(self):
        name = self.profile_name_entry.get().strip()
        mac = self.profile_mac_entry.get().strip()
        url = self.profile_url_entry.get().strip()
        proxy = self.profile_proxy_entry.get().strip()
        if not name or not mac:
            self._log("Podaj nazwę i MAC dla profilu.", "warning")
            return
        self.profiles.append({"name": name, "mac": mac, "url": url, "proxy": proxy})
        self._refresh_profile_tree()
        self.profile_name_entry.delete(0, tk.END)
        self.profile_mac_entry.delete(0, tk.END)
        self.profile_url_entry.delete(0, tk.END)
        self.profile_proxy_entry.delete(0, tk.END)
        self._log(f"Zapisano profil: {name}", "success")

    def _set_active_profile(self):
        sel = self.profile_tree.selection()
        if not sel:
            self._log("Zaznacz profil.", "warning")
            return
        idx = self.profile_tree.index(sel[0])
        if idx < len(self.profiles):
            self.active_profile = self.profiles[idx]
            self.active_profile_label.configure(
                text=f"Aktywny: {self.active_profile['name']}"
            )
            self._log(f"Aktywny profil: {self.active_profile['name']}", "info")

    def _edit_profile(self):
        sel = self.profile_tree.selection()
        if not sel:
            self._log("Zaznacz profil do edycji.", "warning")
            return
        idx = self.profile_tree.index(sel[0])
        if idx >= len(self.profiles):
            return

        profile = self.profiles[idx]
        old_name = profile.get("name", "")

        name = simpledialog.askstring(
            "Edytuj profil",
            "Nazwa:",
            initialvalue=profile.get("name", ""),
            parent=self.root,
        )
        if name is None:
            return

        mac = simpledialog.askstring(
            "Edytuj profil",
            "MAC:",
            initialvalue=profile.get("mac", ""),
            parent=self.root,
        )
        if mac is None:
            return

        url = simpledialog.askstring(
            "Edytuj profil",
            "URL:",
            initialvalue=profile.get("url", ""),
            parent=self.root,
        )
        if url is None:
            return

        proxy = simpledialog.askstring(
            "Edytuj profil",
            "Proxy (puste = brak):",
            initialvalue=profile.get("proxy", ""),
            parent=self.root,
        )
        if proxy is None:
            return

        profile["name"] = name.strip() or old_name
        profile["mac"] = mac.strip()
        profile["url"] = url.strip()
        profile["proxy"] = proxy.strip()

        if (
            self.active_profile
            and self.active_profile.get("name") == old_name
            and self.active_profile.get("mac") == self.profiles[idx].get("mac")
        ):
            self.active_profile = profile
            self.active_profile_label.configure(text=f"Aktywny: {profile['name']}")

        self._refresh_profile_tree()
        self._refresh_player_profile_list()
        self._log(f"Zaktualizowano profil: {profile['name']}", "success")

    def _rename_profile(self):
        """Rename selected profile via dialog."""
        sel = self.profile_tree.selection()
        if not sel:
            self._log("Zaznacz profil do zmiany nazwy.", "warning")
            return
        idx = self.profile_tree.index(sel[0])
        if idx >= len(self.profiles):
            return
        old_name = self.profiles[idx]["name"]
        new_name = simpledialog.askstring(
            "Zmień nazwę",
            "Nowa nazwa profilu:",
            initialvalue=old_name,
            parent=self.root,
        )
        if not new_name or new_name == old_name:
            return
        self.profiles[idx]["name"] = new_name
        if (
            self.active_profile
            and self.active_profile.get("mac") == self.profiles[idx]["mac"]
        ):
            self.active_profile["name"] = new_name
            self.active_profile_label.configure(text=f"Aktywny: {new_name}")
        self._refresh_profile_tree()
        self._log(f"Zmieniono nazwę: {old_name} → {new_name}", "info")

    def _delete_profile(self):
        sel = self.profile_tree.selection()
        if not sel:
            self._log("Zaznacz profil do usunięcia.", "warning")
            return
        idx = self.profile_tree.index(sel[0])
        if idx < len(self.profiles):
            removed = self.profiles.pop(idx)
            if (
                self.active_profile
                and self.active_profile.get("name") == removed["name"]
                and self.active_profile.get("mac") == removed["mac"]
            ):
                self.active_profile = None
                self.active_profile_label.configure(text="Aktywny: (brak)")
            self._refresh_profile_tree()
            self._refresh_player_profile_list()
            self._log(f"Usunięto profil: {removed['name']}", "info")

    # ══════════════════════════════════════════════════════
    #  PLAYER SIDEBAR HELPERS (only MAC, no URL)
    # ══════════════════════════════════════════════════════

    def _refresh_player_mac_list(self):
        self.player_mac_listbox.delete(0, tk.END)
        for i, m in enumerate(self.active_macs):
            mac = m["mac"]
            self.player_mac_listbox.insert(tk.END, mac)
            status = self.mac_status.get(mac)
            if status == "green":
                self.player_mac_listbox.itemconfigure(
                    i, fg="#00ff88", selectforeground="#00ff88"
                )
            elif status == "red":
                self.player_mac_listbox.itemconfigure(
                    i, fg="#ff4444", selectforeground="#ff4444"
                )

    def _set_mac_status(self, mac, status):
        """Set MAC status color: 'green', 'red', or None to reset."""
        if status:
            self.mac_status[mac] = status
        else:
            self.mac_status.pop(mac, None)
        self.root.after(0, self._refresh_player_mac_list)

    def _refresh_player_profile_list(self):
        self.player_profile_listbox.delete(0, tk.END)
        for p in self.profiles:
            text = f"{p['name']}  ({p['mac'][:17]})"
            self.player_profile_listbox.insert(tk.END, text)

    def _delete_selected_player_mac(self):
        sel = self.player_mac_listbox.curselection()
        if not sel:
            self._log("Zaznacz MAC w panelu Player.", "warning")
            return
        idx = sel[0]
        if idx >= len(self.active_macs):
            return
        removed = self.active_macs.pop(idx)
        self.mac_proxy_map.pop(removed.get("mac", ""), None)
        self._filter_active_macs()
        self._refresh_player_mac_list()
        self.mac_count_label.configure(text=f"Znaleziono: {len(self.active_macs)}")
        self._auto_save()
        self._log(f"Usunięto MAC: {removed.get('mac', '?')}", "info")

    def _delete_selected_player_profile(self):
        sel = self.player_profile_listbox.curselection()
        if not sel:
            self._log("Zaznacz profil w panelu Player.", "warning")
            return
        idx = sel[0]
        if idx >= len(self.profiles):
            return
        removed = self.profiles.pop(idx)
        if (
            self.active_profile
            and self.active_profile.get("name") == removed.get("name")
            and self.active_profile.get("mac") == removed.get("mac")
        ):
            self.active_profile = None
            self.active_profile_label.configure(text="Aktywny: (brak)")
        self._refresh_profile_tree()
        self._refresh_player_profile_list()
        self._log(f"Usunięto profil: {removed.get('name', '?')}", "info")

    def _edit_selected_player_profile(self):
        sel = self.player_profile_listbox.curselection()
        if not sel:
            self._log("Zaznacz profil do edycji.", "warning")
            return
        idx = sel[0]
        if idx >= len(self.profiles):
            return
        self.profile_tree.selection_set(self.profile_tree.get_children()[idx])
        self._edit_profile()

    def _on_player_mac_select(self, event):
        sel = self.player_mac_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.active_macs):
            m = self.active_macs[idx]
            self.active_profile = {
                "name": m["mac"][:17],
                "mac": m["mac"],
                "url": m["url"],
                "proxy": m.get("proxy", ""),
            }
            self.active_profile_label.configure(text=f"Aktywny: {m['mac']}")
            self._fetch_channels()

    def _on_player_profile_select(self, event):
        sel = self.player_profile_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self.profiles):
            self.active_profile = self.profiles[idx]
            self.active_profile_label.configure(
                text=f"Aktywny: {self.active_profile['name']}"
            )
            self._fetch_channels()

    def _get_player_mac_url_proxy(self):
        if self.active_profile:
            mac = self.active_profile.get("mac", "")
            url = self.active_profile.get("url", "")
            proxy = self.active_profile.get("proxy", "")
            if self.player_use_proxy_var.get():
                if not proxy:
                    proxy = self._get_proxy_for_mac(mac)
            else:
                proxy = None
            if not url:
                url = self.url_entry.get().strip()
            return mac, url, proxy or None
        return None, None, None

    # ══════════════════════════════════════════════════════
    #  PLAYER CONTENT TYPE + GENRES
    # ══════════════════════════════════════════════════════

    def _switch_content_type(self, ctype):
        self.player_content_type = ctype
        self.nav_stack.clear()
        self._update_nav_ui()
        for ct, btn in self.content_type_btns:
            if ct == ctype:
                btn._normal_bg = ACCENT
                btn._hover_bg = "#1d4ed8"
                btn.configure(bg=ACCENT)
            else:
                btn._normal_bg = "#333355"
                btn._hover_bg = "#444466"
                btn.configure(bg="#333355")
        self._fetch_channels()

    def _on_genre_change(self, *args):
        self._fetch_channels_for_genre()

    # ══════════════════════════════════════════════════════
    #  NAVIGATION STACK
    # ══════════════════════════════════════════════════════

    def _update_nav_ui(self):
        if self.nav_stack:
            self._btn_enable(self.go_back_btn)
            trail = " → ".join(s.get("label", "?") for s in self.nav_stack)
            self.nav_label.configure(text=trail)
        else:
            self._btn_disable(self.go_back_btn)
            self.nav_label.configure(text="")

    def _nav_go_back(self):
        if not self.nav_stack:
            return
        self.nav_stack.pop()
        if self.nav_stack:
            prev = self.nav_stack[-1]
            self.player_channels = prev.get("channels", [])
            self.root.after(0, self._populate_channel_tree)
        else:
            self._fetch_channels()
        self._update_nav_ui()

    def _on_channel_double_click(self, event):
        sel = self.channel_tree.selection()
        if not sel:
            return
        item_id = sel[0]
        ch = self._get_channel_for_tree_item(item_id)
        if ch is None:
            return

        if ch.get("cmd"):
            self._play_channel_entry(ch)
            return

        genre_id = ch.get("id")
        genre_name = ch.get("name", ch.get("title", "?"))
        if genre_id:
            self.nav_stack.append(
                {
                    "label": genre_name,
                    "channels": list(self.player_channels),
                    "genre_id": str(genre_id),
                }
            )
            self._update_nav_ui()
            self._fetch_genre_channels(str(genre_id))
        else:
            self._log(
                f"Element '{genre_name}' nie ma strumienia ani kategorii.", "warning"
            )

    # ══════════════════════════════════════════════════════
    #  CHANNEL SEARCH / FILTER / SORT
    # ══════════════════════════════════════════════════════

    def _filter_channel_list(self, *args):
        query = self.channel_search_var.get().strip().lower()
        for item in self.channel_tree.get_children():
            self.channel_tree.delete(item)
        self._tree_item_to_channel = {}
        count = 0
        for ch in self.player_channels:
            num = ch.get("number", ch.get("id", ""))
            name = ch.get("name", ch.get("title", ch.get("o_name", "?")))
            if (
                query
                and query not in str(name).lower()
                and query not in str(num).lower()
            ):
                continue
            self.channel_tree.insert("", tk.END, values=(num, name))
            self._tree_item_to_channel[self.channel_tree.get_children()[-1]] = ch
            count += 1
        self.channel_count_label.configure(text=f"Kanały: {count}")

    def _sort_channel_list(self):
        self.player_channels.sort(
            key=lambda c: c.get("name", c.get("title", c.get("o_name", ""))).lower()
        )
        self._populate_channel_tree()
        self._log("Posortowano kanały A→Z.", "info")

    # ══════════════════════════════════════════════════════
    #  CHANNEL FETCHING — uses URL directly (no endpoint scan)
    # ══════════════════════════════════════════════════════

    def _fetch_channels(self):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac:
            self._log("Wybierz MAC lub profil w panelu Player.", "error")
            return
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            self._log("Podaj URL serwera.", "error")
            return

        self._log(
            f"Pobieranie kanałów ({self.player_content_type}) dla {mac}...", "info"
        )
        self.player_status_label.configure(text="Pobieranie kanałów...")
        self._set_progress(10, "Łączenie z serwerem...")

        threading.Thread(
            target=self._fetch_channels_worker, args=(url_raw, mac, proxy), daemon=True
        ).start()

    def _fetch_channels_worker(self, url_raw, mac, proxy):
        timeout = self._get_timeout()
        url = parse_url(url_raw)
        base_cache_key = f"{url}|{mac}|{self.player_content_type}"
        genres_cache_key = f"{base_cache_key}|genres"

        # Try genres cache first
        cache = self._load_channels_cache()
        cached_genres = cache.get(genres_cache_key)
        if cached_genres:
            self.player_genres = cached_genres
            self.player_channels = list(cached_genres)
            count = len(cached_genres)
            self._log_safe(
                f"Załadowano {count} kategorii z cache ({self.player_content_type}).",
                "info",
            )
            self._set_progress(100, f"Cache: {count} kategorii")
            self.root.after(0, self._populate_genre_menu)
            self.root.after(0, self._populate_channel_tree)
            self.root.after(
                0,
                lambda: self.player_status_label.configure(
                    text=f"{count} kategorii (cache)"
                ),
            )
            # Mark MAC green — cache hit means it worked before
            self._set_mac_status(mac, "green")

            # Still do handshake for token
            self._set_progress(90, "Handshake...")
            token, _ = get_handshake(url, mac, timeout=timeout, proxy=proxy)
            if token:
                self.player_token = token
                self._fetch_account_info_worker(url, mac, token, proxy)
            return

        self._set_progress(30, "Handshake...")
        token, hs_code = get_handshake(url, mac, timeout=timeout, proxy=proxy)
        if not token:
            self._log_safe(f"Handshake failed (HTTP {hs_code}).", "error")
            self._set_progress(100, "Błąd handshake")
            self.root.after(
                0, lambda: self.player_status_label.configure(text="Błąd połączenia")
            )
            # Mark MAC red — handshake failed
            self._set_mac_status(mac, "red")
            return
        self.player_token = token

        # Fetch genres
        self._set_progress(50, "Pobieranie kategorii...")
        genres = get_genres(
            url,
            mac,
            token,
            content_type=self.player_content_type,
            timeout=timeout,
            proxy=proxy,
        )
        self.player_genres = genres
        self.player_channels = list(genres)
        self.root.after(0, self._populate_genre_menu)
        self.root.after(0, self._populate_channel_tree)

        # Save genres to cache
        cache[genres_cache_key] = genres
        self._save_channels_cache(cache)

        if genres:
            # Mark MAC green — genres loaded successfully
            self._set_mac_status(mac, "green")
        else:
            # Mark MAC red — no genres returned
            self._set_mac_status(mac, "red")

        self._log_safe(
            f"Załadowano {len(genres)} kategorii ({self.player_content_type}).",
            "success",
        )
        self._set_progress(100, f"Wybierz kategorię ({len(genres)})")
        self.root.after(
            0,
            lambda: self.player_status_label.configure(
                text=f"{len(genres)} kategorii — wybierz kategorię"
            ),
        )

        # Also fetch account info
        self._fetch_account_info_worker(url, mac, token, proxy)

    def _genre_channels_cache_key(self, url, mac, content_type, genre_id):
        return f"{url}|{mac}|{content_type}|genre|{genre_id}"

    def _fetch_genre_channels(self, genre_id):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not self.player_token:
            return
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            return
        self._set_progress(30, "Pobieranie kategorii...")
        threading.Thread(
            target=self._fetch_genre_worker,
            args=(url_raw, mac, proxy, genre_id),
            daemon=True,
        ).start()

    def _fetch_channels_for_genre(self):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not self.player_token:
            return
        genre_name = self.genre_var.get()
        if genre_name == "Wszystkie":
            self.player_channels = list(self.player_genres)
            self.root.after(0, self._populate_channel_tree)
            self.root.after(
                0,
                lambda: self.player_status_label.configure(
                    text=f"{len(self.player_genres)} kategorii"
                ),
            )
            return
        else:
            genre_id = "*"
            for g in self.player_genres:
                name = g.get("title", g.get("name", ""))
                if name == genre_name:
                    genre_id = str(g.get("id", "*"))
                    break
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            return
        threading.Thread(
            target=self._fetch_genre_worker,
            args=(url_raw, mac, proxy, genre_id),
            daemon=True,
        ).start()

    def _fetch_genre_worker(self, url_raw, mac, proxy, genre_id):
        timeout = self._get_timeout()
        url = parse_url(url_raw)

        cache = self._load_channels_cache()
        genre_cache_key = self._genre_channels_cache_key(
            url, mac, self.player_content_type, genre_id
        )
        cached_items = cache.get(genre_cache_key)
        if cached_items is not None:
            self.player_channels = cached_items
            self._set_progress(100, f"Cache: {len(cached_items)} kanałów")
            self.root.after(0, self._populate_channel_tree)
            self.root.after(
                0,
                lambda: self.player_status_label.configure(
                    text=f"{len(cached_items)} kanałów (cache)"
                ),
            )
            return

        items = []
        page = 1
        while True:
            batch = get_channels(
                url,
                mac,
                self.player_token,
                genre_id=genre_id,
                content_type=self.player_content_type,
                page=page,
                timeout=timeout,
                proxy=proxy,
            )
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 10:
                break
            page += 1
            if page > 50:
                break
        self.player_channels = items

        cache[genre_cache_key] = items
        self._save_channels_cache(cache)

        self._set_progress(100, f"{len(items)} kanałów")
        self.root.after(0, self._populate_channel_tree)

    def _populate_genre_menu(self):
        menu = self.genre_menu["menu"]
        menu.delete(0, "end")
        menu.add_command(
            label="Wszystkie", command=lambda: self.genre_var.set("Wszystkie")
        )
        for g in self.player_genres:
            name = g.get("title", g.get("name", "?"))
            menu.add_command(label=name, command=lambda n=name: self.genre_var.set(n))

    def _populate_channel_tree(self):
        query = self.channel_search_var.get().strip().lower()
        for item in self.channel_tree.get_children():
            self.channel_tree.delete(item)
        self._tree_item_to_channel = {}
        count = 0
        for ch in self.player_channels:
            num = ch.get("number", ch.get("id", ""))
            name = ch.get("name", ch.get("title", ch.get("o_name", "?")))
            if (
                query
                and query not in str(name).lower()
                and query not in str(num).lower()
            ):
                continue
            self.channel_tree.insert("", tk.END, values=(num, name))
            self._tree_item_to_channel[self.channel_tree.get_children()[-1]] = ch
            count += 1
        self.channel_count_label.configure(text=f"Kanały: {count}")

    def _get_channel_for_tree_item(self, item_id):
        """Get channel dict for a tree item, works even when filtered."""
        mapping = getattr(self, "_tree_item_to_channel", {})
        return mapping.get(item_id)

    # ══════════════════════════════════════════════════════
    #  ACCOUNT INFO (Info tab)
    # ══════════════════════════════════════════════════════

    def _fetch_account_info(self):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac:
            self._log("Wybierz MAC lub profil aby pobrać info.", "warning")
            return
        if not url_raw:
            url_raw = self.url_entry.get().strip()
        if not url_raw:
            self._log("Podaj URL serwera.", "error")
            return
        self._log("Pobieranie informacji o koncie...", "info")
        self._set_progress(20, "Pobieranie info...")
        threading.Thread(
            target=self._fetch_account_info_thread,
            args=(url_raw, mac, proxy),
            daemon=True,
        ).start()

    def _fetch_account_info_thread(self, url_raw, mac, proxy):
        timeout = self._get_timeout()
        url = parse_url(url_raw)
        token, _ = get_handshake(url, mac, timeout=timeout, proxy=proxy)
        if not token:
            self._log_safe("Handshake failed.", "error")
            self._set_progress(100, "Błąd")
            return
        self._fetch_account_info_worker(url, mac, token, proxy)

    def _fetch_account_info_worker(self, url, mac, token, proxy):
        try:
            timeout = self._get_timeout()
            cookies = make_cookies(mac)
            params = make_params(mac, "get_main_info", "account_info")
            headers = {
                "User-Agent": random_user_agent(),
                "Accept": "*/*",
                "Authorization": f"Bearer {token}",
            }
            res = _request_get(
                url,
                params=params,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
                proxy=proxy,
            )
            if res.status_code != 200:
                self._log_safe(f"Account info HTTP {res.status_code}", "error")
                return

            js = res.json().get("js", {})
            if not js:
                return

            profile = {}
            try:
                params2 = make_params(mac, "get_profile", "stb")
                res2 = _request_get(
                    url,
                    params=params2,
                    headers=headers,
                    cookies=cookies,
                    timeout=timeout,
                    proxy=proxy,
                )
                if res2.status_code == 200:
                    profile = res2.json().get("js", {})
            except Exception:
                pass

            info_lines = []
            info_lines.append(("URL:", url))
            info_lines.append(("MAC:", mac))
            info_lines.append(("─" * 40, ""))

            phone = js.get("phone", "?")
            info_lines.append(("Wygasa:", phone))

            for key, label in [
                ("mac", "MAC (serwer)"),
                ("ip", "IP"),
                ("login", "Login"),
                ("status", "Status"),
            ]:
                val = js.get(key, "")
                if val:
                    info_lines.append((f"{label}:", str(val)))

            if profile:
                info_lines.append(("─" * 40, ""))
                for key, label in [
                    ("name", "Nazwa profilu"),
                    ("sname", "Nazwa STB"),
                    ("stb_type", "Typ STB"),
                    ("timezone", "Strefa czasowa"),
                    ("locale", "Język"),
                ]:
                    val = profile.get(key, "")
                    if val:
                        info_lines.append((f"{label}:", str(val)))

            self.root.after(0, self._display_account_info, info_lines)
            self._set_progress(100, "Info pobrane")

        except Exception as e:
            self._log_safe(f"Błąd pobierania info: {e}", "error")
            self._set_progress(100, "Błąd")

    def _display_account_info(self, info_lines):
        self.info_text.configure(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        for label, value in info_lines:
            if label.startswith("─"):
                self.info_text.insert(tk.END, f"{label}\n", "label")
            else:
                self.info_text.insert(tk.END, f"{label} ", "label")
                self.info_text.insert(
                    tk.END, f"{value}\n", "highlight" if "Wygasa" in label else "value"
                )
        self.info_text.configure(state=tk.DISABLED)

    # ══════════════════════════════════════════════════════
    #  MPV EMBEDDED PLAYER
    # ══════════════════════════════════════════════════════

    def _init_mpv(self):
        """Initialize mpv player embedded in the player_frame."""
        if not HAS_MPV:
            return
        try:
            wid = str(int(self.player_frame.winfo_id()))
            if sys.platform == "win32":
                vo = "gpu"
            elif sys.platform == "darwin":
                vo = "libmpv"
            else:
                vo = "gpu"
            self.mpv_player = mpv.MPV(
                wid=wid,
                vo=vo,
                input_default_bindings=True,
                input_vo_keyboard=True,
                osc=True,
                ytdl=False,
                log_handler=self._mpv_log_handler,
                loglevel="info",
            )
            self.mpv_player.volume = self.volume_scale.get()
            self._log(f"mpv zainicjalizowany (vo={vo}, wid={wid}).", "success")
        except Exception as e:
            self._log(f"Błąd inicjalizacji mpv (vo={vo}): {e}", "error")
            # Fallback: try without wid embedding
            try:
                self.mpv_player = mpv.MPV(
                    input_default_bindings=True,
                    input_vo_keyboard=True,
                    osc=True,
                    ytdl=False,
                    log_handler=self._mpv_log_handler,
                    loglevel="info",
                )
                self.mpv_player.volume = self.volume_scale.get()
                self._log("mpv zainicjalizowany (tryb okienkowy).", "success")
            except Exception as e2:
                self._log(f"Błąd inicjalizacji mpv (fallback): {e2}", "error")
                self.mpv_player = None

    def _mpv_log_handler(self, loglevel, component, message):
        """Capture mpv internal log messages."""
        try:
            # Suppress ytdl_hook noise (ytdl is disabled but just in case)
            if component in ("ytdl_hook", "ytdl_hook/") or "ytdl" in message.lower():
                return
            if loglevel in ("error", "fatal"):
                # Detect HTTP 459 (IPTV server overload / token expired)
                if "459" in message or "http error" in message.lower():
                    self._log_safe(
                        f"mpv [{component}]: {message} — resetuję token...", "warning"
                    )
                    self.player_token = None  # Force re-handshake on next play
                    return
                self._log_safe(f"mpv [{component}]: {message}", "error")
            elif loglevel == "warn":
                self._log_safe(f"mpv [{component}]: {message}", "warning")
        except Exception:
            pass

    def _ensure_mpv(self):
        """Lazy-init mpv when first needed (needs visible window)."""
        if not HAS_MPV:
            return False
        if self.mpv_player is None:
            self._init_mpv()
        return self.mpv_player is not None

    def _mpv_play_url(self, stream_url, portal_url=None, mac=None):
        if not self._ensure_mpv():
            return False
        try:
            # Set HTTP headers matching Stalker Portal / MAG STB device
            ua = (
                "Mozilla/5.0 (QtEmbedded; U; Linux; C) "
                "AppleWebKit/533.3 (KHTML, like Gecko) "
                "MAG200 stbapp ver: 4 rev: 2116 Mobile Safari/533.3"
            )
            headers = [
                f"User-Agent: {ua}",
                "X-User-Agent: Model: MAG250; Link: Ethernet",
            ]
            if portal_url:
                headers.append(f"Referer: {portal_url}")
            if mac:
                import hashlib as _hl

                sn = _hl.md5(mac.encode()).hexdigest()
                cookie = f"mac={mac}; sn={sn}; stb_lang=en; timezone=Europe/Amsterdam"
                headers.append(f"Cookie: {cookie}")
            try:
                self.mpv_player["http-header-fields"] = headers
            except Exception:
                pass
            self._log(f"mpv.play({stream_url[:80]}...)", "info")
            self.mpv_player.play(stream_url)
            return True
        except Exception as e:
            self._log_safe(f"mpv play error: {e}", "error")
            return False

    def _player_play_pause(self):
        if self.mpv_player:
            try:
                paused = self.mpv_player.pause
                self.mpv_player.pause = not paused
                self.play_pause_btn.configure(text="▶" if not paused else "⏸")
            except Exception:
                self._play_selected_channel()
        else:
            self._play_selected_channel()

    def _player_stop(self):
        if self.mpv_player:
            try:
                self.mpv_player.stop()
            except Exception:
                pass
        self.play_pause_btn.configure(text="▶")
        self.player_status_label.configure(text="Zatrzymano")
        self.current_stream_url = None

    def _player_prev(self):
        sel = self.channel_tree.selection()
        if not sel:
            return
        idx = self.channel_tree.index(sel[0])
        if idx > 0:
            children = self.channel_tree.get_children()
            self.channel_tree.selection_set(children[idx - 1])
            self.channel_tree.see(children[idx - 1])
            self._play_selected_channel()

    def _player_next(self):
        sel = self.channel_tree.selection()
        if not sel:
            children = self.channel_tree.get_children()
            if children:
                self.channel_tree.selection_set(children[0])
                self._play_selected_channel()
            return
        idx = self.channel_tree.index(sel[0])
        children = self.channel_tree.get_children()
        if idx < len(children) - 1:
            self.channel_tree.selection_set(children[idx + 1])
            self.channel_tree.see(children[idx + 1])
            self._play_selected_channel()

    def _on_volume_change(self, val):
        if self.mpv_player:
            try:
                self.mpv_player.volume = int(float(val))
            except Exception:
                pass

    def _player_fullscreen(self):
        if self.mpv_player:
            try:
                self.mpv_player.fullscreen = not bool(self.mpv_player.fullscreen)
                return
            except Exception:
                pass
        is_fs = self.root.attributes("-fullscreen")
        self.root.attributes("-fullscreen", not is_fs)

    # ══════════════════════════════════════════════════════
    #  PLAY / STREAM — uses URL directly
    # ══════════════════════════════════════════════════════

    def _play_selected_channel(self):
        sel = self.channel_tree.selection()
        if not sel:
            self._log("Zaznacz kanał do odtworzenia.", "warning")
            return
        ch = self._get_channel_for_tree_item(sel[0])
        if ch is None:
            self._log("Nie znaleziono kanału.", "error")
            return
        self._play_channel_entry(ch)

    def _extract_stream_url_from_cmd(self, cmd: str) -> Optional[str]:
        if not cmd:
            return None
        raw = str(cmd).strip()
        if raw.startswith("ffmpeg "):
            raw = raw[7:].strip()
        for token in raw.replace('"', " ").split():
            if token.startswith(("http://", "https://")):
                return token.strip()
        if raw.startswith(("http://", "https://")):
            return raw
        return None

    def _is_suspicious_stream_url(self, stream_url: Optional[str]) -> bool:
        if not stream_url:
            return True
        try:
            parsed = urlparse(stream_url)
            path = (parsed.path or "").lower()
            if (
                path.endswith(("/movie.php", "/live.php", "/series.php"))
                and not parsed.query
            ):
                return True
        except Exception:
            return False
        return False

    def _resolve_stream_url(self, url, mac, cmd, timeout, proxy):
        ctype = self.player_content_type
        stream_url = get_stream_url(
            url,
            mac,
            self.player_token,
            cmd,
            content_type=ctype,
            timeout=timeout,
            proxy=proxy,
        )

        # If portal returns incomplete URL (e.g. /movie.php without query),
        # try alternate content types first.
        if self._is_suspicious_stream_url(stream_url):
            for alt in ("itv", "vod", "series"):
                if alt == ctype:
                    continue
                alt_url = get_stream_url(
                    url,
                    mac,
                    self.player_token,
                    cmd,
                    content_type=alt,
                    timeout=timeout,
                    proxy=proxy,
                )
                if alt_url and not self._is_suspicious_stream_url(alt_url):
                    self._log_safe(f"Użyto alternatywnego typu streamu: {alt}", "info")
                    stream_url = alt_url
                    break

        # Final fallback: parse direct URL from cmd
        if self._is_suspicious_stream_url(stream_url):
            cmd_url = self._extract_stream_url_from_cmd(cmd)
            if cmd_url:
                self._log_safe("Fallback: używam URL bezpośrednio z cmd.", "warning")
                stream_url = cmd_url

        return stream_url

    def _play_channel_entry(self, ch):
        cmd = ch.get("cmd", "")
        name = ch.get("name", ch.get("o_name", "?"))
        if not cmd:
            self._log(f"Brak strumienia: {name}", "error")
            return

        self._log(f"Odtwarzanie: {name}...", "info")
        self.player_status_label.configure(text=f"▶ {name}")
        self.play_pause_btn.configure(text="⏸")
        self._set_progress(30, f"Odtwarzanie: {name}")

        threading.Thread(
            target=self._play_stream_worker, args=(cmd, name), daemon=True
        ).start()

    def _play_stream_worker(self, cmd, name):
        try:
            mac, url_raw, proxy = self._get_player_mac_url_proxy()
            if not mac or not url_raw:
                self._log_safe("Brak MAC/URL. Wybierz profil.", "error")
                return

            timeout = self._get_timeout()
            url = parse_url(url_raw)

            if not self.player_token:
                self._log_safe("Brak tokena, wykonuję handshake...", "info")
                self.player_token, _ = get_handshake(
                    url, mac, timeout=timeout, proxy=proxy
                )
            if not self.player_token:
                self._log_safe("Nie udało się uzyskać tokena.", "error")
                self._set_progress(100, "Błąd")
                self._set_mac_status(mac, "red")
                return

            self._log_safe(f"Pobieranie URL streamu (cmd={cmd[:60]})...", "info")
            stream_url = self._resolve_stream_url(url, mac, cmd, timeout, proxy)
            if not stream_url:
                # Token might have expired, retry with fresh handshake
                self._log_safe("Brak URL — próba z nowym tokenem...", "warning")
                self.player_token, _ = get_handshake(
                    url, mac, timeout=timeout, proxy=proxy
                )
                if self.player_token:
                    stream_url = self._resolve_stream_url(url, mac, cmd, timeout, proxy)
            if not stream_url:
                self._log_safe(f"Nie udało się pobrać URL: {name}", "error")
                self._set_progress(100, "Błąd")
                self._set_mac_status(mac, "red")
                return

            self._log_safe(f"Stream: {stream_url}", "success")
            self._set_progress(100, f"▶ {name}")
            self.current_stream_url = stream_url
            # Mark MAC green — stream URL obtained successfully
            self._set_mac_status(mac, "green")

            # Play in embedded mpv on UI thread
            self.root.after(0, self._play_stream_on_ui, stream_url, url, mac)
        except Exception as e:
            self._log_safe(f"Błąd odtwarzania: {e}", "error")
            self._set_progress(100, "Błąd")
            # Try to mark MAC red on exception
            try:
                mac, _, _ = self._get_player_mac_url_proxy()
                if mac:
                    self._set_mac_status(mac, "red")
            except Exception:
                pass

    def _play_stream_on_ui(self, stream_url, portal_url=None, mac=None):
        ok = self._mpv_play_url(stream_url, portal_url=portal_url, mac=mac)
        if ok:
            self._log("Odtwarzanie w wbudowanym mpv.", "success")
        else:
            self._log(
                "mpv niedostępny — próba otwarcia w zewnętrznym playerze...", "warning"
            )
            self._open_stream_external(stream_url)

    def _open_stream_external(self, stream_url):
        """Fallback: open stream in external player (mpv/VLC/browser)."""
        try:
            # Try external mpv first
            mpv_exe = shutil.which("mpv")
            if mpv_exe:
                subprocess.Popen(
                    [mpv_exe, stream_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._log("Otwarto w zewnętrznym mpv.", "success")
                return
            # Try VLC
            vlc_exe = shutil.which("vlc")
            if not vlc_exe and sys.platform == "win32":
                for p in (
                    os.environ.get("ProgramFiles", ""),
                    os.environ.get("ProgramFiles(x86)", ""),
                ):
                    c = os.path.join(p, "VideoLAN", "VLC", "vlc.exe")
                    if os.path.isfile(c):
                        vlc_exe = c
                        break
            if vlc_exe:
                subprocess.Popen(
                    [vlc_exe, stream_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._log("Otwarto w VLC.", "success")
                return
            # Last resort: copy to clipboard
            self.root.clipboard_clear()
            self.root.clipboard_append(stream_url)
            self._log(
                f"Brak zewnętrznego playera. URL skopiowany do schowka.", "warning"
            )
        except Exception as e:
            self._log(f"Błąd otwierania zewnętrznego playera: {e}", "error")

    def _copy_channel_url(self):
        sel = self.channel_tree.selection()
        if not sel:
            self._log("Zaznacz kanał.", "warning")
            return
        ch = self._get_channel_for_tree_item(sel[0])
        if ch is None:
            self._log("Nie znaleziono kanału.", "error")
            return
        cmd = ch.get("cmd", "")
        name = ch.get("name", ch.get("title", "?"))
        if not cmd:
            self._log(f"Brak strumienia: {name}", "error")
            return
        self._log(f"Pobieranie URL: {name}...", "info")
        threading.Thread(
            target=self._copy_url_worker, args=(cmd, name), daemon=True
        ).start()

    def _copy_url_worker(self, cmd, name):
        mac, url_raw, proxy = self._get_player_mac_url_proxy()
        if not mac or not url_raw:
            self._log_safe("Brak MAC/URL.", "error")
            return
        timeout = self._get_timeout()
        url = parse_url(url_raw)
        if not self.player_token:
            self.player_token, _ = get_handshake(url, mac, timeout=timeout, proxy=proxy)
        if not self.player_token:
            return
        stream_url = self._resolve_stream_url(url, mac, cmd, timeout, proxy)
        if not stream_url:
            self._log_safe(f"Nie udało się pobrać URL: {name}", "error")
            return
        self.root.after(0, lambda: self._do_copy(stream_url, name))

    def _do_copy(self, url, name):
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._log(f"Skopiowano URL: {name} → {url}", "success")

    # ══════════════════════════════════════════════════════
    #  SCANNING (with full proxy retry)
    # ══════════════════════════════════════════════════════

    def _toggle_start(self):
        if self.is_running:
            return
        self._start_scan()

    def _start_scan(self):
        url_raw = self.url_entry.get().strip()
        mac_prefix = self.mac_entry.get().strip()
        workers_str = self.workers_entry.get().strip()
        timeout = self._get_timeout()

        if not url_raw:
            self._log("Podaj adres URL serwera!", "error")
            return
        if len(mac_prefix) < 8:
            self._log("Pierwsze 3 bajty MAC: XX:XX:XX", "error")
            return

        try:
            workers = int(workers_str)
        except ValueError:
            workers = 10

        self.is_running = True
        self.is_paused = False
        self.stop_event.clear()
        self.pause_event.set()
        self.checked_count = 0
        self.found_count = 0
        self._update_stats()

        self._btn_disable(self.start_btn)
        self._btn_enable(self.pause_btn)
        self._btn_enable(self.stop_btn)
        self._set_status("Uruchamianie...", "#ffaa00")
        self._set_progress(5, "Uruchamianie skanera...")

        server_address = parse_url(url_raw)
        self.scan_thread = threading.Thread(
            target=self._scan_worker,
            args=(server_address, mac_prefix, workers, timeout),
            daemon=True,
        )
        self.scan_thread.start()

    def _scan_worker(self, server_address, mac_prefix, workers, timeout):
        # Pre-scan: ensure proxies are available
        if not get_proxy_list():
            self._log_safe("Brak proxy — automatyczne pobieranie...", "info")
            self._set_status("Pobieranie proxy...", "#55aaff")
            self._set_progress(5, "Pobieranie proxy...")
            self._fetch_only_worker()

            if self.stop_event.is_set():
                self._scan_finished()
                return

            if not get_proxy_list():
                self._log_safe(
                    "❌ Nie udało się pobrać proxy. Skanowanie wymaga proxy!", "error"
                )
                self._set_progress(100, "Brak proxy")
                self._scan_finished()
                return

        self._log_safe(
            f"Szukam endpoint-u na {server_address} "
            f"(proxy: {len(get_proxy_list())})...",
            "info",
        )
        self._set_status("Szukanie endpoint-u...", "#55aaff")
        self._set_progress(15, "Szukanie endpoint-u...")

        # Use the full proxy retry helper
        endpoint, proxy = self._find_endpoint_with_proxy_retry(server_address, timeout)

        if self.stop_event.is_set():
            self._scan_finished()
            return

        if not endpoint:
            self._set_progress(100, "Serwer nie odpowiada")
            self._scan_finished()
            return

        url = server_address + endpoint
        self._log_safe(f"Endpoint: {url}", "success")
        if proxy:
            self._log_safe(f"Proxy: {proxy}", "info")
        self._set_status("Skanowanie...", "#00ff88")
        self._set_progress(30, "Skanowanie...")

        self.executor = ThreadPoolExecutor(max_workers=workers)
        futures = []
        try:
            while not self.stop_event.is_set():
                self.pause_event.wait()
                if self.stop_event.is_set():
                    break
                for _ in range(workers * 2):
                    if self.stop_event.is_set():
                        break
                    futures.append(
                        self.executor.submit(
                            self._check_single_mac, url, mac_prefix, timeout
                        )
                    )
                remaining = []
                for f in futures:
                    if f.done():
                        try:
                            f.result()
                        except Exception:
                            pass
                    else:
                        remaining.append(f)
                futures = remaining
                time.sleep(0.05)
        except Exception as e:
            self._log_safe(f"Błąd: {e}", "error")
        finally:
            if self.executor:
                try:
                    self.executor.shutdown(wait=True, cancel_futures=True)
                except Exception:
                    pass
                self.executor = None
            self._scan_finished()

    def _check_single_mac(self, url, mac_prefix, timeout):
        if self.stop_event.is_set():
            return
        self.pause_event.wait()

        mac = generate_random_mac(mac_prefix)

        # Multi-proxy: each worker thread gets its own proxy
        if self.multi_proxy_var.get():
            thread_id = threading.current_thread().ident
            # Use thread-local-like proxy assignment via multiproxy helper
            proxy = get_proxy_for_multiproxy(
                exclude=getattr(self, "_multiproxy_used", set())
            )
            if proxy:
                if not hasattr(self, "_multiproxy_used"):
                    self._multiproxy_used = set()
                # Don't permanently exclude — just prefer different ones
        else:
            proxy = self._get_active_proxy()

        if not proxy:
            proxy = self._get_active_proxy()

        result = check_mac(url, mac, timeout=timeout, proxy=proxy)
        codes = result.get("codes", [])
        # Show only the last (most relevant) HTTP code
        last_code = codes[-1] if codes else "?"
        elapsed = result.get("elapsed_ms", 0)
        time_tag = f"{elapsed:.1f}ms"
        error_msg = result.get("error", "")

        self.checked_count += 1
        self._update_stats_safe()

        # Verbose logging
        if self.verbose_logs_var.get():
            req_info = result.get("request_info", "")
            res_info = result.get("response_info", "")
            if req_info:
                self._log_safe(f"  ➡ {req_info}", "dim")
            if res_info:
                self._log_safe(f"  ⬅ {res_info[:300]}", "dim")

        # Handle 429 rate limit
        for code in codes:
            if code == 429 and proxy:
                mark_proxy_rate_limited(proxy)
                self._log_safe(
                    f"⚠ [{code}] {time_tag} Proxy rate-limited: {proxy}", "warning"
                )
                self.root.after(0, self._refresh_proxy_tree)
                self._save_proxy_state()
                self._schedule_rate_limit_recheck()
                new_proxy = rotate_proxy()
                if new_proxy:
                    self._log_safe(f"Zmiana proxy → {new_proxy}", "info")
                return

        # Timeout → remove proxy and rotate
        if error_msg == "Timeout" and proxy:
            self._log_safe(f"⏱ Timeout {time_tag} → usuwam proxy: {proxy}", "warning")
            set_proxy_tag(proxy, PROXY_TAG_DEAD)
            remove_proxy(proxy)
            self.root.after(0, self._refresh_proxy_tree)
            new_proxy = rotate_proxy()
            if new_proxy:
                self._log_safe(f"Zmiana proxy → {new_proxy}", "info")
            return

        if result["found"]:
            if proxy:
                report_proxy_success(proxy)
                set_proxy_tag(proxy, PROXY_TAG_WORKING)

            # Check channel count
            ch_count = 0
            try:
                ch_count = count_channels_quick(url, mac, timeout=timeout, proxy=proxy)
            except Exception:
                pass

            # Min channels filter
            try:
                min_ch = int(self.min_channels_entry.get().strip())
            except (ValueError, AttributeError):
                min_ch = 0

            if min_ch > 0 and ch_count < min_ch:
                self._log_safe(
                    f"⚠ [{last_code}] {time_tag} {mac} → "
                    f"{ch_count} kanałów (min: {min_ch}), pomijam",
                    "warning",
                )
                return

            self.found_count += 1
            self._update_stats_safe()
            self._log_safe(
                f"✅ [{last_code}] {time_tag} ZNALEZIONO: {mac} → "
                f"{result['expiry']} ({ch_count} kanałów)",
                "success",
            )
            self._add_active_mac(url, mac, result["expiry"], proxy, channels=ch_count)
            self._auto_save()
        else:
            # Handle proxy failures for bad codes
            for code in codes:
                if code and should_remove_proxy(code) and proxy:
                    self._handle_proxy_fail(proxy, code)
                    break

            if self.checked_count % 25 == 0:
                self._log_safe(
                    f"[{last_code}] {time_tag} Sprawdzono "
                    f"{self.checked_count}, "
                    f"znaleziono {self.found_count}...",
                    "info",
                )
            else:
                self._log_safe(f"[{last_code}] {time_tag} {mac}", "dim")

    def _scan_finished(self):
        # Avoid duplicate reset if already stopped manually
        if not self.is_running and not self.is_paused:
            return
        self.is_running = False
        self.is_paused = False
        self._set_status("Zakończono", "#888888")
        self._set_progress(100, "Skanowanie zakończone")
        self.root.after(0, self._reset_buttons)
        self._log_safe(
            f"Zakończono. Sprawdzono: {self.checked_count}, "
            f"Znaleziono: {self.found_count}",
            "info",
        )
        self._auto_save()

    def _reset_buttons(self):
        self._btn_enable(self.start_btn)
        self._btn_disable(self.pause_btn)
        self.pause_btn.configure(text="⏸ PAUZA")
        self._btn_disable(self.stop_btn)

    def _toggle_pause(self):
        if not self.is_running:
            return
        if self.is_paused:
            self.is_paused = False
            self.pause_event.set()
            self.pause_btn._normal_bg = "#c78d00"
            self.pause_btn._hover_bg = "#a87600"
            self.pause_btn.configure(text="⏸ PAUZA", bg="#c78d00")
            self._set_status("Skanowanie...", "#00ff88")
            self._log("▶  Wznowiono.", "info")
        else:
            self.is_paused = True
            self.pause_event.clear()
            self.pause_btn._normal_bg = "#00b359"
            self.pause_btn._hover_bg = "#009945"
            self.pause_btn.configure(text="▶ WZNÓW", bg="#00b359")
            self._set_status("Wstrzymano", "#ffaa00")
            self._log("⏸  Pauza.", "warning")

    def _stop_scan(self):
        if not self.is_running:
            return
        self._log("⏹  Zatrzymywanie...", "warning")
        self.stop_event.set()
        self.pause_event.set()
        # Immediately reset state and buttons
        self.is_running = False
        self.is_paused = False
        self._set_status("Zatrzymano", "#888888")
        self.root.after(0, self._reset_buttons)

    # ══════════════════════════════════════════════════════
    #  CLOSE
    # ══════════════════════════════════════════════════════

    def _on_close(self):
        if self._is_closing:
            return
        self._is_closing = True

        self.stop_event.set()
        self.pause_event.set()
        # Stop proxy testing if running
        self._proxy_stop.set()
        self._proxy_paused.set()
        # Stop deep proxy testing if running
        self._deep_proxy_stop.set()
        self._deep_proxy_paused.set()

        if self.executor:
            try:
                self.executor.shutdown(wait=True, cancel_futures=True)
            except Exception:
                pass
            self.executor = None

        if self.scan_thread and self.scan_thread.is_alive():
            try:
                self.scan_thread.join(timeout=2)
            except Exception:
                pass

        if self.mpv_player:
            try:
                self.mpv_player.terminate()
            except Exception:
                pass
            self.mpv_player = None

        self._save_session()
        self._auto_save()
        self.root.quit()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = App()
    app.run()
