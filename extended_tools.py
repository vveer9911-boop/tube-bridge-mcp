"""Combined YouTube & Video Toolkit Extensions for tube-bridge."""
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from tube_bridge.youtube.client import extract_video_id, get_proxy, ytdlp_failure

def parse_time_str(time_val: str | int | float) -> float:
    """Parse time string (e.g. '01:25', '01:15:30', or '85') into seconds."""
    if isinstance(time_val, (int, float)):
        return float(time_val)
    time_str = str(time_val).strip()
    if not time_str:
        return 0.0
    if ":" in time_str:
        parts = [float(p) for p in time_str.split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return float(time_str)

def format_time_str(seconds: float) -> str:
    """Format seconds into HH:MM:SS.mmm for yt-dlp section filter."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:06.3f}"

async def download_video_segment(url: str, start_time: str | int | float, end_time: str | int | float, quality: str = "best", output_dir: str | None = None) -> dict:
    """Download a specific trimmed segment directly from a YouTube URL without full download."""
    video_id = extract_video_id(url)
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    
    start_sec = parse_time_str(start_time)
    end_sec = parse_time_str(end_time)
    
    if end_sec <= start_sec:
        raise ValueError("end_time must be greater than start_time")
        
    start_fmt = format_time_str(start_sec)
    end_fmt = format_time_str(end_sec)
    
    out_dir = Path(output_dir) if output_dir else Path.home() / "Downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_prefix = f"clip_{video_id}_{int(start_sec)}_{int(end_sec)}"
    out_template = str(out_dir / f"{out_prefix}.%(ext)s")
    
    scripts_dir = str(Path(__file__).parent.parent.parent.parent / "Scripts")
    ytdlp_bin = os.path.join(scripts_dir, "yt-dlp.exe") if os.path.exists(os.path.join(scripts_dir, "yt-dlp.exe")) else "yt-dlp"
    
    cmd = [
        ytdlp_bin,
        "--no-warnings",
        "--no-playlist",
        "--download-sections", f"*{start_fmt}-{end_fmt}",
        "--format", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "--output", out_template,
        watch_url
    ]
    
    env = os.environ.copy()
    env["PATH"] = f"{scripts_dir};{env.get('PATH', '')}"
    proxy = get_proxy()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        
    def _run():
        return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
        
    res = await asyncio.to_thread(_run)
    if res.returncode != 0:
        raise RuntimeError(f"yt-dlp failed to download segment: {res.stderr}")
        
    matched = [p for p in out_dir.glob(f"{out_prefix}.*") if p.suffix not in {".part", ".ytdl"}]
    file_path = str(matched[0].resolve()) if matched else out_template
    
    return {
        "status": "success",
        "video_id": video_id,
        "clip_file": file_path,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": round(end_sec - start_sec, 2),
        "message": f"Successfully trimmed and downloaded clip directly from YouTube URL to {file_path}"
    }

async def analyze_video_frames(url: str, start_time: str | int | float = 0, end_time: str | int | float | None = None, interval_seconds: float = 5.0, max_frames: int = 6) -> dict:
    """Extract multiple visual snapshots over a video section for AI visual analysis."""
    video_id = extract_video_id(url)
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    
    start_sec = parse_time_str(start_time)
    end_sec = parse_time_str(end_time) if end_time is not None else start_sec + 30.0
    
    if end_sec <= start_sec:
        raise ValueError("end_time must be greater than start_time")
        
    total_duration = end_sec - start_sec
    count = min(max_frames, max(1, int(total_duration / interval_seconds)))
    step = total_duration / count
    timestamps = [start_sec + (i * step) for i in range(count)]
    
    frames_meta = []
    out_dir = Path.home() / "Downloads" / "analyzed_frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    with tempfile.TemporaryDirectory(prefix="tube-bridge-analyze-") as tmpdir:
        tmp_path = Path(tmpdir)
        clip_file = tmp_path / "section.mp4"
        
        start_fmt = format_time_str(start_sec)
        end_fmt = format_time_str(end_sec)
        
        dl_cmd = [
            "yt-dlp",
            "--no-warnings",
            "--no-playlist",
            "--download-sections", f"*{start_fmt}-{end_fmt}",
            "--format", "bestvideo[height<=720]/best[height<=720]/best",
            "--output", str(clip_file),
            watch_url
        ]
        
        env = os.environ.copy()
        scripts_dir = str(Path(__file__).parent.parent.parent.parent / "Scripts")
        env["PATH"] = f"{scripts_dir};{env.get('PATH', '')}"
        proxy = get_proxy()
        if proxy:
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy
            
        ytdlp_bin = os.path.join(scripts_dir, "yt-dlp.exe") if os.path.exists(os.path.join(scripts_dir, "yt-dlp.exe")) else "yt-dlp"
        dl_cmd[0] = ytdlp_bin
            
        res = await asyncio.to_thread(lambda: subprocess.run(dl_cmd, capture_output=True, text=True, env=env, timeout=90))
        if res.returncode != 0:
            raise RuntimeError(f"Failed to fetch section for analysis: {res.stderr}")
            
        found_clips = [p for p in tmp_path.glob("section.*") if p.is_file()]
        if not found_clips:
            raise RuntimeError("No media section downloaded for analysis")
            
        target_video = str(found_clips[0])
        ffmpeg_bin = os.path.join(scripts_dir, "ffmpeg.exe") if os.path.exists(os.path.join(scripts_dir, "ffmpeg.exe")) else "ffmpeg"
        
        for idx, ts in enumerate(timestamps):
            rel_sec = max(0, ts - start_sec)
            frame_img = out_dir / f"frame_{video_id}_{idx+1}_{int(ts)}.jpg"
            ff_cmd = [
                ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(rel_sec),
                "-i", target_video,
                "-vframes", "1",
                "-vf", "scale=w='min(640,iw)':h=-2",
                "-q:v", "3",
                str(frame_img)
            ]
            await asyncio.to_thread(lambda: subprocess.run(ff_cmd, capture_output=True, check=False, timeout=15))
            if frame_img.exists():
                frames_meta.append({
                    "frame_index": idx + 1,
                    "timestamp_formatted": format_time_str(ts),
                    "timestamp_seconds": round(ts, 2),
                    "status": "extracted",
                    "file_path": str(frame_img.resolve())
                })

    return {
        "status": "success",
        "video_id": video_id,
        "analyzed_range": f"{format_time_str(start_sec)} to {format_time_str(end_sec)}",
        "extracted_frames_count": len(frames_meta),
        "frames": frames_meta,
        "summary": f"Extracted {len(frames_meta)} visual frames across video segment for visual AI analysis."
    }
