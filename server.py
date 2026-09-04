import os
import subprocess
import uvicorn

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
                new_cmd[idx + 1] = f"{new_cmd[idx + 1]};youtube:player_client=android,web"
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

port = int(os.environ.get("PORT", 8080))
host = "0.0.0.0"

app = create_app(server, host, port)

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port, log_level="info")
