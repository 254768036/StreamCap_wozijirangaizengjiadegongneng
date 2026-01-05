import asyncio
import hashlib
import os
import random
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional, TypeVar

from ...messages import desktop_notify, message_pusher
from ...models.media.video_quality_model import VideoQuality
from ...models.recording.recording_status_model import RecordingStatus
from ...utils import utils
from ...utils.logger import logger
from ..media import ffmpeg_builders
from ..media.direct_downloader import DirectStreamDownloader
from ..platforms import platform_handlers
from ..platforms.platform_handlers import StreamData
from ..runtime.process_manager import BackgroundService

T = TypeVar("T")


class TieredCache:
    """
    分层缓存系统
    - L1: room_id/unique_id (长TTL: 10分钟)
    - L2: is_live状态 (中TTL: 30秒)
    - L3: play_url/flv_url (短TTL: 10-20秒或根据expires计算)
    """

    _instance = None
    _l1_cache: Dict[str, Dict[str, Any]] = {}  # room_id/unique_id
    _l2_cache: Dict[str, Dict[str, Any]] = {}  # is_live状态
    _l3_cache: Dict[str, Dict[str, Any]] = {}  # play_url
    _offline_count: Dict[str, int] = {}
    _lock = threading.Lock()

    TTL_CONFIG = {
        "l1": 600,  # 10分钟 room_id缓存
        "l2": 30,  # 30秒 状态缓存
        "l3": 15,  # 15秒 play_url缓存（默认值）
        "force_refresh_interval": 5,  # 每5次离线强制刷新一次
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def should_force_refresh(self, url: str) -> bool:
        """判断是否应该强制刷新（用于检测离线→在线切换）"""
        count = self._offline_count.get(url, 0)
        return count > 0 and count % self.TTL_CONFIG["force_refresh_interval"] == 0

    def set_l1(self, url: str, data: Dict[str, str]):
        """设置L1缓存（room_id/unique_id）"""
        with self._lock:
            self._l1_cache[url] = {"data": data, "timestamp": time.time()}

    def get_l1(self, url: str) -> Optional[Dict[str, str]]:
        """获取L1缓存"""
        cached = self._l1_cache.get(url)
        if cached and (time.time() - cached["timestamp"]) < self.TTL_CONFIG["l1"]:
            return cached["data"]
        return None

    def set_l2(self, url: str, is_live: bool):
        """设置L2缓存（is_live状态）"""
        with self._lock:
            self._l2_cache[url] = {"is_live": is_live, "timestamp": time.time()}

    def get_l2(self, url: str) -> Optional[tuple[bool, int]]:
        """获取L2缓存，返回(is_live, age_seconds)"""
        cached = self._l2_cache.get(url)
        if cached:
            age = int(time.time() - cached["timestamp"])
            if age < self.TTL_CONFIG["l2"]:
                return cached["is_live"], age
        return None, 0

    def set_l3(self, url: str, play_url: str, expires_in: int = None):
        """设置L3缓存（play_url，可根据expires计算TTL）"""
        with self._lock:
            ttl = expires_in if expires_in else self.TTL_CONFIG["l3"]
            self._l3_cache[url] = {"play_url": play_url, "expires_in": ttl, "timestamp": time.time()}

    def get_l3(self, url: str) -> Optional[tuple[str, int]]:
        """获取L3缓存，返回(play_url, remaining_ttl)"""
        cached = self._l3_cache.get(url)
        if cached:
            elapsed = time.time() - cached["timestamp"]
            remaining = cached["expires_in"] - int(elapsed)
            if remaining > 0:
                return cached["play_url"], remaining
        return None, 0

    def update_offline_count(self, url: str, increment: bool = True) -> int:
        """更新连续离线计数"""
        with self._lock:
            if url not in self._offline_count:
                self._offline_count[url] = 0
            if increment:
                self._offline_count[url] += 1
            else:
                self._offline_count[url] = 0
            return self._offline_count[url]

    def get_poll_interval(self, url: str, base_interval: int = 60, is_priority: bool = False) -> int:
        """获取动态轮询间隔，支持优先级和时间段策略"""
        count = self._offline_count.get(url, 0)

        if is_priority:
            min_interval = 60
            max_interval = 300
        else:
            current_hour = datetime.now().hour
            is_prime_time = 19 <= current_hour <= 23
            if is_prime_time:
                min_interval = 60
                max_interval = 300
            else:
                min_interval = 120
                max_interval = 600

        if count == 0:
            return base_interval
        elif count == 1:
            return 180
        elif count == 2:
            return 300
        else:
            return min(max_interval, base_interval * (2 ** (count - 2)))

    def clear_cache(self, url: str = None):
        """清除缓存"""
        with self._lock:
            if url:
                self._l1_cache.pop(url, None)
                self._l2_cache.pop(url, None)
                self._l3_cache.pop(url, None)
                self._offline_count.pop(url, None)
            else:
                self._l1_cache.clear()
                self._l2_cache.clear()
                self._l3_cache.clear()
                self._offline_count.clear()


class StickySessionManager:
    """
    Sticky Session 管理器
    同一个URL/主播在一定时间内固定使用相同的UA和Cookie
    """

    _instance = None
    _sessions: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    SESSION_TTL = 3600  # 1小时

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_session(self, url: str) -> Dict[str, Any]:
        """获取URL对应的session"""
        with self._lock:
            if url not in self._sessions:
                self._sessions[url] = {
                    "ua": None,
                    "cookie": None,
                    "created_at": time.time(),
                    "success_count": 0,
                    "fail_count": 0,
                }

            session = self._sessions[url]

            if time.time() - session["created_at"] > self.SESSION_TTL:
                session["ua"] = None
                session["cookie"] = None

            return session

    def set_ua(self, url: str, ua: str):
        """设置UA"""
        session = self.get_session(url)
        if session["ua"] is None:
            session["ua"] = ua
            logger.debug(f"Sticky UA assigned for {url[:50]}...")

    def get_ua(self, url: str, default_ua: str = None) -> Optional[str]:
        """获取UA，如果没有则使用默认"""
        session = self.get_session(url)
        return session["ua"] or default_ua

    def set_cookie(self, url: str, cookie: str):
        """设置Cookie"""
        session = self.get_session(url)
        if session["cookie"] is None:
            session["cookie"] = cookie

    def get_cookie(self, url: str, default_cookie: str = None) -> Optional[str]:
        """获取Cookie"""
        session = self.get_session(url)
        return session["cookie"] or default_cookie

    def record_success(self, url: str):
        """记录成功"""
        session = self.get_session(url)
        session["success_count"] += 1

    def record_failure(self, url: str):
        """记录失败，失败多次后切换UA"""
        session = self.get_session(url)
        session["fail_count"] += 1
        if session["fail_count"] >= 3:
            session["ua"] = None
            session["fail_count"] = 0
            logger.warning(f"UA rotated for {url[:50]}... after 3 failures")

    def get_stats(self) -> Dict:
        """获取统计信息"""
        with self._lock:
            total = len(self._sessions)
            active = sum(1 for s in self._sessions.values() if time.time() - s["created_at"] < self.SESSION_TTL)
            return {"total": total, "active": active}


class RiskControlManager:
    """
    风控管理器和自愈闭环
    检测到风控后自动进入cooldown并降速
    """

    _instance = None
    _risk_domains: Dict[str, Dict[str, Any]] = {}
    _global_cooldown_until: float = 0
    _lock = threading.Lock()

    COOLDOWN_DURATION = 900  # 15分钟
    FALLBACK_LIMIT = 5  # 每主播每小时fallback上限

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def is_in_cooldown(self, url: str = None) -> bool:
        """检查是否在cooldown期"""
        if time.time() < self._global_cooldown_until:
            return True
        if url:
            domain = self._extract_domain(url)
            with self._lock:
                risk_info = self._risk_domains.get(domain, {})
                if risk_info.get("cooldown_until", 0) > time.time():
                    return True
        return False

    def _extract_domain(self, url: str) -> str:
        """提取域名"""
        from urllib.parse import urlparse

        try:
            return urlparse(url).netloc
        except:
            return url

    def on_risk_detected(self, url: str, risk_type: str = "general"):
        """风控检测到时的处理"""
        domain = self._extract_domain(url)

        with self._lock:
            if domain not in self._risk_domains:
                self._risk_domains[domain] = {"fallback_count": 0, "cooldown_until": 0, "last_risk_type": None}

            risk_info = self._risk_domains[domain]
            risk_info["fallback_count"] += 1
            risk_info["last_risk_type"] = risk_type

            if risk_info["fallback_count"] >= self.FALLBACK_LIMIT:
                risk_info["cooldown_until"] = time.time() + self.COOLDOWN_DURATION
                risk_info["fallback_count"] = 0
                logger.warning(f"Domain {domain} entered cooldown ({self.COOLDOWN_DURATION}s) due to {risk_type}")
            else:
                risk_info["cooldown_until"] = time.time() + 300  # 5分钟
                logger.warning(
                    f"Domain {domain} at risk ({risk_type}), "
                    f"fallback count: {risk_info['fallback_count']}/{self.FALLBACK_LIMIT}"
                )

    def get_fallback_count(self, url: str) -> int:
        """获取fallback调用次数"""
        domain = self._extract_domain(url)
        with self._lock:
            return self._risk_domains.get(domain, {}).get("fallback_count", 0)

    def can_use_fallback(self, url: str) -> bool:
        """检查是否可以使用fallback API"""
        return self.get_fallback_count(url) < self.FALLBACK_LIMIT


class MetricsCollector:
    """性能指标收集器"""

    _instance = None
    _metrics: Dict[str, Any] = {}
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._metrics = {
                "total_fetches": 0,
                "success_fetches": 0,
                "parse_failures": 0,
                "http_errors": 0,
                "captcha_errors": 0,
                "risk_control_errors": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "strategy_stats": {},
                "latency_sum": 0,
                "latency_count": 0,
            }
        return cls._instance

    def record_cache_hit(self):
        with self._lock:
            self._metrics["cache_hits"] += 1

    def record_cache_miss(self):
        with self._lock:
            self._metrics["cache_misses"] += 1

    def record_success(self, latency: float = 0):
        with self._lock:
            self._metrics["total_fetches"] += 1
            self._metrics["success_fetches"] += 1
            if latency > 0:
                self._metrics["latency_sum"] += latency
                self._metrics["latency_count"] += 1

    def record_failure(self, error_type: str, strategy: str = None):
        with self._lock:
            self._metrics["total_fetches"] += 1
            error_types = {
                "parse": "parse_failures",
                "http": "http_errors",
                "captcha": "captcha_errors",
                "risk_control": "risk_control_errors",
            }
            key = error_types.get(error_type, "parse_failures")
            self._metrics[key] += 1

            if strategy:
                if strategy not in self._metrics["strategy_stats"]:
                    self._metrics["strategy_stats"][strategy] = {"success": 0, "fail": 0}
                self._metrics["strategy_stats"][strategy]["fail"] += 1

    def record_strategy_success(self, strategy: str):
        with self._lock:
            if strategy not in self._metrics["strategy_stats"]:
                self._metrics["strategy_stats"][strategy] = {"success": 0, "fail": 0}
            self._metrics["strategy_stats"][strategy]["success"] += 1

    def get_metrics(self) -> Dict:
        """获取指标"""
        with self._lock:
            total = self._metrics["total_fetches"]
            success = self._metrics["success_fetches"]
            latency = (
                self._metrics["latency_sum"] / self._metrics["latency_count"]
                if self._metrics["latency_count"] > 0
                else 0
            )

            cache_total = self._metrics["cache_hits"] + self._metrics["cache_misses"]
            cache_rate = (self._metrics["cache_hits"] / cache_total * 100) if cache_total > 0 else 0

            return {
                "total_fetches": total,
                "success_rate": f"{(success / total * 100):.1f}%" if total > 0 else "N/A",
                "cache_hit_rate": f"{cache_rate:.1f}%",
                "parse_failures": self._metrics["parse_failures"],
                "http_errors": self._metrics["http_errors"],
                "captcha_errors": self._metrics["captcha_errors"],
                "risk_control_errors": self._metrics["risk_control_errors"],
                "avg_latency_ms": f"{(latency * 1000):.1f}" if latency > 0 else "N/A",
                "strategy_stats": dict(self._metrics["strategy_stats"]),
            }

    def get_json_report(self) -> str:
        """获取JSON格式报告"""
        import json

        return json.dumps(self.get_metrics(), indent=2)


class AdaptiveScheduler:
    """自适应调度器，支持批次和随机抖动"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._batch_delay = 0
        return cls._instance

    def get_start_delay(self, index: int, total: int, batch_size: int = 5) -> float:
        """获取批次调度延迟"""
        batch_index = index // batch_size
        jitter = random.uniform(0, 5)
        return batch_index * self._batch_delay + jitter

    def set_batch_delay(self, delay: float):
        """设置批次间延迟"""
        self._batch_delay = delay


tiered_cache = TieredCache()
sticky_session = StickySessionManager()
risk_control = RiskControlManager()
metrics_collector = MetricsCollector()
adaptive_scheduler = AdaptiveScheduler()


class LiveStreamRecorder:
    DEFAULT_SEGMENT_TIME = "1800"
    DEFAULT_SAVE_FORMAT = "mp4"
    DEFAULT_QUALITY = VideoQuality.OD

    def __init__(self, app, recording, recording_info):
        self.app = app
        self.settings = app.settings
        self.recording = recording
        self.recording_info = recording_info
        self.subprocess_start_info = app.subprocess_start_up_info
        self.should_stop = False

        self.user_config = self.settings.user_config
        self.account_config = self.settings.accounts_config
        self.platform_key = self._get_info("platform_key")
        self.cookies = self.settings.cookies_config.get(self.platform_key)

        self.platform = self._get_info("platform")
        self.live_url = self._get_info("live_url")
        self.output_dir = self._get_info("output_dir")
        self.segment_record = self._get_info("segment_record", default=False)
        self.segment_time = self._get_info("segment_time", default=self.DEFAULT_SEGMENT_TIME)
        self.quality = self._get_info("quality", default=self.DEFAULT_QUALITY)
        self.save_format = self._get_info("save_format", default=self.DEFAULT_SAVE_FORMAT).lower()
        self.proxy = self.is_use_proxy()
        self.direct_downloader = None
        os.makedirs(self.output_dir, exist_ok=True)
        self.app.language_manager.add_observer(self)
        self._ = {}
        self.load()

    def load(self):
        language = self.app.language_manager.language
        for key in ("recording_manager", "stream_manager"):
            self._.update(language.get(key, {}))

    def _get_info(self, key: str, default: T = None) -> T:
        return self.recording_info.get(key, default) or default

    def is_use_proxy(self):
        default_proxy_platform = self.user_config.get("default_platform_with_proxy", "")
        proxy_list = default_proxy_platform.replace("，", ",").replace(" ", "").split(",")
        if self.user_config.get("enable_proxy") and self.platform_key in proxy_list:
            self.proxy = self.user_config.get("proxy_address")
            return self.proxy

    def _get_filename(self, stream_info: StreamData) -> str:
        live_title = None
        stream_info.title = utils.clean_name(stream_info.title, None)
        if self.user_config.get("filename_includes_title") and stream_info.title:
            stream_info.title = self._clean_and_truncate_title(stream_info.title)
            live_title = stream_info.title

        if self.recording.streamer_name and self.recording.streamer_name != self._["live_room"]:
            stream_info.anchor_name = utils.clean_name(self.recording.streamer_name)
        else:
            stream_info.anchor_name = utils.clean_name(stream_info.anchor_name, self._["live_room"])

        now = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())

        custom_template = self.user_config.get("custom_filename_template")
        if custom_template:
            filename = custom_template
            filename = filename.replace("{anchor_name}", stream_info.anchor_name or "")
            filename = filename.replace("{title}", live_title or "")
            filename = filename.replace("{time}", now)
            filename = filename.replace("{platform}", stream_info.platform or "")

            while "__" in filename:
                filename = filename.replace("__", "_")

            filename = filename.strip("_")

            if not filename:
                full_filename = "_".join([i for i in (stream_info.anchor_name, live_title, now) if i])
            else:
                full_filename = filename
        else:
            full_filename = "_".join([i for i in (stream_info.anchor_name, live_title, now) if i])

        return full_filename

    def _get_output_dir(self, stream_info: StreamData) -> str:
        if self.recording.recording_dir and self.user_config.get("folder_name_time"):
            current_date = datetime.today().strftime("%Y-%m-%d")
            if current_date not in self.recording.recording_dir:
                self.recording.recording_dir = None

        if self.recording.recording_dir:
            return self.recording.recording_dir

        now = datetime.today().strftime("%Y-%m-%d_%H-%M-%S")
        output_dir = self.output_dir.rstrip("/").rstrip("\\")
        if self.user_config.get("folder_name_platform"):
            output_dir = os.path.join(output_dir, stream_info.platform)
        if self.user_config.get("folder_name_author"):
            output_dir = os.path.join(output_dir, stream_info.anchor_name)
        if self.user_config.get("folder_name_time"):
            output_dir = os.path.join(output_dir, now[:10])
        if self.user_config.get("folder_name_title") and stream_info.title:
            live_title = self._clean_and_truncate_title(stream_info.title)
            if self.user_config.get("folder_name_time"):
                output_dir = os.path.join(output_dir, f"{live_title}_{stream_info.anchor_name}")
            else:
                output_dir = os.path.join(output_dir, f"{now[:10]}_{live_title}")
        os.makedirs(output_dir, exist_ok=True)
        self.recording.recording_dir = output_dir
        self.app.page.run_task(self.app.record_manager.persist_recordings)
        return output_dir

    def _get_save_path(self, filename: str, use_direct_download: bool = False) -> str:
        suffix = self.save_format
        suffix = "_%03d." + suffix if self.segment_record and not use_direct_download else "." + suffix
        save_file_path = os.path.join(self.output_dir, filename + suffix).replace(" ", "_")
        return save_file_path.replace("\\", "/")

    @staticmethod
    def _clean_and_truncate_title(title: str) -> str | None:
        if not title:
            return None
        cleaned_title = title[:30].replace("，", ",").replace(" ", "")
        return cleaned_title

    @property
    def is_flv_preferred_platform(self):
        return self.platform_key in {"douyin", "tiktok"}

    def _select_source_url(self, stream_info: StreamData):
        if self.user_config.get("default_live_source") != "HLS" and self.is_flv_preferred_platform:
            codec = utils.get_query_params(stream_info.flv_url, "codec")
            if codec and codec[0] == "h265":
                logger.warning("FLV is not supported for h265 codec, use HLS source instead")
            else:
                return stream_info.flv_url

        return stream_info.record_url

    def _get_record_url(self, stream_info: StreamData):
        url = self._select_source_url(stream_info)

        http_record_list = ["shopee", "migu"]
        if self.user_config.get("force_https_recording") and url.startswith("http://"):
            url = url.replace("http://", "https://")

        if self.platform_key in http_record_list:
            url = url.replace("https://", "http://")
        return url

    def set_preview_url(self, stream_info: StreamData):
        self.recording.preview_url = stream_info.m3u8_url or stream_info.flv_url

    def _get_record_format(self, stream_info: StreamData):
        use_flv_record = ["shopee"]
        if stream_info.flv_url:
            if self.platform_key in use_flv_record or self.recording.flv_use_direct_download:
                self.save_format = "flv"
                self.recording.record_format = self.save_format
                self.recording.segment_record = False
                return self.save_format, True

            elif self.save_format == "flv":
                codec = utils.get_query_params(stream_info.flv_url, "codec")
                if codec and codec[0] == "h265":
                    logger.warning("FLV is not supported for h265 codec, use TS format instead")
                    self.save_format = "ts"

        return self.save_format, False

    async def fetch_stream(self) -> StreamData:
        url = self.live_url
        from ...utils.logger import logger

        logger.info(f"Live URL: {url}")
        logger.info(f"Use Proxy: {self.proxy or None}")
        self.recording.use_proxy = bool(self.proxy)

        if risk_control.is_in_cooldown(url):
            logger.debug(f"URL {url} is in cooldown, skipping")
            return None

        if tiered_cache.should_force_refresh(url):
            logger.info(f"Force refresh for {url} (detection mode)")

        l2_status, l2_age = tiered_cache.get_l2(url)
        if l2_status is True and l2_age < 10:
            logger.info(f"Use cached live status for {url} ({l2_age}s ago)")

        handler = platform_handlers.get_platform_handler(
            live_url=url,
            proxy=self.proxy,
            cookies=self.cookies,
            record_quality=self.quality,
            platform=self.platform,
            username=self.account_config.get(self.platform_key, {}).get("username"),
            password=self.account_config.get(self.platform_key, {}).get("password"),
            account_type=self.account_config.get(self.platform_key, {}).get("account_type"),
        )

        stream_info = await handler.get_stream_info(url)
        self.recording.is_checking = False

        if stream_info and stream_info.anchor_name:
            l1_data = tiered_cache.get_l1(url)
            if l1_data:
                room_id = l1_data.get("room_id")
                if room_id:
                    self.recording.room_id = room_id
            tiered_cache.set_l2(url, stream_info.is_live)
            if stream_info.record_url:
                tiered_cache.set_l3(url, stream_info.record_url)
            tiered_cache.update_offline_count(url, increment=False)
            sticky_session.record_success(url)
            metrics_collector.record_success()
        else:
            self._handle_fetch_failure(url, stream_info)
            metrics_collector.record_failure("parse")

        return stream_info

    def _handle_fetch_failure(self, url: str, stream_info: StreamData):
        """处理获取失败的情况"""
        tiered_cache.update_offline_count(url, increment=True)
        poll_interval = tiered_cache.get_poll_interval(url)
        offline_count = tiered_cache._offline_count.get(url, 0)
        logger.warning(
            f"Fetch stream failed for {url}, consecutive_offline: {offline_count}, next_poll_in: {poll_interval}s"
        )
        sticky_session.record_failure(url)

    async def start_recording(self, stream_info: StreamData):
        """
        Construct ffmpeg recording parameters and start recording
        """

        self.save_format, use_direct_download = self._get_record_format(stream_info)
        filename = self._get_filename(stream_info)
        self.output_dir = self._get_output_dir(stream_info)
        save_path = self._get_save_path(filename, use_direct_download)
        logger.info(f"Save Path: {save_path}")
        self.recording.recording_dir = os.path.dirname(save_path)
        os.makedirs(self.recording.recording_dir, exist_ok=True)
        record_url = self._get_record_url(stream_info)
        self.set_preview_url(stream_info)

        try:
            if self.recording.rec_id in self.app.record_manager.active_recorders:
                old_recorder = self.app.record_manager.active_recorders[self.recording.rec_id]
                logger.warning(
                    f"Found existing recorder instance for {self.recording.rec_id}, id: {id(old_recorder)}, stopping it"
                )
                old_recorder.request_stop()

                await asyncio.sleep(1)

            self.app.record_manager.active_recorders[self.recording.rec_id] = self
            logger.info(f"Saved recorder instance for {self.recording.rec_id}, id: {id(self)}")
        except Exception as e:
            logger.error(f"Failed to save recorder instance: {e}")

        if use_direct_download:
            logger.info(f"Use Direct Downloader to Download FLV Stream: {record_url}")
            headers = {}
            header_params = self.get_headers_params(record_url, self.platform_key)
            if header_params:
                key, value = header_params.split(":", 1)
                headers[key] = value

            self.direct_downloader = DirectStreamDownloader(
                record_url=record_url,
                save_path=save_path,
                headers=headers,
                proxy=self.proxy,
                speed_callback=lambda speed: self._update_download_speed(speed),
            )

            self.app.page.run_task(
                self.start_direct_download,
                stream_info.anchor_name,
                self.live_url,
                record_url,
                save_path,
                self.save_format,
                self.user_config.get("custom_script_command"),
            )
        else:
            ffmpeg_builder = ffmpeg_builders.create_builder(
                self.save_format,
                record_url=record_url,
                proxy=self.proxy,
                segment_record=self.segment_record,
                segment_time=self.segment_time,
                full_path=save_path,
                headers=self.get_headers_params(record_url, self.platform_key),
            )
            ffmpeg_command = ffmpeg_builder.build_command()
            self.app.page.run_task(
                self.start_ffmpeg,
                stream_info.anchor_name,
                self.live_url,
                record_url,
                ffmpeg_command,
                self.save_format,
                self.user_config.get("custom_script_command"),
            )

    async def start_ffmpeg(
        self,
        record_name: str,
        live_url: str,
        record_url: str,
        ffmpeg_command: list,
        save_type: str,
        script_command: str | None = None,
    ) -> bool:
        """
        The child process executes ffmpeg for recording
        """

        logger.info(f"Starting ffmpeg recording - recorder id: {id(self)}, rec_id: {self.recording.rec_id}")
        self.should_stop = False

        try:
            save_file_path = ffmpeg_command[-1]

            process = await asyncio.create_subprocess_exec(
                *ffmpeg_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                startupinfo=self.subprocess_start_info,
            )

            self.app.add_ffmpeg_process(process)
            self.recording.status_info = RecordingStatus.RECORDING
            self.recording.record_url = record_url
            logger.info(f"Recording in Progress: {live_url}")
            logger.log("STREAM", f"Recording Stream URL: {record_url}")

            last_file_size = 0
            last_update_time = time.time()
            file_initialized = False
            output_dir = os.path.dirname(save_file_path)
            base_filename = os.path.basename(save_file_path)
            logger.info(
                f"Speed monitoring setup - save_file_path: {save_file_path}, output_dir: {output_dir}, base_filename: {base_filename}"
            )

            while True:
                if self.should_stop or self.recording.force_stop or not self.app.recording_enabled:
                    logger.info(f"Preparing to End Recording: {live_url}")

                    try:
                        if os.name == "nt":
                            if process.stdin:
                                process.stdin.write(b"q")
                                await process.stdin.drain()
                                await asyncio.sleep(5)
                        else:
                            import signal

                            process.send_signal(signal.SIGINT)
                            # process.terminate()
                            await asyncio.sleep(5)

                        if process.stdin:
                            process.stdin.close()

                        await asyncio.wait_for(process.wait(), timeout=15.0)
                    except asyncio.TimeoutError:
                        logger.warning(f"FFmpeg process did not exit gracefully, forcing termination: {live_url}")
                        process.kill()
                        await process.wait()
                    finally:
                        if process.stdin:
                            try:
                                process.stdin.close()
                            except Exception:
                                pass
                        process.stdin = None
                        if process.stdout:
                            try:
                                process.stdout.close()
                            except Exception:
                                pass
                        if process.stderr:
                            try:
                                process.stderr.close()
                            except Exception:
                                pass

                    self.recording.force_stop = False
                    break

                if process.returncode is not None:
                    logger.info(f"Exit loop recording (normal 0 | abnormal 1): code={process.returncode}, {live_url}")
                    self.recording.is_recording = False
                    break

                try:
                    if os.path.exists(save_file_path):
                        current_file_size = os.path.getsize(save_file_path)
                        current_time = time.time()

                        if not file_initialized:
                            file_initialized = True
                            last_file_size = current_file_size
                            last_update_time = current_time
                            logger.info(
                                f"Recording file initialized: {save_file_path}, size: {current_file_size} bytes"
                            )
                        elif current_time - last_update_time >= 1.0:
                            time_elapsed = current_time - last_update_time
                            size_diff = current_file_size - last_file_size

                            if time_elapsed > 0 and size_diff >= 0:
                                bytes_per_sec = size_diff / time_elapsed
                                mb_per_sec = bytes_per_sec / (1024 * 1024)
                                speed_str = (
                                    f"{mb_per_sec * 1024:.1f} KB/s" if mb_per_sec < 1 else f"{mb_per_sec:.2f} MB/s"
                                )
                                self.recording.speed = speed_str
                                logger.info(
                                    f"Recording speed updated: {speed_str}, size diff: {size_diff} bytes, elapsed: {time_elapsed:.2f}s"
                                )
                                if self.app.page:
                                    self.app.page.pubsub.send_all_on_topic("update", self.recording)
                            else:
                                logger.debug(
                                    f"Skipping speed update: time_elapsed={time_elapsed:.2f}, size_diff={size_diff}"
                                )

                            last_file_size = current_file_size
                            last_update_time = current_time
                    elif "%03d" in base_filename or "%d" in base_filename:
                        import glob

                        matching_files = glob.glob(
                            os.path.join(output_dir, base_filename.replace("%03d", "*").replace("%d", "*"))
                        )
                        if matching_files:
                            current_file_path = max(matching_files, key=os.path.getctime)
                            current_file_size = os.path.getsize(current_file_path)
                            current_time = time.time()

                            if not file_initialized:
                                file_initialized = True
                                last_file_size = current_file_size
                                last_update_time = current_time
                                logger.info(
                                    f"Recording file initialized (segmented): {current_file_path}, size: {current_file_size} bytes"
                                )
                            elif current_time - last_update_time >= 1.0:
                                time_elapsed = current_time - last_update_time
                                size_diff = current_file_size - last_file_size

                                if time_elapsed > 0 and size_diff >= 0:
                                    bytes_per_sec = size_diff / time_elapsed
                                    mb_per_sec = bytes_per_sec / (1024 * 1024)
                                    speed_str = (
                                        f"{mb_per_sec * 1024:.1f} KB/s" if mb_per_sec < 1 else f"{mb_per_sec:.2f} MB/s"
                                    )
                                    self.recording.speed = speed_str
                                    logger.info(
                                        f"Recording speed updated (segmented): {speed_str}, size diff: {size_diff} bytes, elapsed: {time_elapsed:.2f}s"
                                    )
                                    if self.app.page:
                                        self.app.page.pubsub.send_all_on_topic("update", self.recording)
                                else:
                                    logger.debug(
                                        f"Skipping speed update (segmented): time_elapsed={time_elapsed:.2f}, size_diff={size_diff}"
                                    )

                                last_file_size = current_file_size
                                last_update_time = current_time
                except Exception as e:
                    logger.debug(f"Failed to update recording speed: {e}")

                await asyncio.sleep(1)

            return_code = process.returncode
            safe_return_code = [0, 255]
            stdout, stderr = await process.communicate()
            if return_code not in safe_return_code and stderr:
                logger.error(f"FFmpeg Stderr Output: {str(stderr.decode()).splitlines()[0]}")
                self.recording.status_info = RecordingStatus.RECORDING_ERROR

                try:
                    self.app.record_manager.stop_recording(self.recording)
                    await self.app.record_card_manager.update_card(self.recording)
                    self.app.page.pubsub.send_others_on_topic("update", self.recording)
                    await self.app.snack_bar.show_snack_bar(
                        record_name + " " + self._["record_stream_error"], duration=2000
                    )
                except Exception as e:
                    logger.debug(f"Failed to update UI: {e}")

            if return_code in safe_return_code:
                if self.recording.monitor_status:
                    self.recording.status_info = RecordingStatus.MONITORING
                    display_title = self.recording.title
                else:
                    self.recording.status_info = RecordingStatus.STOPPED_MONITORING
                    display_title = self.recording.display_title

                self.recording.live_title = None
                if self.recording.manually_stopped:
                    logger.success(f"Live recording has stopped: {record_name}")
                else:
                    logger.success(f"Live recording completed: {record_name}")
                    self.app.page.run_task(self.end_message_push)

                try:
                    if self.recording.rec_id in self.app.record_manager.active_recorders:
                        del self.app.record_manager.active_recorders[self.recording.rec_id]
                        logger.info(f"Removed recorder from active_recorders: {self.recording.rec_id}")
                except Exception as e:
                    logger.error(f"Failed to remove recorder instance: {e}")

                try:
                    self.recording.update({"display_title": display_title})
                    self.app.page.run_task(self.app.record_card_manager.update_card, self.recording)
                    self.app.page.pubsub.send_others_on_topic("update", self.recording)
                    if not self.app.recording_enabled:
                        self.recording.status_info = RecordingStatus.NOT_RECORDING_SPACE
                        self.app.page.run_task(self.stop_recording_notify)

                except Exception as e:
                    logger.debug(f"Failed to update UI: {e}")

                if self.app.recording_enabled and not self.is_flv_preferred_platform:
                    self.app.page.run_task(self.app.record_manager.check_if_live, self.recording)

                if self.user_config.get("convert_to_mp4") and self.save_format == "ts":
                    if self.segment_record:
                        file_paths = utils.get_file_paths(os.path.dirname(save_file_path))
                        prefix = os.path.basename(save_file_path).rsplit("_", maxsplit=1)[0]
                        for path in file_paths:
                            if prefix in path:
                                await self.converts_mp4(path, self.user_config["delete_original"])
                    else:
                        await self.converts_mp4(save_file_path, self.user_config["delete_original"])

                if self.user_config.get("execute_custom_script") and script_command:
                    logger.info("Prepare a direct script in the background")
                    try:
                        self.app.page.run_task(
                            self.custom_script_execute,
                            script_command,
                            record_name,
                            save_file_path,
                            save_type,
                            self.segment_record,
                            self.user_config.get("convert_to_mp4"),
                        )
                        logger.success("Successfully added script execution")
                    except Exception as e:
                        logger.error(f"Failed to execute custom script: {e}")
                        await self.custom_script_execute(
                            script_command,
                            record_name,
                            save_file_path,
                            save_type,
                            self.segment_record,
                            self.user_config.get("convert_to_mp4"),
                        )

        except Exception as e:
            logger.error(f"An error occurred during the subprocess execution: {e}")
            self.recording.status_info = RecordingStatus.RECORDING_ERROR

            try:
                self.app.record_manager.stop_recording(self.recording)
                await self.app.record_card_manager.update_card(self.recording)
                self.app.page.pubsub.send_others_on_topic("update", self.recording)
                await self.app.snack_bar.show_snack_bar(record_name + " " + self._["no_ffmpeg_tip"], duration=4000)
            except Exception as e:
                logger.debug(f"Failed to update UI: {e}")
            return False
        finally:
            self.recording.record_url = None

        return True

    async def converts_mp4(self, converts_file_path: str, is_original_delete: bool = True) -> None:
        """Asynchronous transcoding method, can be added to the background service to continue execution"""
        if not self.app.recording_enabled:
            logger.info(f"Application is closing, adding transcoding task to background service: {converts_file_path}")
            BackgroundService.get_instance().add_task(self.converts_mp4_sync, converts_file_path, is_original_delete)
            return

        # Otherwise, execute transcoding normally
        await self._do_converts_mp4(converts_file_path, is_original_delete)

    def converts_mp4_sync(self, converts_file_path: str, is_original_delete: bool = True) -> None:
        """Synchronous version of the transcoding method, used for background service"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._do_converts_mp4(converts_file_path, is_original_delete))
        finally:
            loop.close()

    async def _do_converts_mp4(self, converts_file_path: str, is_original_delete: bool = True) -> None:
        """Actual execution method for transcoding"""
        converts_success = False
        save_path = None
        try:
            converts_file_path = converts_file_path.replace("\\", "/")
            if os.path.exists(converts_file_path) and os.path.getsize(converts_file_path) > 0:
                save_path = converts_file_path.rsplit(".", maxsplit=1)[0] + ".mp4"
                ffmpeg_command = [
                    "ffmpeg",
                    "-i",
                    converts_file_path,
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    "-f",
                    "mp4",
                    save_path,
                ]
                process = await asyncio.create_subprocess_exec(
                    *ffmpeg_command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    startupinfo=self.subprocess_start_info,
                )

                self.app.add_ffmpeg_process(process)
                task = asyncio.create_task(process.communicate())
                _, stderr = await task
                if process.returncode == 0:
                    converts_success = True
                    logger.info(f"Video transcoding completed: {save_path}")
                else:
                    logger.error(
                        f"Video transcoding failed! Error message: {stderr.decode() if stderr else 'Unknown error'}"
                    )

        except subprocess.CalledProcessError as e:
            logger.error(f"Video transcoding failed! Error message: {e.output.decode()}")

        try:
            if converts_success:
                if is_original_delete:
                    await asyncio.sleep(1)
                    if os.path.exists(converts_file_path):
                        os.remove(converts_file_path)
                    logger.info(f"Delete Original File: {converts_file_path}")
                else:
                    converts_dir = f"{os.path.dirname(save_path)}/original"
                    os.makedirs(converts_dir, exist_ok=True)
                    shutil.move(converts_file_path, converts_dir)
                    logger.info(f"Move Transcoding Files: {converts_file_path}")

        except subprocess.CalledProcessError as e:
            logger.error(f"Error occurred during conversion: {e}")
        except Exception as e:
            logger.error(f"An unknown error occurred: {e}")

    async def custom_script_execute(
        self,
        script_command: str,
        record_name: str,
        save_file_path: str,
        save_type: str,
        split_video_by_time: bool,
        converts_to_mp4: bool,
    ):
        from ..runtime.process_manager import BackgroundService

        if "python" in script_command:
            params = [
                f'--record_name "{record_name}"',
                f'--save_file_path "{save_file_path}"',
                f"--save_type {save_type}--split_video_by_time {split_video_by_time}",
                f"--converts_to_mp4 {converts_to_mp4}",
            ]
        else:
            params = [
                f'"{record_name.split(" ", maxsplit=1)[-1]}"',
                f'"{save_file_path}"',
                save_type,
                f"split_video_by_time: {split_video_by_time}",
                f"converts_to_mp4: {converts_to_mp4}",
            ]
        script_command = script_command.strip() + " " + " ".join(params)

        if not self.app.recording_enabled:
            logger.info("Application is closing, adding script execution task to background service")
            BackgroundService.get_instance().add_task(self.run_script_sync, script_command)
        else:
            self.app.page.run_task(self.run_script_async, script_command)

        logger.success("Script command execution initiated!")

    def run_script_sync(self, command: str) -> None:
        """Synchronous version of the script execution method, used for background service"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.run_script_async(command))
        finally:
            loop.close()

    async def run_script_async(self, command: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                *command.split(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                startupinfo=self.subprocess_start_info,
                text=False,
            )

            stdout, stderr = await process.communicate()

            if stdout:
                logger.info(stdout.splitlines()[0].decode())
            if stderr:
                logger.error(stderr.splitlines()[0].decode())

            if process.returncode != 0:
                logger.info(f"Custom Script process exited with return code {process.returncode}")

        except PermissionError:
            logger.error(
                "Script has no execution permission!, If it is a Linux environment, "
                "please first execute: chmod+x your_script.sh to grant script executable permission"
            )
        except OSError:
            logger.error("Please add `#!/bin/bash` at the beginning of your bash script file.")
        except Exception as e:
            logger.error(f"An error occurred: {e}")

    @staticmethod
    def get_headers_params(live_url, platform_key):
        live_domain = "/".join(live_url.split("/")[0:3])
        record_headers = {
            "pandalive": "origin:https://www.pandalive.co.kr",
            "winktv": "origin:https://www.winktv.co.kr",
            "popkontv": "origin:https://www.popkontv.com",
            "flextv": "origin:https://www.flextv.co.kr",
            "qiandurebo": "referer:https://qiandurebo.com",
            "17live": "referer:https://17.live/en/live/6302408",
            "lang": "referer:https://www.lang.live",
            "shopee": "origin:" + live_domain,
            "blued": "referer:https://app.blued.cn",
        }
        return record_headers.get(platform_key)

    async def start_direct_download(
        self,
        record_name: str,
        live_url: str,
        record_url: str,
        save_file_path: str,
        save_type: str,
        script_command: str | None = None,
    ) -> bool:
        """
        Use the direct downloader to download the live stream
        """

        logger.info(f"Starting direct download - recorder id: {id(self)}, rec_id: {self.recording.rec_id}")
        self.should_stop = False

        try:
            await self.direct_downloader.start_download()

            self.recording.status_info = RecordingStatus.RECORDING
            self.recording.record_url = record_url
            logger.info(f"Direct Downloading: {live_url}")
            logger.log("STREAM", f"Direct Download Stream URL: {record_url}")

            while True:
                if self.should_stop or self.recording.force_stop or not self.app.recording_enabled:
                    logger.info(f"Prepare to end direct download: {live_url}")
                    await self.direct_downloader.stop_download()
                    self.recording.force_stop = False
                    break

                await asyncio.sleep(1)

                if self.direct_downloader.download_task and self.direct_downloader.download_task.done():
                    break

            if self.recording.monitor_status:
                self.recording.status_info = RecordingStatus.MONITORING
                display_title = self.recording.title
            else:
                self.recording.status_info = RecordingStatus.STOPPED_MONITORING
                display_title = self.recording.display_title

            self.recording.live_title = None
            if self.recording.manually_stopped:
                logger.success(f"Direct Downloading Stopped: {record_name}")
            else:
                logger.success(f"Direct Downloading Completed: {record_name}")
                self.app.page.run_task(self.end_message_push)

                try:
                    if self.recording.rec_id in self.app.record_manager.active_recorders:
                        del self.app.record_manager.active_recorders[self.recording.rec_id]
                        logger.info(f"Removed recorder from active_recorders: {self.recording.rec_id}")
                except Exception as e:
                    logger.error(f"Failed to remove recorder instance: {e}")

                if self.app.recording_enabled and not self.is_flv_preferred_platform:
                    self.app.page.run_task(self.app.record_manager.check_if_live, self.recording)

            try:
                self.recording.update({"display_title": display_title})
                await self.app.record_card_manager.update_card(self.recording)
                self.app.page.pubsub.send_others_on_topic("update", self.recording)
                if not self.app.recording_enabled:
                    self.recording.status_info = RecordingStatus.NOT_RECORDING_SPACE
                    self.app.page.run_task(self.stop_recording_notify)

            except Exception as e:
                logger.debug(f"Failed to update UI: {e}")

            if self.user_config.get("execute_custom_script") and script_command:
                logger.info("Prepare to execute custom script in the background")
                try:
                    self.app.page.run_task(
                        self.custom_script_execute, script_command, record_name, save_file_path, save_type, False, False
                    )
                    logger.success("Successfully added script execution")
                except Exception as e:
                    logger.error(f"Failed to execute custom script: {e}")
                    await self.custom_script_execute(
                        script_command, record_name, save_file_path, save_type, False, False
                    )

            return True

        except Exception as e:
            logger.error(f"Error occurred during direct download: {e}")
            self.recording.status_info = RecordingStatus.RECORDING_ERROR

            try:
                self.app.record_manager.stop_recording(self.recording)
                await self.app.record_card_manager.update_card(self.recording)
                self.app.page.pubsub.send_others_on_topic("update", self.recording)
                await self.app.snack_bar.show_snack_bar(
                    record_name + " " + self._["record_stream_error"], duration=2000
                )
            except Exception as e:
                logger.debug(f"Failed to update UI: {e}")
            return False
        finally:
            self.recording.record_url = None

    async def stop_recording_notify(self):
        if desktop_notify.should_push_notification(self.app):
            desktop_notify.send_notification(
                title=self._["notify"],
                message=self.recording.streamer_name + " | " + self._["live_recording_stopped_message"],
                app_icon=self.app.tray_manager.icon_path,
            )

    async def end_message_push(self):
        msg_manager = message_pusher.MessagePusher(self.settings)
        user_config = self.settings.user_config

        if (
            self.app.recording_enabled
            and msg_manager.should_push_message(
                self.settings, self.recording, check_manually_stopped=True, message_type="end"
            )
            and not self.recording.notified_live_end
        ):
            self.recording.notified_live_end = True
            push_content = self._["push_content_end"]
            end_push_message_text = user_config.get("custom_stream_end_content")
            if end_push_message_text:
                push_content = end_push_message_text

            push_at = datetime.today().strftime("%Y-%m-%d %H:%M:%S")
            push_content = (
                push_content.replace("[room_name]", self.recording.streamer_name)
                .replace("[time]", push_at)
                .replace("[title]", self.recording.live_title or "None")
            )
            msg_title = user_config.get("custom_notification_title").strip()
            msg_title = msg_title or self._["status_notify"]

            self.app.page.run_task(msg_manager.push_messages, msg_title, push_content)

    def request_stop(self):
        logger.info(f"Stop requested for recorder: {self.recording.url}, rec_id: {self.recording.rec_id}")
        logger.info(f"Recorder instance details - id: {id(self)}, recording: {self.recording.title}")

        old_value = self.should_stop
        self.should_stop = True

        logger.info(f"Set should_stop from {old_value} to {self.should_stop} for recorder: {self.recording.rec_id}")

    def _update_download_speed(self, speed: str):
        self.recording.speed = speed
        if self.app.page:
            self.app.page.pubsub.send_all_on_topic("update", self.recording)
