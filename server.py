import asyncio
import glob
import os
import re
import subprocess
import sys
import tempfile
import uvicorn
from starlette.responses import JSONResponse, FileResponse

from tube_bridge.server import server
import tube_bridge.tools as tools
tbs_mod = sys.modules["tube_bridge.server"]

# 1. Fast metadata extraction via ytsearch
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
    return await tools.video_info(video_id)

tbs_mod.video_info = _fast_video_info

# 2. Robust transcript extraction via yt-dlp android client (bypasses YouTube 403 blocks)
async def _robust_transcript(video_id: str, lang: str | None = None, with_timestamps: bool = False) -> dict:
    def _fetch_subs():
        url = f"https://youtube.com/watch?v={video_id}"
        target_lang = lang or "en"
        with tempfile.TemporaryDirectory(prefix="yt-sub-") as tmpdir:
            cmd = [
                "yt-dlp", "--skip-download",
                "--write-sub", "--write-auto-sub",
                "--sub-lang", f"{target_lang}.*,{target_lang}",
                "--sub-format", "vtt/srt/best",
                "--output", os.path.join(tmpdir, "sub.%(ext)s"),
                "--extractor-args", "youtube:player_client=android",
                url
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            files = [f for f in glob.glob(os.path.join(tmpdir, "*")) if not f.endswith(".ytdl") and not f.endswith(".part")]
            if not files:
                raise RuntimeError(f"yt-dlp exit {res.returncode}: {res.stderr.strip()[-300:]}")
            
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

tbs_mod.transcript = _robust_transcript











import time
import base64
import uuid
import contextlib
from mcp.types import Tool, CallToolResult, ImageContent, TextContent
from starlette.responses import FileResponse, JSONResponse
from tube_bridge.youtube.client import extract_video_id
import extended_tools as ext_tools

public_dir = os.path.join(tempfile.gettempdir(), "tube_bridge_public")
os.makedirs(public_dir, exist_ok=True)

# --- OVERRIDE TOOLS ---
new_tools = [t for t in tbs_mod.TOOL_CATALOG if t.name not in ("download_video_segment", "analyze_video_frames", "watch_video")]

watch_video_tool = Tool(
    name="watch_video",
    description="Watch a YouTube video by extracting frames at intervals and burning visual timestamps on them. Returns frames inline (for ChatGPT vision) and temporary HTTP links.",
    inputSchema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "YouTube video URL or ID"},
            "start_time": {"type": "string", "description": "Start timestamp (e.g. '00:00', '01:15' or seconds '75'). Default 0.", "default": "0"},
            "duration": {"type": "integer", "description": "Duration to watch in seconds. Default 30, max 120.", "default": 30},
            "interval_seconds": {"type": "number", "description": "Seconds between frames. Default 5.0.", "default": 5.0}
        },
        "required": ["url"]
    }
)

download_segment_tool = Tool(
    name="download_video_segment",
    description="Download a specific trimmed section of a YouTube video as MP4. Returns a temporary HTTP link (expires in 5 mins) for the user to download to their device.",
    inputSchema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "YouTube video URL or ID"},
            "start_time": {"type": "string", "description": "Start timestamp (e.g. '01:15')"},
            "end_time": {"type": "string", "description": "End timestamp (e.g. '02:30')"},
        },
        "required": ["url", "start_time", "end_time"]
    }
)

new_tools.extend([watch_video_tool, download_segment_tool])
tbs_mod.TOOL_CATALOG = tuple(new_tools)

server = tbs_mod.server

async def _do_download_video_segment(args: dict) -> CallToolResult:
    video_id = extract_video_id(args["url"])
    start_time = args["start_time"]
    end_time = args["end_time"]
    
    download_dir = os.path.join(public_dir, "downloads")
    os.makedirs(download_dir, exist_ok=True)
    
    # Run original extension tool logic
    res = await ext_tools.download_video_segment(
        args["url"], start_time, end_time, "best", download_dir
    )
    
    file_path = res["clip_file"]
    file_name = os.path.basename(file_path)
    render_url = f"https://tube-bridge-mcp.onrender.com/public/downloads/{file_name}"
    
    msg = f"Successfully trimmed video segment.\nDownload it here: {render_url}\nNote: This link will expire in 5 minutes."
    return CallToolResult(content=[TextContent(type="text", text=msg)])


async def _do_watch_video(args: dict) -> CallToolResult:
    video_id = extract_video_id(args["url"])
    start_time = args.get("start_time", "0")
    duration = min(int(args.get("duration", 30)), 120)
    interval = max(float(args.get("interval_seconds", 5.0)), 0.5)
    
    start_sec = ext_tools.parse_time_str(start_time)
    end_sec = start_sec + duration
    
    frames_dir = os.path.join(public_dir, f"frames_{video_id}_{uuid.uuid4().hex[:6]}")
    os.makedirs(frames_dir, exist_ok=True)
    
    # 1. Download chunk
    start_fmt = ext_tools.format_time_str(start_sec)
    end_fmt = ext_tools.format_time_str(end_sec)
    clip_file = os.path.join(frames_dir, "clip.mp4")
    
    dl_cmd = [
        "yt-dlp", "--no-warnings", "--no-playlist",
        "--download-sections", f"*{start_fmt}-{end_fmt}",
        "--extractor-args", "youtube:player_client=ios,tv,web",
        "--format", "bestvideo[height<=720]/best[height<=720]/best",
        "--cookies", "cookies.txt",
        "--output", clip_file,
        f"https://www.youtube.com/watch?v={video_id}"
    ]
    import subprocess
    proc = await asyncio.to_thread(lambda: subprocess.run(dl_cmd, capture_output=True, text=True, timeout=120))
    if proc.returncode != 0:
        return CallToolResult(content=[TextContent(type="text", text=f"Failed to fetch segment: {proc.stderr}")], isError=True)
    
    found_clips = [p for p in glob.glob(os.path.join(frames_dir, "clip.*")) if not p.endswith(".part") and not p.endswith(".ytdl")]
    if not found_clips:
        return CallToolResult(content=[TextContent(type="text", text="No media segment downloaded")], isError=True)
    
    target_video = found_clips[0]
    
    # 2. Extract frames and burn timestamps
    count = max(1, int(duration / interval))
    step = duration / count
    timestamps = [start_sec + (i * step) for i in range(count)]
    
    contents = []
    links = []
    
    for idx, ts in enumerate(timestamps):
        rel_sec = max(0, ts - start_sec)
        fname = f"frame_{idx:03d}.jpg"
        frame_img = os.path.join(frames_dir, fname)
        
        # Burn timestamp. e.g. [00:01:25.5]
        display_ts = f"[{ext_tools.format_time_str(ts)}]"
        vf_expr = f"scale=w='min(640,iw)':h=-2,drawtext=text='{display_ts}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.7:boxborderw=5:x=10:y=10"
        
        ff_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(rel_sec),
            "-i", target_video,
            "-vframes", "1",
            "-vf", vf_expr,
            "-q:v", "3",
            frame_img
        ]
        await asyncio.to_thread(lambda: subprocess.run(ff_cmd, capture_output=True, timeout=15))
        
        if os.path.exists(frame_img):
            rel_dir_name = os.path.basename(frames_dir)
            render_url = f"https://tube-bridge-mcp.onrender.com/public/{rel_dir_name}/{fname}"
            links.append(render_url)
            
            with open(frame_img, "rb") as img_f:
                b64_data = base64.b64encode(img_f.read()).decode("ascii")
            
            contents.append(TextContent(type="text", text=f"Frame at {display_ts}:"))
            contents.append(ImageContent(type="image", data=b64_data, mimeType="image/jpeg"))

    msg = f"Extracted {len(links)} frames from {start_fmt} to {end_fmt}.\nTemporary HTTP links:\n" + "\n".join(links)
    contents.insert(0, TextContent(type="text", text=msg))
    
    return CallToolResult(content=contents)


@server.call_tool(validate_input=False)
async def custom_call_tool(name: str, arguments: dict):
    try:
        if name == "watch_video":
            return await _do_watch_video(arguments)
        if name == "download_video_segment":
            return await _do_download_video_segment(arguments)
            
        tbs_mod._validate_arguments(name, arguments)
        result = await tbs_mod._handle_tool(name, arguments)
        if isinstance(result, tbs_mod.ExtractedFrame):
            import json
            metadata = {
                "video_id": result.video_id,
                "requested_timestamp_ms": result.requested_timestamp_ms,
                "mime_type": result.mime_type,
            }
            encoded_image = base64.b64encode(result.data).decode("ascii")
            return CallToolResult(
                content=[
                    TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False, indent=2)),
                    ImageContent(type="image", data=encoded_image, mimeType=result.mime_type)
                ]
            )
        import json
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))])
    except Exception as e:
        return tbs_mod._error_result(tbs_mod.InternalError())


async def cleanup_task():
    while True:
        try:
            now = time.time()
            for root, dirs, files in os.walk(public_dir):
                for f in files:
                    fpath = os.path.join(root, f)
                    if now - os.path.getmtime(fpath) > 300: # 5 minutes
                        os.remove(fpath)
            # also cleanup empty dirs
            for root, dirs, files in os.walk(public_dir, topdown=False):
                if root != public_dir and not os.listdir(root):
                    os.rmdir(root)
        except Exception:
            pass
        await asyncio.sleep(60)

from tube_bridge.transport import create_app
port = int(os.environ.get("PORT", 8080))
host = "0.0.0.0"
tube_bridge_app = create_app(server, host, port)

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        task = asyncio.create_task(cleanup_task())
        try:
            await tube_bridge_app(scope, receive, send)
        finally:
            task.cancel()
        return

    if scope["type"] == "http":
        path = scope["path"]
        if path.startswith("/public/"):
            rel_path = path[len("/public/"):]
            file_path = os.path.normpath(os.path.join(public_dir, rel_path))
            if os.path.commonpath([public_dir, file_path]) == public_dir and os.path.isfile(file_path):
                response = FileResponse(file_path)
                await response(scope, receive, send)
                return
            else:
                response = JSONResponse({"error": "not found"}, status_code=404)
                await response(scope, receive, send)
                return
            
    await tube_bridge_app(scope, receive, send)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
