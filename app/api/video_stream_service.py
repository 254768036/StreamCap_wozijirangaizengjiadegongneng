import asyncio
import hashlib
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import aiofiles
from cachetools import TTLCache
from dotenv import find_dotenv, load_dotenv
import time
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware

dotenv_path = find_dotenv()
load_dotenv(dotenv_path)
CUSTOM_VIDEO_ROOT_DIR = os.getenv("CUSTOM_VIDEO_ROOT_DIR")
VIDEO_API_PORT = os.getenv("VIDEO_API_PORT") or 6007

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_VIDEO_ROOT_DIR = Path(os.path.split(os.path.realpath(sys.argv[0]))[0]).parent.parent / "downloads"
VIDEO_DIR = Path(CUSTOM_VIDEO_ROOT_DIR or DEFAULT_VIDEO_ROOT_DIR)
os.makedirs(VIDEO_DIR, exist_ok=True)

VIDEO_META_CACHE = TTLCache(maxsize=50, ttl=300)
CHUNK_CACHE = TTLCache(maxsize=25, ttl=60)
# 请求频率限制缓存
RATE_LIMIT_CACHE = TTLCache(maxsize=100, ttl=60)  # 1分钟内最多100个请求

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not VIDEO_DIR.exists():
        logger.error(f"Video directory does not exist: {VIDEO_DIR}")
        raise RuntimeError(f"Video directory does not exist: {VIDEO_DIR}")
    _app.mount("/api/videos", StaticFiles(directory=VIDEO_DIR), name="videos")
    yield

    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    _app.mount("/api/videos", StaticFiles(directory=None))
    logger.info("Shutting down the application.")


app = FastAPI(
    title="StreamCap Video API",
    description="Secure video streaming API for StreamCap",
    version="2.0.0",
    docs_url=None,  # 生产环境关闭文档
    redoc_url=None
)

# 添加安全中间件
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "0.0.0.0"]  # 限制允许的主机
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 在生产环境中应该设置具体的域名
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_client_ip(request: Request) -> str:
    """获取客户端真实IP地址"""
    # 检查代理头部
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    return request.client.host


def check_rate_limit(request: Request, max_requests: int = 100, time_window: int = 60) -> bool:
    """
    请求频率限制检查

    Args:
        request: FastAPI 请求对象
        max_requests: 时间窗口内最大请求数
        time_window: 时间窗口（秒）

    Returns:
        bool: 是否允许请求
    """
    client_ip = get_client_ip(request)
    current_time = int(time.time())
    time_bucket = current_time // time_window
    rate_limit_key = f"rate_limit:{client_ip}:{time_bucket}"

    # 获取当前计数
    current_count = RATE_LIMIT_CACHE.get(rate_limit_key, 0)

    if current_count >= max_requests:
        logger.warning(f"Rate limit exceeded for IP {client_ip}: {current_count}/{max_requests}")
        return False

    # 增加计数
    RATE_LIMIT_CACHE[rate_limit_key] = current_count + 1
    return True


def validate_file_path(requested_path: Path, base_dir: Path) -> bool:
    """
    严格验证文件路径防止路径遍历攻击

    Args:
        requested_path: 请求的文件路径
        base_dir: 允许的根目录

    Returns:
        bool: 路径是否安全
    """
    try:
        # 规范化路径并解析为绝对路径
        requested_path = requested_path.resolve()
        base_dir = base_dir.resolve()

        # 确保请求的路径在基础目录内
        requested_path.relative_to(base_dir)

        # 额外检查：禁止访问隐藏文件和系统文件
        if any(part.startswith('.') for part in requested_path.parts):
            logger.warning(f"Attempt to access hidden file: {requested_path}")
            return False

        # 检查是否为系统关键文件
        system_files = {
            'boot.ini', 'ntldr', 'ntdetect.com', 'config.sys', 'autoexec.bat',
            'hosts', 'lmhosts.sam', 'networks', 'protocol', 'services'
        }
        if requested_path.name.lower() in system_files:
            logger.warning(f"Attempt to access system file: {requested_path}")
            return False

        return True
    except (ValueError, RuntimeError, OSError) as e:
        logger.error(f"Path validation failed for {requested_path}: {e}")
        return False


def validate_subfolder_path(subfolder: str):
    """
    验证子文件夹路径的安全性，防止路径遍历攻击

    Args:
        subfolder: 子文件夹路径

    Raises:
        HTTPException: 如果路径不安全
    """
    if not subfolder.strip():
        raise HTTPException(status_code=400, detail="Subfolder cannot be empty")

    # 检查路径长度
    if len(subfolder) > 1000:
        raise HTTPException(status_code=400, detail="Subfolder path too long")

    # 检查危险字符和模式
    dangerous_patterns = [
        r"\.\.",            # 父目录引用
        r"[<>:\"|?*]",      # Windows 禁用字符
        r"[\0-\x1f\x7f]",   # 控制字符
        r"^[\\/]",          # 绝对路径
        r"[{}$`']",         # Shell 特殊字符
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, subfolder, re.IGNORECASE):
            logger.warning(f"Dangerous subfolder pattern detected: {subfolder}")
            raise HTTPException(status_code=400, detail="Invalid subfolder path")

    # 检查是否为隐藏目录
    parts = [part.strip() for part in subfolder.split('/') if part.strip()]
    if any(part.startswith('.') for part in parts):
        logger.warning(f"Hidden directory access attempt: {subfolder}")
        raise HTTPException(status_code=400, detail="Hidden directories not allowed")


def validate_filename(filename: str):
    """
    严格的文件名验证，防止路径遍历和注入攻击

    Args:
        filename: 要验证的文件名

    Raises:
        HTTPException: 如果文件名包含危险字符或非法格式
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Filename cannot be empty")

    # 检查文件名长度
    if len(filename) > 255:
        raise HTTPException(status_code=400, detail="Filename too long")

    # 检查危险字符（更严格）
    dangerous_patterns = [
        r"[\\/]",           # 路径分隔符
        r"\.\.",            # 父目录引用
        r"[<>:\"|?*]",      # Windows 禁用字符
        r"[\0-\x1f\x7f]",   # 控制字符
        r"^\.|\/\.|\.$",    # 隐藏文件或以点结尾
        r"[{}$`']",         # Shell 特殊字符
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, filename, re.IGNORECASE):
            logger.warning(f"Dangerous filename pattern detected: {filename}")
            raise HTTPException(status_code=400, detail="Invalid filename contains dangerous characters")

    # 检查文件扩展名白名单（防止执行文件）
    allowed_extensions = {
        '.mp4', '.flv', '.ts', '.mkv', '.mov', '.avi', '.webm',  # 视频文件
        '.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg',         # 音频文件
        '.srt', '.ass', '.vtt',                                  # 字幕文件
        '.txt', '.log', '.json',                                 # 文本文件
    }

    file_extension = Path(filename).suffix.lower()
    if file_extension and file_extension not in allowed_extensions:
        logger.warning(f"File extension not allowed: {file_extension}")
        raise HTTPException(status_code=400, detail="File type not allowed")


@app.get("/api/videos")
async def get_video(
        request: Request,
        filename: str = Query(..., description="Video filename"),
        subfolder: str | None = Query(None, description="Safe subfolder path")
):
    """
    安全的视频文件访问API，包含输入验证、路径遍历防护和频率限制
    """
    # 请求频率限制检查
    if not check_rate_limit(request, max_requests=50, time_window=60):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": "60"}
        )

    # 输入参数验证
    if not filename or not isinstance(filename, str):
        raise HTTPException(status_code=400, detail="Filename is required and must be a string")

    # 验证子文件夹参数
    if subfolder is not None:
        if not isinstance(subfolder, str):
            raise HTTPException(status_code=400, detail="Subfolder must be a string")

        # 验证子文件夹路径安全性
        try:
            validate_subfolder_path(subfolder)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Subfolder validation error: {e}")
            raise HTTPException(status_code=400, detail="Invalid subfolder path")

    cache_key = f"{filename}-{subfolder}"
    if meta := VIDEO_META_CACHE.get(cache_key):
        if_none_match = request.headers.get("If-None-Match")
        if_modified_since = request.headers.get("If-Modified-Since")

        if if_none_match and if_none_match == meta['etag']:
            return Response(status_code=304)

        if if_modified_since:
            last_modified = datetime.fromisoformat(meta['last_modified'])
            if datetime.strptime(if_modified_since, "%a, %d %b %Y %H:%M:%S GMT") >= last_modified:
                return Response(status_code=304)

    try:
        # 验证文件名
        validate_filename(filename)

        # 构建文件路径
        if subfolder:
            video_path = VIDEO_DIR / subfolder.strip('/') / filename
        else:
            video_path = VIDEO_DIR / filename

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Path construction error: {e}")
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not video_path.is_file():
        logger.error(f"File not found: {video_path}")
        raise HTTPException(status_code=404, detail="Video file not found")

    # 增强的路径遍历攻击防护
    if not validate_file_path(video_path, VIDEO_DIR):
        logger.error(f"Path traversal attempt detected: {video_path}")
        raise HTTPException(status_code=400, detail="Invalid file path")

    stat = video_path.stat()
    file_size = stat.st_size
    last_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()
    etag = hashlib.md5(f"{file_size}-{last_modified}".encode()).hexdigest()

    VIDEO_META_CACHE[cache_key] = {
        'etag': etag,
        'last_modified': last_modified,
        'file_size': file_size
    }

    # Parse Range header
    range_header = request.headers.get("Range")
    if range_header:
        start, end = range_header.replace("bytes=", "").split("-")
        start = int(start)
        end = int(end) if end else file_size - 1

        if start >= file_size or end >= file_size:
            logger.error(f"Invalid range request: {range_header}, file size: {file_size}")
            raise HTTPException(status_code=416, detail="Requested range not satisfiable")

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Type": "video/mp4",
        }
        return StreamingResponse(
            file_sender_range(video_path, start, end),
            status_code=206,
            headers=headers,
        )

    # If no Range header, return the whole file
    security_headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Content-Security-Policy": "default-src 'self'; media-src *; blob-src *;",
    }

    headers = {
        "Content-Length": str(file_size),
        "Content-Type": "video/mp4",
        "Cache-Control": "public, max-age=300",
        "ETag": etag,
        "Last-Modified": datetime.fromisoformat(last_modified).strftime("%a, %d %b %Y %H:%M:%S GMT"),
        **security_headers,
    }

    try:
        return StreamingResponse(file_sender(video_path), headers=headers)
    except Exception:
        logger.exception("Streaming error")
        raise HTTPException(status_code=500, detail="Internal Server Error")


# Async file sender (full content)
async def file_sender(video_path: Path):
    async with aiofiles.open(video_path, "rb") as file:
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            yield chunk


# Async file sender (range content)
async def file_sender_range(video_path: Path, start: int, end: int):
    cache_key = f"{video_path.name}-{start}-{end}"

    if cached := CHUNK_CACHE.get(cache_key):
        yield cached
        return

    async with aiofiles.open(video_path, "rb") as file:
        await file.seek(start)
        chunks = []
        while start <= end:
            chunk_size = min(65536, end - start + 1)
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)
            start += len(chunk)

        full_chunk = b"".join(chunks)
        if len(full_chunk) < 1024 * 1024:
            CHUNK_CACHE[cache_key] = full_chunk
        yield full_chunk


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(VIDEO_API_PORT), log_level="debug")
