import asyncio
import os
import time

import flet as ft

from . import execute_dir
from .core.config.config_manager import ConfigManager
from .core.config.language_manager import LanguageManager
from .core.recording.record_manager import RecordingManager
from .core.runtime.process_manager import AsyncProcessManager
from .core.update.update_checker import UpdateChecker
from .core.process_manager import RobustProcessManager, setup_signal_handlers
from .core.resource_manager import ResourceManager
from .initialization.installation_manager import InstallationManager
from .ui.components.business.recording_card import RecordingCardManager
from .ui.components.common.show_snackbar import ShowSnackBar
from .ui.navigation.sidebar import LeftNavigationMenu, NavigationSidebar
from .ui.views.about_view import AboutPage
from .ui.views.home_view import HomePage
from .ui.views.recordings_view import RecordingsPage
from .ui.views.settings_view import SettingsPage
from .ui.views.storage_view import StoragePage
from .utils import utils
from .utils.logger import logger


class App:
    def __init__(self, page: ft.Page):
        self.install_progress = None
        self.page = page
        self.run_path = execute_dir
        self.assets_dir = os.path.join(execute_dir, "assets")

        # 核心管理器
        self.process_manager = AsyncProcessManager()
        self.config_manager = ConfigManager(self.run_path)

        # 新增的管理器
        self.robust_process_manager = RobustProcessManager()
        self.resource_manager = ResourceManager()

        self.is_web_mode = False
        self.auth_manager = None
        self.current_username = None
        self.content_area = ft.Column(
            controls=[],
            expand=True,
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

        self.settings = SettingsPage(self)
        self.language_manager = LanguageManager(self)
        self.language_code = self.settings.language_code
        self.about = AboutPage(self)
        self.recordings = RecordingsPage(self)
        self.home = HomePage(self)
        self.storage = StoragePage(self)
        self.pages = self.initialize_pages()
        self.sidebar = NavigationSidebar(self)
        self.left_navigation_menu = LeftNavigationMenu(self)

        self.snack_bar_area = ft.Container()
        self.dialog_area = ft.Container()
        self.complete_page = ft.Row(
            expand=True,
            controls=[
                self.left_navigation_menu,
                ft.VerticalDivider(width=1),
                self.content_area,
                self.dialog_area,
                self.snack_bar_area,
            ]
        )
        self.snack_bar = ShowSnackBar(self)
        self.subprocess_start_up_info = utils.get_startup_info()
        self.record_card_manager = RecordingCardManager(self)
        self.record_manager = RecordingManager(self)
        self.current_page = None
        self._loading_page = False
        self.recording_enabled = True
        self.install_manager = InstallationManager(self)
        self.update_checker = UpdateChecker(self)

        # 设置信号处理器
        setup_signal_handlers()

        # 初始化新增的管理器
        self.page.run_task(self._initialize_managers)

        # 启动原有的初始化任务
        self.page.run_task(self.install_manager.check_env)
        self.page.run_task(self.record_manager.check_free_space)
        self.page.run_task(self._check_for_updates)

    def initialize_pages(self):
        return {
            "settings": self.settings,
            "home": self.home,
            "recordings": self.recordings,
            "storage": self.storage,
            "about": self.about,
        }

    async def switch_page(self, page_name):
        if self._loading_page:
            return

        self._loading_page = True

        try:
            await self.clear_content_area()
            if page := self.pages.get(page_name):
                await self.settings.is_changed()
                self.current_page = page
                await page.load()
        finally:
            self._loading_page = False

    async def clear_content_area(self):
        self.content_area.clean()
        self.content_area.update()

    async def _initialize_managers(self) -> None:
        """初始化新增的管理器"""
        try:
            # 初始化资源管理器
            await self.resource_manager.initialize()
            logger.info("资源管理器初始化成功")

            # 注册清理任务
            self.resource_manager.add_cleanup_task(self._custom_cleanup)

            logger.info("增强管理器初始化完成")
        except Exception as e:
            logger.error(f"管理器初始化失败: {e}")

    async def cleanup(self):
        """增强的资源清理"""
        logger.info("开始应用清理...")

        cleanup_tasks = []

        # 清理原有进程管理器
        try:
            cleanup_tasks.append(self.process_manager.cleanup())
        except Exception as e:
            logger.error(f"原进程管理器清理失败: {e}")

        # 清理健壮进程管理器
        try:
            cleanup_tasks.append(self.robust_process_manager.cleanup())
        except Exception as e:
            logger.error(f"健壮进程管理器清理失败: {e}")

        # 清理资源管理器
        try:
            cleanup_tasks.append(self.resource_manager.cleanup_all())
        except Exception as e:
            logger.error(f"资源管理器清理失败: {e}")

        # 执行所有清理任务
        if cleanup_tasks:
            try:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"清理任务执行失败: {e}")

        logger.info("应用清理完成")

    async def _custom_cleanup(self) -> None:
        """自定义清理任务"""
        try:
            # 保存用户配置
            await self.config_manager.save_user_config(self.settings.user_config)

            # 停止录制任务
            if hasattr(self, 'record_manager'):
                await self.record_manager.stop_all_recordings()

            logger.info("自定义清理任务完成")
        except Exception as e:
            logger.error(f"自定义清理失败: {e}")

    def get_system_stats(self) -> dict:
        """获取系统统计信息"""
        stats = {
            'process_manager': self.process_manager.get_process_count(),
            'robust_processes': len(self.robust_process_manager.get_all_processes()),
            'resources': self.resource_manager.get_resource_stats(),
        }

        try:
            import psutil
            process = psutil.Process()
            stats['memory_mb'] = process.memory_info().rss / 1024 / 1024
            stats['cpu_percent'] = process.cpu_percent()
        except ImportError:
            stats['memory_mb'] = 'N/A'
            stats['cpu_percent'] = 'N/A'

        return stats

    def add_ffmpeg_process(self, process):
        self.process_manager.add_process(process)

    async def _check_for_updates(self):
        """Check for updates when the application starts"""
        try:
            if not self.update_checker.update_config["auto_check"]:
                return
                
            last_check_time = self.settings.user_config.get("last_update_check", 0)
            current_time = time.time()
            check_interval = self.update_checker.update_config["check_interval"]
            
            if current_time - last_check_time >= check_interval:
                update_info = await self.update_checker.check_for_updates()
                self.settings.user_config["last_update_check"] = current_time
                await self.config_manager.save_user_config(self.settings.user_config)

                if update_info.get("has_update", False):
                    await self.update_checker.show_update_dialog(update_info)
        except Exception as e:
            logger.error(f"Update check failed: {e}")
