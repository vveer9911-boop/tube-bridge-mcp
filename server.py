import os
import uvicorn
from starlette.responses import JSONResponse

import tube_bridge.tools as tools

# Fast, robust metadata extraction via ytsearch
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

from tube_bridge.server import server
from tube_bridge.transport import create_app

port = int(os.environ.get("PORT", 8080))
host = "0.0.0.0"

app = create_app(server, host, port)

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port, log_level="info")
