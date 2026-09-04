import os
import uvicorn
from tube_bridge.server import server
from tube_bridge.transport import create_app

port = int(os.environ.get("PORT", 8080))
host = "0.0.0.0"

app = create_app(server, host, port)

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port, log_level="info")
