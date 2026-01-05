"""
增强的解析模块
提供多策略解析、fallback机制和错误响应日志
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """解析错误基类"""

    def __init__(self, message: str, error_type: str = "parse", response_info: Dict = None):
        super().__init__(message)
        self.error_type = error_type
        self.response_info = response_info or {}
        self.timestamp = datetime.now().isoformat()


class ResponseAnalyzer:
    """响应分析器，用于解析失败时提取关键信息"""

    RISK_CONTROL_SIGNATURES = [
        "验证码",
        "captcha",
        "安全验证",
        "security check",
        "过于频繁",
        "too many requests",
        "请求频率",
        "风控",
        "risk control",
        "block",
        "blocked",
    ]

    CAPTCHA_SIGNATURES = ["验证码", "captcha", "滑动验证", "点选验证", "图片验证码", "SMS verification"]

    SENSITIVE_PATTERNS = [
        r"ticket=([^&]+)",
        r"challenge=([^&]+)",
        r"s_v_web_id=([^&]+)",
        r"session_id=([^&]+)",
        r"token=([^&]+)",
    ]

    CONTENT_PREVIEW_MAX_LENGTH = 300

    @staticmethod
    def _mask_sensitive(text: str) -> str:
        """脱敏敏感信息"""
        masked = text
        for pattern in ResponseAnalyzer.SENSITIVE_PATTERNS:
            masked = re.sub(pattern, r"ticket=***", masked, flags=re.IGNORECASE)
        return masked

    @staticmethod
    def analyze_response(content: str, status_code: int, final_url: str, content_type: str = "") -> Dict[str, Any]:
        """分析响应，返回诊断信息"""
        result = {
            "status_code": status_code,
            "final_url": final_url,
            "content_type": content_type,
            "content_length": len(content) if content else 0,
            "error_type": "unknown",
            "is_risk_control": False,
            "is_captcha": False,
            "is_html_structure_changed": False,
            "content_preview": "",
            "detected_signature": None,
        }

        if not content:
            result["error_type"] = "empty_response"
            return result

        content_lower = content.lower()

        for sig in ResponseAnalyzer.RISK_CONTROL_SIGNATURES:
            if sig.lower() in content_lower:
                result["is_risk_control"] = True
                result["error_type"] = "risk_control"
                result["detected_signature"] = sig
                break

        if not result["is_risk_control"]:
            for sig in ResponseAnalyzer.CAPTCHA_SIGNATURES:
                if sig.lower() in content_lower:
                    result["is_captcha"] = True
                    result["error_type"] = "captcha"
                    result["detected_signature"] = sig
                    break

        if not result["is_risk_control"] and not result["is_captcha"]:
            if "unique_id" not in content_lower and "<script" in content_lower:
                result["is_html_structure_changed"] = True
                result["error_type"] = "html_structure_changed"

        preview = content[: ResponseAnalyzer.CONTENT_PREVIEW_MAX_LENGTH]
        preview = preview.replace("\n", " ").strip()
        preview = ResponseAnalyzer._mask_sensitive(preview)
        result["content_preview"] = preview

        return result

    @staticmethod
    def log_parse_failure(url: str, error: ParseError):
        """记录解析失败详情"""
        logger.error(f"Parse failed for {url} | Type: {error.error_type} | Time: {error.timestamp}")

        if error.response_info:
            info = error.response_info
            logger.error(
                f"  Response: HTTP{info.get('status_code', 'N/A')} | "
                f"URL: {info.get('final_url', 'N/A')[:80]}... | "
                f"Length: {info.get('content_length', 0)}"
            )
            if info.get("content_preview"):
                logger.error(f"  Preview: {info['content_preview'][:100]}...")

            if info.get("detected_signature"):
                logger.error(f"  Signature: {info['detected_signature']}")


class MultiStrategyParser:
    """多策略解析器"""

    STRATEGY_A_URL_PARSE = "url_parse"
    STRATEGY_B_HTML_PARSE = "html_parse"
    STRATEGY_C_API_PARSE = "api_parse"

    @staticmethod
    async def extract_from_url(url: str) -> Optional[Dict[str, str]]:
        """策略A: 从最终跳转URL中提取roomId/unique_id"""
        try:
            from urllib.parse import urlparse, parse_qs

            parsed = urlparse(url)
            params = parse_qs(parsed.query)

            result = {}

            patterns = [
                ("room_id", r"room/(\d+)"),
                ("room_id", r"room_id[=:]*(\d+)"),
                ("unique_id", r"user/([^/?&]+)"),
                ("unique_id", r"u/([^/?&]+)"),
                ("user_id", r"user_id[=:]*(\d+)"),
            ]

            for key, pattern in patterns:
                match = re.search(pattern, url)
                if match:
                    result[key] = match.group(1)

            return result if result else None

        except Exception as e:
            logger.debug(f"Strategy A failed: {e}")
            return None

    @staticmethod
    async def extract_from_html(html_content: str) -> Optional[Dict[str, str]]:
        """策略B: 从HTML/JSON blob中提取"""
        try:
            result = {}

            json_patterns = [
                r"<script[^>]*>\s*window\.__INITIAL_STATE__\s*=\s*({.*?})\s*</script>",
                r"<script[^>]*>\s*window\.__DATA__\s*=\s*({.*?})\s*</script>",
                r'"roomId"\s*:\s*(\d+)',
                r'"unique_id"\s*:\s*"([^"]+)"',
                r'"userId"\s*:\s*(\d+)',
            ]

            for pattern in json_patterns:
                match = re.search(pattern, html_content)
                if match:
                    if pattern.startswith("<script"):
                        try:
                            data = json.loads(match.group(1))
                            if "roomId" in data:
                                result["room_id"] = str(data["roomId"])
                            if "uniqueId" in data:
                                result["unique_id"] = data["uniqueId"]
                        except json.JSONDecodeError:
                            pass
                    else:
                        result[pattern.split(":")[0].strip().replace('"', "")] = match.group(1)

            return result if result else None

        except Exception as e:
            logger.debug(f"Strategy B failed: {e}")
            return None

    @staticmethod
    async def fallback_api_call(url: str, headers: Dict = None) -> Optional[Dict[str, str]]:
        """策略C: 调用备用API"""
        try:
            import aiohttp

            api_endpoints = {
                "douyin": "https://live.douyin.com/webcast/room/pc/",
                "tiktok": "https://www.tiktok.com/api/live/detail/",
            }

            for platform, endpoint in api_endpoints.items():
                if platform in url.lower():
                    async with aiohttp.ClientSession() as session:
                        async with session.get(endpoint, headers=headers, timeout=10) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                return {"api_response": data}
                    break

            return None

        except Exception as e:
            logger.debug(f"Strategy C failed: {e}")
            return None

    @staticmethod
    async def parse_with_fallback(
        url: str, html_content: str = None, final_url: str = None, headers: Dict = None
    ) -> Tuple[Optional[Dict[str, str]], str]:
        """
        尝试多种解析策略

        Returns:
            Tuple[解析结果, 使用的策略名称]
        """

        strategies = [
            (MultiStrategyParser.STRATEGY_A_URL_PARSE, lambda: MultiStrategyParser.extract_from_url(final_url or url)),
            (
                MultiStrategyParser.STRATEGY_B_HTML_PARSE,
                lambda: MultiStrategyParser.extract_from_html(html_content) if html_content else None,
            ),
            (MultiStrategyParser.STRATEGY_C_API_PARSE, lambda: MultiStrategyParser.fallback_api_call(url, headers)),
        ]

        for strategy_name, strategy_func in strategies:
            try:
                result = await strategy_func()
                if result:
                    logger.debug(f"Strategy {strategy_name} succeeded")
                    return result, strategy_name
            except Exception as e:
                logger.debug(f"Strategy {strategy_name} failed: {e}")
                continue

        return None, "none"


class MetricsReporter:
    """指标报告器"""

    def __init__(self):
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "parse_errors": {
                "unique_id_missing": 0,
                "risk_control": 0,
                "captcha": 0,
                "html_changed": 0,
                "empty_response": 0,
            },
            "strategy_usage": {},
            "avg_response_time": 0,
        }
        self._lock = asyncio.Lock()

    async def record_request(self, url: str, success: bool, response_info: Dict = None):
        """记录请求结果"""
        async with self._lock:
            self.metrics["total_requests"] += 1
            if success:
                self.metrics["successful_requests"] += 1
            else:
                self.metrics["failed_requests"] += 1

            if response_info and not success:
                error_type = response_info.get("error_type", "unknown")
                if error_type in self.metrics["parse_errors"]:
                    self.metrics["parse_errors"][error_type] += 1

    def record_strategy_usage(self, strategy: str):
        """记录策略使用情况"""
        if strategy in self.metrics["strategy_usage"]:
            self.metrics["strategy_usage"][strategy] += 1
        else:
            self.metrics["strategy_usage"][strategy] = 1

    def get_report(self) -> str:
        """生成报告"""
        total = self.metrics["total_requests"]
        success_rate = (self.metrics["successful_requests"] / total * 100) if total > 0 else 0

        report_lines = [
            f"=== StreamGet Metrics Report ===",
            f"Total Requests: {total}",
            f"Success Rate: {success_rate:.1f}%",
            f"Success: {self.metrics['successful_requests']}",
            f"Failed: {self.metrics['failed_requests']}",
            f"",
            f"Parse Errors:",
        ]

        for error_type, count in self.metrics["parse_errors"].items():
            report_lines.append(f"  {error_type}: {count}")

        report_lines.append(f"")
        report_lines.append(f"Strategy Usage:")
        for strategy, count in self.metrics["strategy_usage"].items():
            report_lines.append(f"  {strategy}: {count}")

        return "\n".join(report_lines)


metrics_reporter = MetricsReporter()
