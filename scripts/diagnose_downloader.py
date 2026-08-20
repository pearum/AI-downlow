#!/usr/bin/env python3
"""ROOT-CAUSE DIAGNOSTIC for the video downloader.

Tests yt-dlp DIRECTLY (no app wrappers) so that real exceptions, full
tracebacks, JS runtime behaviour and HTTP client configuration are captured
without anything being translated or masked.

- No downloads (extract_info(download=False) only).
- No cookies, no stolen sessions, no auth bypass.
"""

import os
import platform
import shutil
import subprocess
import sys
import traceback

from pathlib import Path

# Add the project root to sys.path (harmless; no app imports below).
sys.path.insert(0, str(Path(__file__).parent.parent))

import yt_dlp  # noqa: E402


def _run(cmd):
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=20
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:  # noqa: BLE001
        return -1, "", str(e)


def pip_show(pkg):
    rc, out, err = _run(f'"{sys.executable}" -m pip show {pkg}')
    if rc == 0:
        for line in out.splitlines():
            if line.lower().startswith("version"):
                return line.split(":", 1)[1].strip()
    return None


def exe_info(name):
    path = shutil.which(name)
    if not path:
        return None
    rc, ver, _ = _run(f'"{path}" --version')
    return {"path": path, "version": ver or "?"}


def extract(url, extra_opts=None):
    """Run yt_dlp extraction directly. Returns (ok, label)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
    }
    if extra_opts:
        opts.update(extra_opts)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        title = str(info.get("title") or "?")
        return True, f"SUCCESS title={title[:60]!r}"
    except yt_dlp.utils.DownloadError as exc:
        return False, (
            "EXCEPTION TYPE: yt_dlp.utils.DownloadError\n"
            f"EXCEPTION: {exc}\n"
            f"FULL TRACEBACK:\n{traceback.format_exc()}"
        )
    except Exception as exc:  # noqa: BLE001
        return False, (
            f"EXCEPTION TYPE: {type(exc).__name__}\n"
            f"EXCEPTION: {exc}\n"
            f"FULL TRACEBACK:\n{traceback.format_exc()}"
        )


YOUTUBE_WORKING = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
YOUTUBE_FAILED_IN_APP = "https://www.youtube.com/watch?v=JGwWNGJdvx8"
TIKTOK_WORKING = (
    "https://www.tiktok.com/@scout2015/video/7675531803725253918"
)
TIKTOK_BLOCKED_IN_APP = (
    "https://www.tiktok.com/@washingtonpost/video/7309051406074473761"
)


def main():
    print("=== ENVIRONMENT ===")
    print(f"Python: {platform.python_version()}")
    print(f"Python executable: {sys.executable}")
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Architecture: {platform.machine()}")
    print(f"yt-dlp: {pip_show('yt-dlp') or 'NOT FOUND'}")
    print(f"yt-dlp-ejs: {pip_show('yt-dlp-ejs') or 'NOT FOUND'}")
    print(f"curl_cffi: {pip_show('curl_cffi') or 'NOT FOUND'}")
    print(f"PySide6: {pip_show('PySide6') or 'NOT FOUND'}")

    ffmpeg = exe_info("ffmpeg")
    deno = exe_info("deno")
    node = exe_info("node")
    print("\nFFmpeg:")
    if ffmpeg:
        print(f"  path: {ffmpeg['path']}")
        print(f"  version: {ffmpeg['version']}")
    else:
        print("  MISSING")
    print("Deno:")
    if deno:
        print(f"  path: {deno['path']}")
        print(f"  version: {deno['version']}")
    else:
        print("  MISSING")
    print("Node:")
    if node:
        print(f"  path: {node['path']}")
        print(f"  version: {node['version']}")
    else:
        print("  MISSING")

    print("\n=== IMPORTS ===")
    try:
        import yt_dlp as ytd  # noqa: F401
        print(f"yt-dlp version: {yt_dlp.version.__version__}")
        print(f"yt-dlp module path: {yt_dlp.__file__}")
    except Exception as e:  # noqa: BLE001
        print(f"yt-dlp IMPORT FAIL: {type(e).__name__}: {e}")
    try:
        import curl_cffi  # noqa: F401
        print(f"curl_cffi import: OK ({curl_cffi.__file__})")
    except Exception as e:  # noqa: BLE001
        print(f"curl_cffi IMPORT FAIL: {type(e).__name__}: {e}")

    print("\n=== YOUTUBE EXTRACTION (DIRECT yt-dlp, download=False) ===")
    for label, url in (
        ("YouTube working (jNQXAC9IVRw)", YOUTUBE_WORKING),
        ("YouTube failed-in-app (JGwWNGJdvx8)", YOUTUBE_FAILED_IN_APP),
    ):
        print(f"\n--- {label}\n{url}")
        ok, detail = extract(url)
        print(detail)

    print("\n=== TIKTOK EXTRACTION (DIRECT yt-dlp, download=False) ===")
    for label, url in (
        ("TikTok working (@scout2015)", TIKTOK_WORKING),
        ("TikTok blocked-in-app (@washingtonpost)", TIKTOK_BLOCKED_IN_APP),
    ):
        print(f"\n--- {label}\n{url}")
        ok, detail = extract(url)
        print(detail)

    print("\n=== JS RUNTIME TEST (YouTube, download=False) ===")
    js_runtimes = {}
    if deno:
        js_runtimes["deno"] = {"path": deno["path"]}
    if node:
        js_runtimes["node"] = {"path": node["path"]}

    ok, detail = extract(
        YOUTUBE_WORKING, {"js_runtimes": js_runtimes}
    )
    print(f"A. WITHOUT js_runtimes+remote_components (default): "
          f"{'SUCCESS' if ok else 'FAIL'}\n{detail}\n" if not ok else
          f"A. WITHOUT js_runtimes+remote_components (default): SUCCESS")
    if deno:
        ok, detail = extract(
            YOUTUBE_WORKING,
            {"js_runtimes": {"deno": {"path": deno["path"]}}},
        )
        print(f"B. WITH Deno: {'SUCCESS' if ok else 'FAIL'}\n"
              + (detail if not ok else ""))
    if node:
        ok, detail = extract(
            YOUTUBE_WORKING,
            {"js_runtimes": {"node": {"path": node["path"]}}},
        )
        print(f"C. WITH Node: {'SUCCESS' if ok else 'FAIL'}\n"
              + (detail if not ok else ""))

    print("\n=== CURL_CFFI HTTP CLIENT TEST (documented impersonate) ===")
    print("No cookies used. Documented yt-dlp option: impersonate=chrome")
    ok, detail = extract(YOUTUBE_WORKING, {"impersonate": "chrome"})
    print(f"YouTube + impersonate=chrome: {'SUCCESS' if ok else 'FAIL'}\n"
          + (detail if not ok else ""))


if __name__ == "__main__":
    main()