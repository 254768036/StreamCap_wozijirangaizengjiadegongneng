"""
健壮的进程管理器，提供进程监控、自动重启和资源清理功能
"""

import asyncio
import signal
import sys
import time
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum

from app.utils.logger import logger


class ProcessStatus(Enum):
    """进程状态枚举"""
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CRASHED = "crashed"
    RESTARTING = "restarting"


@dataclass
class ProcessInfo:
    """进程信息"""
    process_id: str
    process: asyncio.subprocess.Process
    status: ProcessStatus
    start_time: float
    last_restart_time: float
    restart_count: int
    max_restarts: int
    restart_delay: float  # 重启延迟（秒）
    command: List[str]
    monitor_task: Optional[asyncio.Task]
    health_check_interval: float
    timeout: float


class RobustProcessManager:
    """
    健壮的进程管理器

    功能：
    1. 进程自动监控和健康检查
    2. 异常进程自动重启
    3. 资源清理和优雅关闭
    4. 指数退避重启策略
    5. 进程状态通知
    """

    def __init__(self,
                 max_restarts: int = 3,
                 default_health_check_interval: float = 10.0,
                 default_timeout: float = 30.0):
        self.max_restarts = max_restarts
        self.default_health_check_interval = default_health_check_interval
        self.default_timeout = default_timeout

        self.processes: Dict[str, ProcessInfo] = {}
        self.status_callbacks: List[Callable[[str, ProcessStatus], None]] = []
        self.shutdown_event = asyncio.Event()

        logger.info(f"进程管理器初始化完成，最大重启次数: {max_restarts}")

    def add_status_callback(self, callback: Callable[[str, ProcessStatus], None]) -> None:
        """添加状态变化回调函数"""
        self.status_callbacks.append(callback)

    def _notify_status_change(self, process_id: str, status: ProcessStatus) -> None:
        """通知状态变化"""
        for callback in self.status_callbacks:
            try:
                callback(process_id, status)
            except Exception as e:
                logger.error(f"状态回调异常: {e}")

    async def start_process(self,
                          process_id: str,
                          command: List[str],
                          max_restarts: Optional[int] = None,
                          restart_delay: float = 2.0,
                          health_check_interval: Optional[float] = None,
                          timeout: Optional[float] = None) -> bool:
        """
        启动进程并开始监控

        Args:
            process_id: 进程唯一标识
            command: 启动命令列表
            max_restarts: 最大重启次数
            restart_delay: 重启延迟时间
            health_check_interval: 健康检查间隔
            timeout: 进程超时时间

        Returns:
            bool: 是否启动成功
        """
        if process_id in self.processes:
            logger.warning(f"进程 {process_id} 已存在，先停止现有进程")
            await self.stop_process(process_id, timeout=5.0)

        try:
            # 创建进程
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE
            )

            process_info = ProcessInfo(
                process_id=process_id,
                process=process,
                status=ProcessStatus.STARTING,
                start_time=time.time(),
                last_restart_time=0,
                restart_count=0,
                max_restarts=max_restarts or self.max_restarts,
                restart_delay=restart_delay,
                command=command,
                monitor_task=None,
                health_check_interval=health_check_interval or self.default_health_check_interval,
                timeout=timeout or self.default_timeout
            )

            self.processes[process_id] = process_info
            self._notify_status_change(process_id, ProcessStatus.STARTING)

            # 启动监控任务
            monitor_task = asyncio.create_task(
                self._monitor_process(process_id)
            )
            process_info.monitor_task = monitor_task

            logger.info(f"进程 {process_id} 启动成功 (PID: {process.pid})")
            return True

        except Exception as e:
            logger.error(f"启动进程 {process_id} 失败: {e}")
            return False

    async def _monitor_process(self, process_id: str) -> None:
        """监控进程状态"""
        process_info = self.processes.get(process_id)
        if not process_info:
            return

        logger.info(f"开始监控进程 {process_id}")
        process_info.status = ProcessStatus.RUNNING
        self._notify_status_change(process_id, ProcessStatus.RUNNING)

        while not self.shutdown_event.is_set():
            try:
                # 等待进程结束或超时
                timeout_coro = asyncio.wait_for(
                    process_info.process.wait(),
                    timeout=process_info.health_check_interval
                )

                try:
                    returncode = await timeout_coro
                    if returncode == 0:
                        logger.info(f"进程 {process_id} 正常退出")
                        process_info.status = ProcessStatus.STOPPED
                    else:
                        logger.error(f"进程 {process_id} 异常退出，返回码: {returncode}")
                        process_info.status = ProcessStatus.CRASHED
                except asyncio.TimeoutError:
                    # 进程仍在运行，继续监控
                    continue

                self._notify_status_change(process_id, process_info.status)

                # 如果进程异常退出，尝试重启
                if process_info.status == ProcessStatus.CRASHED:
                    if await self._should_restart(process_info):
                        await self._restart_process(process_info)
                    else:
                        logger.error(f"进程 {process_id} 达到最大重启次数，停止监控")
                        break

                return  # 退出监控循环

            except asyncio.CancelledError:
                logger.info(f"进程 {process_id} 监控任务被取消")
                break
            except Exception as e:
                logger.error(f"进程 {process_id} 监控异常: {e}")
                await asyncio.sleep(1.0)  # 短暂等待后继续

        logger.info(f"进程 {process_id} 监控结束")

    async def _should_restart(self, process_info: ProcessInfo) -> bool:
        """判断是否应该重启进程"""
        if process_info.restart_count >= process_info.max_restarts:
            return False

        # 检查重启时间间隔，避免过于频繁的重启
        current_time = time.time()
        if current_time - process_info.last_restart_time < process_info.restart_delay:
            logger.info(f"进程 {process_info.process_id} 重启间隔未到，延迟重启")
            await asyncio.sleep(process_info.restart_delay)
            return True

        return True

    async def _restart_process(self, process_info: ProcessInfo) -> None:
        """重启进程"""
        process_id = process_info.process_id

        logger.info(f"开始重启进程 {process_id} (第 {process_info.restart_count + 1} 次)")

        # 更新状态
        process_info.status = ProcessStatus.RESTARTING
        self._notify_status_change(process_id, ProcessStatus.RESTARTING)
        process_info.last_restart_time = time.time()
        process_info.restart_count += 1

        # 清理旧进程
        await self._cleanup_process(process_info.process)

        # 计算重启延迟（指数退避）
        restart_delay = process_info.restart_delay * (2 ** min(process_info.restart_count - 1, 5))
        logger.info(f"进程 {process_id} 将在 {restart_delay} 秒后重启")
        await asyncio.sleep(restart_delay)

        try:
            # 启动新进程
            new_process = await asyncio.create_subprocess_exec(
                *process_info.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE
            )

            process_info.process = new_process
            process_info.start_time = time.time()

            # 重新启动监控
            if process_info.monitor_task:
                process_info.monitor_task.cancel()
                try:
                    await process_info.monitor_task
                except asyncio.CancelledError:
                    pass

            process_info.monitor_task = asyncio.create_task(
                self._monitor_process(process_id)
            )

            logger.info(f"进程 {process_id} 重启成功 (新 PID: {new_process.pid})")

        except Exception as e:
            logger.error(f"重启进程 {process_id} 失败: {e}")
            process_info.status = ProcessStatus.CRASHED
            self._notify_status_change(process_id, ProcessStatus.CRASHED)

    async def _cleanup_process(self, process: asyncio.subprocess.Process) -> None:
        """清理进程资源"""
        if process.returncode is None:  # 进程仍在运行
            try:
                # 优雅终止
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                # 强制终止
                logger.warning("进程未优雅终止，强制杀死")
                process.kill()
                await process.wait()
            except Exception as e:
                logger.error(f"清理进程时出现异常: {e}")
                process.kill()

    async def stop_process(self, process_id: str, timeout: float = 10.0) -> bool:
        """
        停止指定进程

        Args:
            process_id: 进程ID
            timeout: 等待进程退出的超时时间

        Returns:
            bool: 是否成功停止
        """
        process_info = self.processes.get(process_id)
        if not process_info:
            logger.warning(f"进程 {process_id} 不存在")
            return False

        logger.info(f"开始停止进程 {process_id}")
        process_info.status = ProcessStatus.STOPPING
        self._notify_status_change(process_id, ProcessStatus.STOPPING)

        # 取消监控任务
        if process_info.monitor_task:
            process_info.monitor_task.cancel()
            try:
                await process_info.monitor_task
            except asyncio.CancelledError:
                pass

        # 停止进程
        await self._cleanup_process(process_info.process)

        process_info.status = ProcessStatus.STOPPED
        self._notify_status_change(process_id, ProcessStatus.STOPPED)

        # 从管理器中移除
        del self.processes[process_id]

        logger.info(f"进程 {process_id} 已停止")
        return True

    async def stop_all_processes(self, timeout: float = 10.0) -> None:
        """停止所有进程"""
        logger.info("开始停止所有进程...")

        # 取消所有监控任务
        for process_info in self.processes.values():
            if process_info.monitor_task:
                process_info.monitor_task.cancel()

        # 停止所有进程
        stop_tasks = []
        for process_id in list(self.processes.keys()):
            stop_tasks.append(self.stop_process(process_id, timeout))

        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        logger.info("所有进程已停止")

    def get_process_status(self, process_id: str) -> Optional[ProcessStatus]:
        """获取进程状态"""
        process_info = self.processes.get(process_id)
        return process_info.status if process_info else None

    def get_all_processes(self) -> Dict[str, ProcessStatus]:
        """获取所有进程状态"""
        return {pid: info.status for pid, info in self.processes.items()}

    async def cleanup(self) -> None:
        """清理所有资源"""
        self.shutdown_event.set()
        await self.stop_all_processes()
        logger.info("进程管理器资源清理完成")


# 全局进程管理器实例
process_manager = RobustProcessManager()


def setup_signal_handlers():
    """设置信号处理器"""
    def signal_handler(signum, frame):
        logger.info(f"收到信号 {signum}，开始清理资源...")
        asyncio.create_task(process_manager.cleanup())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if sys.platform != "win32":
        signal.signal(signal.SIGUSR1, signal_handler)
        signal.signal(signal.SIGUSR2, signal_handler)