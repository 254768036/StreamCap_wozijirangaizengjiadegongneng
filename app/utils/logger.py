import hashlib
import os
import re
import sys

from loguru import logger

script_path = os.path.split(os.path.realpath(sys.argv[0]))[0]

SENSITIVE_PARAMS = {"expires", "sign", "volcSecret", "volcTime", "token", "key", "signature", "password", "cookie"}


def sanitize_url(url: str) -> str:
    """脱敏URL，隐藏敏感参数"""
    try:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)

        sanitized_params = {}
        for key, values in params.items():
            if key.lower() in SENSITIVE_PARAMS:
                sanitized_params[key] = ["***REDACTED***"]
            else:
                sanitized_params[key] = values

        sanitized_query = urlencode(sanitized_params, doseq=True)
        sanitized_url = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, sanitized_query, parsed.fragment)
        )
        return sanitized_url
    except Exception:
        return url[:50] + "***"


def sanitize_for_log(message: str) -> str:
    """对日志消息进行脱敏处理"""
    if "http" in message.lower():
        urls = re.findall(r'https?://[^\s<>"]+', message)
        for url in urls:
            sanitized = sanitize_url(url)
            message = message.replace(url, sanitized)
    return message


class LogInterceptor:
    """日志拦截器，用于脱敏和结构化日志"""

    @staticmethod
    def process_message(message: str) -> str:
        return sanitize_for_log(message)


logger.add(
    f"{script_path}/logs/streamget.log",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
    filter=lambda i: i["level"].name != "STREAM",
    serialize=False,
    enqueue=True,
    retention=3,
    rotation="3 MB",
    encoding="utf-8",
)

logger.level("STREAM", no=22, color="<blue>")


def log_stream_url(level: str, url: str, extra_info: dict = None):
    """记录流地址，自动脱敏"""
    sanitized_url = sanitize_url(url)

    if extra_info:
        info_str = " | " + ", ".join([f"{k}={v}" for k, v in extra_info.items()])
        message = f"Stream URL: {sanitized_url}{info_str}"
    else:
        message = f"Stream URL: {sanitized_url}"

    logger.log(level, LogInterceptor.process_message(message))


logger.add(
    f"{script_path}/logs/play_url.log",
    level="STREAM",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {message}",
    filter=lambda i: i["level"].name == "STREAM",
    serialize=False,
    enqueue=True,
    retention=1,
    rotation="500 KB",
    encoding="utf-8",
)
