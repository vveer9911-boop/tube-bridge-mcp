import os
import shlex
import subprocess
import uvicorn
from starlette.responses import JSONResponse

from tube_bridge.server import server
from tube_bridge.transport import create_app
import tube_bridge.tools as tools

# Add direct fast metadata retrieval via ytsearch
_orig_video_info = tools._video_info_cached

def _robust_video_info(video_id: str) -> dict:
    try:
        items, err = tools.yt.run_ytdlp_multi([
            f"ytsearch1:{video_id}",
            "--dump-json",
        ], timeout=15)
        if items:
            d = items[0]
            info = tools.yt.parse_video_info(d)
            res = info.to_dict()
            tools.cache.set_video_info(video_id, res)
            return res
    except Exception:
        pass
    return _orig_video_info(video_id)

tools._video_info_cached = _robust_video_info

port = int(os.environ.get("PORT", 8080))
host = "0.0.0.0"

app = create_app(server, host, port)

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port, log_level="info")
