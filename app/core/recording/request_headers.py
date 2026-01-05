"""
请求头规范化模块
提供UA轮换、Header规范化功能
"""

import random
from typing import Dict, List, Optional


class UserAgentPool:
    """User-Agent 池"""

    CHROMIUM_UA = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]

    FIREFOX_UA = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]

    MOBILE_UA = [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.43 Mobile Safari/537.36",
    ]

    _index = 0

    @classmethod
    def get_random(cls) -> str:
        """随机获取一个UA"""
        all_ua = cls.CHROMIUM_UA + cls.FIREFOX_UA + cls.MOBILE_UA
        return random.choice(all_ua)

    @classmethod
    def get_next(cls) -> str:
        """轮询获取UA"""
        all_ua = cls.CHROMIUM_UA + cls.FIREFOX_UA + cls.MOBILE_UA
        ua = all_cls._index % len(all_ua)
        cls._index += 1
        return ua

    @classmethod
    def get_for_platform(cls, platform: str) -> str:
        """根据平台获取合适的UA"""
        mobile_platforms = {"tiktok", "douyin", "kuaishou"}

        if platform.lower() in mobile_platforms:
            return random.choice(cls.MOBILE_UA)

        return random.choice(cls.CHROMIUM_UA)


class HeaderBuilder:
    """Header 构建器"""

    PLATFORM_HEADERS = {
        "douyin": {
            "Referer": "https://live.douyin.com/",
            "Origin": "https://live.douyin.com",
        },
        "tiktok": {
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
        },
        "kuaishou": {
            "Referer": "https://live.kuaishou.com/",
            "Origin": "https://live.kuaishou.com",
        },
        "huya": {
            "Referer": "https://www.huya.com/",
            "Origin": "https://www.huya.com",
        },
        "douyu": {
            "Referer": "https://www.douyu.com/",
            "Origin": "https://www.douyu.com",
        },
        "bilibili": {
            "Referer": "https://live.bilibili.com/",
            "Origin": "https://live.bilibili.com",
        },
        "youtube": {
            "Referer": "https://www.youtube.com/",
            "Origin": "https://www.youtube.com",
        },
    }

    @staticmethod
    def build(platform: str = None, referer: str = None, ua: str = None, custom_headers: Dict = None) -> Dict[str, str]:
        """
        构建标准化的请求头

        Args:
            platform: 平台名称
            referer: 自定义Referer
            ua: 自定义User-Agent
            custom_headers: 自定义头

        Returns:
            标准化的请求头字典
        """
        headers = {
            "User-Agent": ua or UserAgentPool.get_random(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }

        if referer:
            headers["Referer"] = referer
        elif platform and platform in HeaderBuilder.PLATFORM_HEADERS:
            headers.update(HeaderBuilder.PLATFORM_HEADERS[platform])

        if custom_headers:
            headers.update(custom_headers)

        return headers

    @staticmethod
    def build_for_recording(platform: str, url: str) -> Dict[str, str]:
        """构建录制用的请求头"""
        headers = HeaderBuilder.build(platform=platform)

        live_domains = {
            "douyin": "live.douyin.com",
            "tiktok": "www.tiktok.com",
            "kuaishou": "live.kuaishou.com",
        }

        if platform in live_domains:
            headers["Referer"] = f"https://{live_domains[platform]}/"

        return headers


class CookieManager:
    """Cookie 管理器"""

    def __init__(self):
        self._cookies = {}

    def set_cookies(self, platform: str, cookies: str):
        """设置平台cookies"""
        if cookies:
            self._cookies[platform] = cookies

    def get_cookies(self, platform: str) -> Optional[str]:
        """获取平台cookies"""
        return self._cookies.get(platform)

    def is_valid(self, platform: str) -> bool:
        """检查cookies是否有效"""
        cookies = self.get_cookies(platform)
        if not cookies:
            return False

        invalid_signs = ["invalid", "expired", "undefined", "null"]
        cookies_lower = cookies.lower()

        for sign in invalid_signs:
            if sign in cookies_lower:
                return False

        return True


header_builder = HeaderBuilder()
cookie_manager = CookieManager()
