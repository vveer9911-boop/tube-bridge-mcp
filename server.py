import asyncio
import glob
import os
import re
import subprocess
import tempfile
import uvicorn
from starlette.responses import JSONResponse

import tube_bridge.tools as tools

# 1. Fast, robust metadata extraction via ytsearch
async def _fast_video_info(video_id: str) -> dict:
    cached = tools.cache.get_video_info(video_id)
    if cached:
        return cached
    items, err = tools.yt.run_ytdlp_multi([
        f"ytsearch1:{video_id}",
        "--dump-json",
    ], timeout=15)
    if items:
        info = tools.yt.parse_video_info(items[0])
        res = info.to_dict()
        tools.cache.set_video_info(video_id, res)
        return res
    return await tools._orig_video_info(video_id)

tools._orig_video_info = tools.video_info
tools.video_info = _fast_video_info

# 2. Cloud-compatible transcript extraction via yt-dlp (bypasses YouTube 403 blocks)
async def _robust_transcript(video_id: str, lang: str | None, with_timestamps: bool = False) -> dict:
    try:
        return await tools._orig_transcript(video_id, lang, with_timestamps)
    except Exception:
        pass

    def _fetch_subs():
        url = f"https://youtube.com/watch?v={video_id}"
        target_lang = lang or "en"
        with tempfile.TemporaryDirectory(prefix="yt-sub-") as tmpdir:
            cmd = [
                "yt-dlp", "--skip-download",
                "--write-sub", "--write-auto-sub",
                "--sub-lang", f"{target_lang}.*,{target_lang}",
                "--sub-format", "vtt",
                "--output", os.path.join(tmpdir, "sub.%(ext)s"),
                "--extractor-args", "youtube:player_client=android,web",
                url
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            files = glob.glob(os.path.join(tmpdir, "*.vtt"))
            if not files:
                raise RuntimeError(f"No transcript or captions found for video {video_id}")
            with open(files[0], "r", encoding="utf-8", errors="replace") as f:
                vtt_text = f.read()

        lines = []
        seen = set()
        for block in vtt_text.split("\n\n"):
            m = re.search(r"(\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->", block)
            if m:
                t = m.group(1).split(".")[0]
                text_lines = [l.strip() for l in block.splitlines() if "-->" not in l and not l.strip().isdigit() and l.strip()]
                clean_text = " ".join(text_lines)
                clean_text = re.sub(r"<[^>]+>", "", clean_text).strip()
                if clean_text and clean_text not in seen:
                    seen.add(clean_text)
                    if with_timestamps:
                        lines.append(f"[{t}] {clean_text}")
                    else:
                        lines.append(clean_text)

        output = "\n".join(lines) if with_timestamps else " ".join(lines)
        return {
            "video_id": video_id,
            "language": target_lang,
            "is_generated": True,
            "segment_count": len(lines),
            "with_timestamps": with_timestamps,
            "text": output
        }

    return await asyncio.to_thread(_fetch_subs)

tools._orig_transcript = tools.transcript
tools.transcript = _robust_transcript

from tube_bridge.server import server
from tube_bridge.transport import create_app

port = int(os.environ.get("PORT", 8080))
host = "0.0.0.0"

app = create_app(server, host, port)

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port, log_level="info")
