"""
增强的直播录制器，集成进程监控、资源管理和错误恢复功能
"""

import asyncio
import signal
from typing import Optional, Dict, Any

from ...utils.logger import logger
from ..process_manager import RobustProcessManager, ProcessStatus
from ..resource_manager import ResourceManager, ResourceType
from .stream_manager import LiveStreamRecorder


class EnhancedLiveStreamRecorder(LiveStreamRecorder):
    """
    增强的直播录制器

    新增功能：
    1. 集成进程管理器，自动监控FFmpeg进程
    2. 集成资源管理器，自动清理资源
    3. 智能错误恢复机制
    4. 录制状态实时跟踪
    """

    def __init__(self, app, recording, recording_info):
        super().__init__(app, recording, recording_info)

        # 进程和资源管理器
        self.process_manager: Optional[RobustProcessManager] = getattr(app, 'process_manager', None)
        self.resource_manager: Optional[ResourceManager] = getattr(app, 'resource_manager', None)

        # 录制进程跟踪
        self.recording_process_id: Optional[str] = None
        self.monitor_task: Optional[asyncio.Task] = None
        self.error_recovery_enabled = True
        self.max_recovery_attempts = 3
        self.recovery_count = 0

        logger.info(f"增强录制器初始化完成: {self.live_url}")

    async def start_recording_with_enhancements(self) -> bool:
        """
        启动增强录制功能

        Returns:
            bool: 是否启动成功
        """
        try:
            logger.info(f"开始增强录制: {self.live_url}")

            # 注册录制进程到进程管理器
            process_id = f"recording_{self.recording.id}_{int(asyncio.get_event_loop().time())}"

            if self.process_manager:
                success = await self._register_managed_recording(process_id)
                if not success:
                    logger.error(f"注册管理录制失败: {process_id}")
                    return False

                self.recording_process_id = process_id

            # 启动录制状态监控
            await self._start_recording_monitor()

            # 更新录制状态为录制中
            await self._update_recording_status("recording")

            logger.info(f"增强录制启动成功: {process_id}")
            return True

        except Exception as e:
            logger.error(f"增强录制启动失败: {e}")
            await self._handle_recording_error(e)
            return False

    async def _register_managed_recording(self, process_id: str) -> bool:
        """注册管理录制到进程管理器"""
        if not self.process_manager:
            return False

        # 构建录制命令
        ffmpeg_command = await self._build_ffmpeg_command()
        if not ffmpeg_command:
            return False

        # 启动管理进程
        success = await self.process_manager.start_process(
            process_id=process_id,
            command=ffmpeg_command,
            max_restarts=self.max_recovery_attempts,
            restart_delay=5.0,
            health_check_interval=10.0,
            timeout=60.0
        )

        if success:
            # 注册状态回调
            self.process_manager.add_status_callback(self._on_recording_status_change)

            # 注册资源到资源管理器
            if self.resource_manager:
                process_info = self.process_manager.processes.get(process_id)
                if process_info:
                    await self.resource_manager.register_subprocess(
                        process_info.process,
                        process_name=f"recording_{self.recording.id}"
                    )

        return success

    async def _build_ffmpeg_command(self) -> Optional[list]:
        """构建FFmpeg命令"""
        try:
            # 这里应该调用原有的FFmpeg构建逻辑
            # 为了演示，我们使用简化版本
            stream_info = await platform_handlers.get_stream_info(
                self.live_url,
                self.quality,
                platform_cookies=self cookies
            )

            if not stream_info.stream_url:
                return None

            ffmpeg_builder = ffmpeg_builders.get_ffmpeg_builder(
                self.save_format,
                stream_info,
                self.output_dir,
                segment_time=self.segment_time,
                segment_record=self.segment_record,
                proxy=self.proxy
            )

            ffmpeg_command = ffmpeg_builder.build_command()
            logger.info(f"FFmpeg命令构建完成: {' '.join(ffmpeg_command)}")

            return ffmpeg_command

        except Exception as e:
            logger.error(f"FFmpeg命令构建失败: {e}")
            return None

    async def _start_recording_monitor(self) -> None:
        """启动录制状态监控"""
        if self.monitor_task:
            self.monitor_task.cancel()

        self.monitor_task = asyncio.create_task(
            self._monitor_recording_health()
        )
        logger.debug(f"录制监控任务已启动: {self.live_url}")

    async def _monitor_recording_health(self) -> None:
        """监控录制健康状况"""
        while not self.should_stop:
            try:
                # 检查录制进程状态
                if self.recording_process_id and self.process_manager:
                    status = self.process_manager.get_process_status(self.recording_process_id)

                    if status == ProcessStatus.CRASHED:
                        logger.warning(f"录制进程崩溃: {self.recording_process_id}")
                        await self._handle_recording_crash()
                    elif status == ProcessStatus.RESTARTING:
                        logger.info(f"录制进程重启中: {self.recording_process_id}")
                        await self._update_recording_status("restarting")

                # 检查磁盘空间
                await self._check_disk_space()

                # 检查录制文件大小
                await self._check_recording_file()

                await asyncio.sleep(30)  # 30秒检查一次

            except asyncio.CancelledError:
                logger.info("录制监控任务被取消")
                break
            except Exception as e:
                logger.error(f"录制监控异常: {e}")
                await asyncio.sleep(10)  # 出现异常时等待10秒

    async def _handle_recording_crash(self) -> None:
        """处理录制进程崩溃"""
        if not self.error_recovery_enabled or self.recovery_count >= self.max_recovery_attempts:
            logger.error(f"录制进程崩溃且恢复次数达到上限: {self.live_url}")
            await self._update_recording_status("error")
            await self.stop_recording()
            return

        self.recovery_count += 1
        logger.info(f"开始录制恢复 (第{self.recovery_count}/{self.max_recovery_attempts}次): {self.live_url}")

        await self._update_recording_status("recovering")

        # 等待进程管理器自动重启（这里可以添加额外的恢复逻辑）
        await asyncio.sleep(10)

    async def _check_disk_space(self) -> None:
        """检查磁盘空间"""
        try:
            import shutil
            total, used, free = shutil.disk_usage(self.output_dir)
            free_gb = free / (1024**3)

            # 如果剩余空间少于500MB，停止录制
            if free_gb < 0.5:
                logger.warning(f"磁盘空间不足 (剩余: {free_gb:.1f}GB)，停止录制: {self.live_url}")
                await self._update_recording_status("disk_full")
                await self.stop_recording()

        except Exception as e:
            logger.error(f"检查磁盘空间失败: {e}")

    async def _check_recording_file(self) -> None:
        """检查录制文件状态"""
        try:
            import os
            from pathlib import Path

            # 查找最近创建的录制文件
            output_path = Path(self.output_dir)
            recording_files = list(output_path.glob(f"*{self.recording.id}*"))

            if recording_files:
                latest_file = max(recording_files, key=os.path.getctime)
                file_size = latest_file.stat().st_size

                # 如果文件长时间没有增长，可能有问题
                current_time = asyncio.get_event_loop().time()
                file_age = current_time - latest_file.stat().st_mtime

                if file_age > 300 and file_size == 0:  # 5分钟没有增长且文件为空
                    logger.warning(f"录制文件疑似异常: {latest_file}")
                    await self._update_recording_status("file_error")

        except Exception as e:
            logger.debug(f"检查录制文件失败（可能是正常的）: {e}")

    async def _on_recording_status_change(self, process_id: str, status: ProcessStatus) -> None:
        """录制进程状态变化回调"""
        if process_id != self.recording_process_id:
            return

        status_mapping = {
            ProcessStatus.RUNNING: "recording",
            ProcessStatus.STOPPING: "stopping",
            ProcessStatus.STOPPED: "stopped",
            ProcessStatus.CRASHED: "error",
            ProcessStatus.RESTARTING: "recovering",
            ProcessStatus.STARTING: "starting"
        }

        recording_status = status_mapping.get(status, "unknown")
        if recording_status != "unknown":
            await self._update_recording_status(recording_status)

        logger.info(f"录制状态更新: {process_id} -> {recording_status}")

    async def _update_recording_status(self, status: str) -> None:
        """更新录制状态"""
        try:
            # 更新录制对象状态
            if hasattr(self.recording, 'status'):
                self.recording.status = status

            # 通知应用
            await self.app.recording_manager.update_recording(self.recording)

            # 发送状态变化事件
            if hasattr(self.app, 'page') and self.app.page:
                self.app.page.pubsub.send_all_on_topic("recording_status_update", {
                    'recording_id': self.recording.id,
                    'status': status
                })

        except Exception as e:
            logger.error(f"更新录制状态失败: {e}")

    async def _handle_recording_error(self, error: Exception) -> None:
        """处理录制错误"""
        logger.error(f"录制错误处理: {error}")

        # 发送错误通知
        try:
            await desktop_notify.notify_error(f"录制失败: {self.live_url}", str(error))
        except Exception as e:
            logger.debug(f"发送错误通知失败: {e}")

        await self._update_recording_status("error")

    async def stop_recording(self) -> None:
        """停止增强录制"""
        try:
            logger.info(f"开始停止增强录制: {self.live_url}")
            self.should_stop = True

            # 停止监控任务
            if self.monitor_task:
                self.monitor_task.cancel()
                try:
                    await self.monitor_task
                except asyncio.CancelledError:
                    pass

            # 停止录制进程
            if self.recording_process_id and self.process_manager:
                success = await self.process_manager.stop_process(self.recording_process_id)
                if success:
                    logger.info(f"录制进程已停止: {self.recording_process_id}")

                # 从资源管理器中移除
                if self.resource_manager:
                    self.resource_manager.unregister_resource(f"recording_{self.recording.id}")

            # 更新最终状态
            await self._update_recording_status("stopped")

            logger.info(f"增强录制已停止: {self.live_url}")

        except Exception as e:
            logger.error(f"停止增强录制失败: {e}")

    async def __aenter__(self):
        """异步上下文管理器入口"""
        success = await self.start_recording_with_enhancements()
        if not success:
            raise RuntimeError("Failed to start enhanced recording")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.stop_recording()

    def get_recording_stats(self) -> Dict[str, Any]:
        """获取录制统计信息"""
        stats = {
            'recording_id': self.recording.id,
            'live_url': self.live_url,
            'status': getattr(self.recording, 'status', 'unknown'),
            'recovery_count': self.recovery_count,
            'max_recovery_attempts': self.max_recovery_attempts,
            'error_recovery_enabled': self.error_recovery_enabled
        }

        if self.recording_process_id and self.process_manager:
            process_status = self.process_manager.get_process_status(self.recording_process_id)
            stats['process_status'] = process_status.value if process_status else 'not_found'

        return stats


def create_enhanced_recorder(app, recording, recording_info) -> EnhancedLiveStreamRecorder:
    """
    创建增强录制器实例的工厂函数

    Args:
        app: 应用实例
        recording: 录制对象
        recording_info: 录制信息

    Returns:
        EnhancedLiveStreamRecorder: 增强录制器实例
    """
    return EnhancedLiveStreamRecorder(app, recording, recording_info)