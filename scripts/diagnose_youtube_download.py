#!/usr/bin/env python3
"""YouTube REAL download diagnostic using EXACTLY the app's options.

Uses the app's own StreamDownloader so the code path is identical to the
worker. Prints the format selector, ffmpeg path, outtmpl, yt-dlp result and
validates the final output. On failure the FULL traceback (and __cause__
chain) is printed — nothing is translated to a generic NetworkError.

No GUI. One authorized public YouTube video. download=True.
"""

import os
import shutil
import sys
import tempfile
import traceback

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.providers.common import yt_adapter  # noqa: E402

os.environ.setdefault("YTDLP_JS_RUNTIME", "")

URL = os.environ.get("YT_URL", "https://www.youtube.com/watch?v=JGwWNGJdvx8")
QUALITY = "Best Available"
OUTPUT_FORMAT = "mp4"


def main():
    print(f"URL: {URL}")
    print(f"quality: {QUALITY}")
    print(f"output_format: {OUTPUT_FORMAT}")

    selector = yt_adapter._map_format_selector(QUALITY, OUTPUT_FORMAT)
    merge_ext = yt_adapter._merge_ext(OUTPUT_FORMAT)
    ffmpeg = shutil.which("ffmpeg") or ""
    print(f"format selector: {selector!r}")
    print(f"merge_output_format: {merge_ext!r}")
    print(f"ffmpeg path: {ffmpeg or 'NOT FOUND'}")

    outdir = tempfile.mkdtemp(prefix="yt_real_")
    filename = "diagnose_youtube.mp4"
    target = str(Path(outdir) / filename)
    print(f"outtmpl (target): {target!r}")

    downloader = yt_adapter.StreamDownloader(
        url=URL,
        output_dir=outdir,
        filename=filename,
        output_format=OUTPUT_FORMAT,
        quality=QUALITY,
        embed_metadata=False,
        ffmpeg_path=ffmpeg,
    )

    try:
        returned = downloader.download()
        print(f"yt-dlp result returned path: {returned!r}")
        p = Path(returned)
        print(f"file exists: {p.exists()}")
        if p.exists():
            print(f"regular file: {p.is_file()}")
            print(f"file size: {p.stat().st_size} bytes")
            print(f"final extension: {p.suffix}")
    except Exception as exc:  # noqa: BLE001
        print("FULL TRACEBACK:")
        traceback.print_exc()
        cause = exc.__cause__
        depth = 0
        while cause is not None and depth < 6:
            print(f"__cause__[{depth}] {type(cause).__name__}: {cause}")
            cause = cause.__cause__
            depth += 1
        print(f"\nFINAL EXCEPTION TYPE: {type(exc).__name__}")
        print(f"FINAL EXCEPTION: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()