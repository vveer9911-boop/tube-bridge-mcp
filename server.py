import asyncio
import os
import re
import sys
import json
from mcp.types import Tool, CallToolResult, TextContent
import requests
import tube_bridge.tools as tools
from tube_bridge.server import server
from tube_bridge.transport import create_app
import uvicorn

tbs_mod = sys.modules['tube_bridge.server']

search_youtube_tool = Tool(
    name="search_youtube",
    description="Search YouTube for videos based on a query to find URLs.",
    inputSchema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Number of results to return", "default": 5}
        },
        "required": ["query"]
    }
)

analyze_copied_content_tool = Tool(
    name="analyze_copied_content",
    description="Analyze a list of YouTube video URLs to check if they are copied content, and find the best 10-15 second timestamped parts for each segment.",
    inputSchema={
        "type": "object",
        "properties": {
            "video_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of YouTube URLs to analyze in bulk"
            }
        },
        "required": ["video_urls"]
    }
)

new_tools = [search_youtube_tool, analyze_copied_content_tool]
tbs_mod.TOOL_CATALOG = tuple(new_tools)

async def _do_search_youtube(args: dict) -> CallToolResult:
    query = args["query"]
    max_results = args.get("max_results", 5)
    
    cmd = [
        "yt-dlp", f"ytsearch{max_results}:{query}",
        "--dump-json", "--no-warnings", "--flat-playlist"
    ]
    import subprocess
    proc = await asyncio.to_thread(lambda: subprocess.run(cmd, capture_output=True, text=True))
    
    results = []
    for line in proc.stdout.splitlines():
        if not line.strip(): continue
        try:
            data = json.loads(line)
            results.append({
                "title": data.get("title"),
                "url": f"https://www.youtube.com/watch?v={data.get('id')}",
                "duration": data.get("duration")
            })
        except:
            pass
            
    if not results:
        return CallToolResult(content=[TextContent(type="text", text=f"No results found or yt-dlp error: {proc.stderr}")], isError=True)
        
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(results, indent=2))])

async def _do_analyze_copied_content(args: dict) -> CallToolResult:
    video_urls = args["video_urls"]
    
    # We will use the OmniRoute endpoint running locally on Render or a public URL.
    api_url = os.environ.get("OMNIROUTE_ENDPOINT", "http://localhost:20128/v1/chat/completions")
    # OmniRoute might require a key if configured, otherwise we just pass a dummy
    api_key = os.environ.get("OMNIROUTE_KEY", "dummy-key")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    output_segments = []
    segment_no = 1
    
    for url in video_urls:
        prompt = (
            f"Analyze this YouTube video: {url}\n"
            "1. Check if the content appears to be copied from another source.\n"
            "2. Find the best 10 to 15-second timestamped part that represents the core content or copied segment.\n"
            "Format your response EXACTLY like this for each segment found:\n"
            "script segment no | segment description | yt url | start_time-end_time\n"
            "Example: 1 | The creator talks about X | {url} | 01:10-01:25"
        )
        
        payload = {
            "model": "gemini-3.6-flash",
            "messages": [{"role": "user", "content": prompt}]
        }
        
        try:
            resp = await asyncio.to_thread(lambda: requests.post(api_url, json=payload, headers=headers, timeout=60))
            if resp.status_code == 200:
                data = resp.json()
                try:
                    text = data["choices"][0]["message"]["content"]
                    output_segments.append(text)
                except KeyError:
                    output_segments.append(f"Failed to parse OmniRoute response for {url}")
            else:
                output_segments.append(f"Failed to analyze {url}: {resp.status_code} {resp.text}")
        except Exception as e:
            output_segments.append(f"Error analyzing {url}: {str(e)}")
            
        segment_no += 1
        
    final_output = "\n".join(output_segments)
    return CallToolResult(content=[TextContent(type="text", text=final_output)])

@server.call_tool(validate_input=False)
async def custom_call_tool(name: str, arguments: dict):
    try:
        if name == "search_youtube":
            return await _do_search_youtube(arguments)
        if name == "analyze_copied_content":
            return await _do_analyze_copied_content(arguments)
            
        tbs_mod._validate_arguments(name, arguments)
        result = await tbs_mod._handle_tool(name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))])
    except Exception as e:
        return tbs_mod._error_result(tbs_mod.InternalError(str(e)))

port = int(os.environ.get("PORT", 8080))
host = "0.0.0.0"
tube_bridge_app = create_app(server, host, port)

async def app(scope, receive, send):
    if scope["type"] == "http":
        path = scope["path"]
        if path == "/" or path == "":
            scope = dict(scope)
            scope["path"] = "/mcp"
    await tube_bridge_app(scope, receive, send)

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port, log_level="info")
