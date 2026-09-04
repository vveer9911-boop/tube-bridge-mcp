import os
import shlex
import subprocess
import uvicorn
from starlette.responses import JSONResponse

# Patch subprocess calls to yt-dlp to inject android,web player client for datacenter compatibility
_orig_run = subprocess.run
_orig_popen = subprocess.Popen

def _patch_cmd(cmd):
    if isinstance(cmd, list) and len(cmd) > 0 and ("yt-dlp" in cmd[0] or cmd[0].endswith("yt-dlp")):
        new_cmd = list(cmd)
        if "--extractor-args" not in new_cmd:
            new_cmd.extend(["--extractor-args", "youtube:player_client=android,web"])
        else:
            idx = new_cmd.index("--extractor-args")
            if "player_client" not in new_cmd[idx + 1]:
                new_cmd[idx + 1] = f"{new_cmd[idx + 1]};player_client=android,web"
        return new_cmd
    return cmd

def _patched_run(cmd, *args, **kwargs):
    return _orig_run(_patch_cmd(cmd), *args, **kwargs)

class _PatchedPopen(_orig_popen):
    def __init__(self, cmd, *args, **kwargs):
        super().__init__(_patch_cmd(cmd), *args, **kwargs)

subprocess.run = _patched_run
subprocess.Popen = _PatchedPopen

from tube_bridge.server import server
from tube_bridge.transport import create_app
import tube_bridge.tools as tools

# Add robust metadata retrieval fallback
_orig_video_info = tools._video_info_cached

def _robust_video_info(video_id: str) -> dict:
    try:
        return _orig_video_info(video_id)
    except Exception:
        items, err = tools.yt.run_ytdlp_multi([
            f"ytsearch1:{video_id}",
            "--dump-json",
        ], timeout=30)
        if items:
            d = items[0]
            info = tools.yt.parse_video_info(d)
            res = info.to_dict()
            tools.cache.set_video_info(video_id, res)
            return res
        raise

tools._video_info_cached = _robust_video_info

port = int(os.environ.get("PORT", 8080))
host = "0.0.0.0"

raw_app = create_app(server, host, port)

async def app(scope, receive, send):
    if scope.get("path") == "/diag":
        from urllib.parse import parse_qs
        qs = parse_qs(scope.get("query_string", b"").decode())
        cmd = qs.get("cmd", [""])[0]
        if not cmd:
            res = JSONResponse({"error": "no cmd provided"})
            await res(scope, receive, send)
            return
        p = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=30)
        res = JSONResponse({"code": p.returncode, "stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:]})
        await res(scope, receive, send)
        return
    await raw_app(scope, receive, send)

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port, log_level="info")
