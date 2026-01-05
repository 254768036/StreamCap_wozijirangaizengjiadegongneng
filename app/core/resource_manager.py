"""
资源管理器，提供统一的资源跟踪、清理和泄漏检测功能
"""

import asyncio
import gc
import os
import psutil
import threading
import weakref
from contextlib import asynccontextmanager
from typing import Set, Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum

from app.utils.logger import logger


class ResourceType(Enum):
    """资源类型枚举"""
    ASYNC_TASK = "async_task"
    SUBPROCESS = "subprocess"
    FILE_HANDLE = "file_handle"
    NETWORK_CONNECTION = "network_connection"
    MEMORY_ALLOC = "memory_alloc"
    CUSTOM = "custom"


@dataclass
class ResourceInfo:
    """资源信息"""
    resource_id: str
    resource_type: ResourceType
    resource: Any
    created_time: float
    cleanup_func: Optional[Callable] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ResourceTracker:
    """资源跟踪器，用于检测资源泄漏"""

    def __init__(self, max_resources: int = 1000):
        self.max_resources = max_resources
        self.resources: Dict[str, ResourceInfo] = {}
        self.lock = threading.Lock()
        self.cleanup_callbacks: List[Callable[[str, ResourceInfo], None]] = []

    def add_cleanup_callback(self, callback: Callable[[str, ResourceInfo], None]) -> None:
        """添加清理回调"""
        self.cleanup_callbacks.append(callback)

    def register_resource(self,
                         resource_id: str,
                         resource: Any,
                         resource_type: ResourceType,
                         cleanup_func: Optional[Callable] = None,
                         **metadata) -> None:
        """注册资源"""
        with self.lock:
            if len(self.resources) >= self.max_resources:
                logger.warning(f"资源数量超过限制 ({self.max_resources})，可能存在资源泄漏")
                self._check_resource_leak()

            self.resources[resource_id] = ResourceInfo(
                resource_id=resource_id,
                resource_type=resource_type,
                resource=resource,
                created_time=asyncio.get_event_loop().time(),
                cleanup_func=cleanup_func,
                metadata=metadata
            )

            logger.debug(f"注册资源 {resource_id} (类型: {resource_type.value})")

    def unregister_resource(self, resource_id: str) -> None:
        """注销资源"""
        with self.lock:
            if resource_id in self.resources:
                resource_info = self.resources.pop(resource_id)
                logger.debug(f"注销资源 {resource_id}")

                # 执行清理回调
                for callback in self.cleanup_callbacks:
                    try:
                        callback(resource_id, resource_info)
                    except Exception as e:
                        logger.error(f"资源清理回调异常: {e}")

    async def cleanup_resource(self, resource_id: str) -> bool:
        """清理指定资源"""
        with self.lock:
            resource_info = self.resources.get(resource_id)
            if not resource_info:
                return False

        try:
            # 执行自定义清理函数
            if resource_info.cleanup_func:
                if asyncio.iscoroutinefunction(resource_info.cleanup_func):
                    await resource_info.cleanup_func()
                else:
                    resource_info.cleanup_func()

            # 根据资源类型执行特定的清理逻辑
            await self._cleanup_resource_by_type(resource_info)

            self.unregister_resource(resource_id)
            return True

        except Exception as e:
            logger.error(f"清理资源 {resource_id} 失败: {e}")
            return False

    async def _cleanup_resource_by_type(self, resource_info: ResourceInfo) -> None:
        """根据资源类型清理资源"""
        resource = resource_info.resource

        if resource_info.resource_type == ResourceType.ASYNC_TASK:
            if isinstance(resource, asyncio.Task) and not resource.done():
                resource.cancel()
                try:
                    await resource
                except asyncio.CancelledError:
                    pass

        elif resource_info.resource_type == ResourceType.SUBPROCESS:
            if hasattr(resource, 'terminate'):
                try:
                    resource.terminate()
                    await asyncio.wait_for(resource.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    resource.kill()
                    await resource.wait()

        elif resource_info.resource_type == ResourceType.FILE_HANDLE:
            if hasattr(resource, 'close'):
                resource.close()

    async def cleanup_all(self) -> None:
        """清理所有资源"""
        logger.info("开始清理所有资源...")

        resource_ids = list(self.resources.keys())
        cleanup_tasks = []

        for resource_id in resource_ids:
            cleanup_tasks.append(self.cleanup_resource(resource_id))

        if cleanup_tasks:
            results = await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            failed_count = sum(1 for result in results if isinstance(result, Exception))

            if failed_count > 0:
                logger.warning(f"{failed_count} 个资源清理失败")

        logger.info("所有资源清理完成")

    def _check_resource_leak(self) -> None:
        """检查资源泄漏"""
        resource_stats = {}
        for resource_info in self.resources.values():
            resource_type = resource_info.resource_type.value
            resource_stats[resource_type] = resource_stats.get(resource_type, 0) + 1

        logger.warning(f"资源统计: {resource_stats}")

        # 检查长时间未释放的资源
        current_time = asyncio.get_event_loop().time()
        for resource_info in list(self.resources.values()):
            age = current_time - resource_info.created_time
            if age > 300:  # 5分钟
                logger.warning(f"资源 {resource_info.resource_id} 已存在 {age:.1f} 秒，可能存在泄漏")

    def get_resource_stats(self) -> Dict[str, Any]:
        """获取资源统计信息"""
        with self.lock:
            stats = {}
            for resource_info in self.resources.values():
                resource_type = resource_info.resource_type.value
                stats[resource_type] = stats.get(resource_type, 0) + 1

            return {
                'total_resources': len(self.resources),
                'by_type': stats,
                'max_allowed': self.max_resources
            }


class MemoryManager:
    """内存管理器"""

    def __init__(self, check_interval: float = 30.0, memory_threshold_mb: float = 1024.0):
        self.check_interval = check_interval
        self.memory_threshold_mb = memory_threshold_mb
        self.monitoring_active = False
        self.monitor_task: Optional[asyncio.Task] = None

    async def start_monitoring(self) -> None:
        """开始内存监控"""
        if self.monitoring_active:
            return

        self.monitoring_active = True
        self.monitor_task = asyncio.create_task(self._monitor_memory())
        logger.info("内存监控已启动")

    async def stop_monitoring(self) -> None:
        """停止内存监控"""
        self.monitoring_active = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("内存监控已停止")

    async def _monitor_memory(self) -> None:
        """监控内存使用"""
        process = psutil.Process()

        while self.monitoring_active:
            try:
                memory_info = process.memory_info()
                memory_mb = memory_info.rss / 1024 / 1024

                if memory_mb > self.memory_threshold_mb:
                    logger.warning(f"内存使用过高: {memory_mb:.1f} MB (阈值: {self.memory_threshold_mb:.1f} MB)")
                    await self._handle_high_memory_usage()

                # 记录内存使用情况
                logger.debug(f"内存使用: {memory_mb:.1f} MB")

                await asyncio.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"内存监控异常: {e}")
                await asyncio.sleep(self.check_interval)

    async def _handle_high_memory_usage(self) -> None:
        """处理内存使用过高情况"""
        logger.info("开始处理高内存使用...")

        # 强制垃圾回收
        collected = gc.collect()
        logger.info(f"垃圾回收释放了 {collected} 个对象")

        # 记录详细内存信息
        process = psutil.Process()
        memory_info = process.memory_info()
        logger.info(f"垃圾回收后内存使用: {memory_info.rss / 1024 / 1024:.1f} MB")

        # 可以在这里添加更多优化措施，比如清理缓存等

    def get_memory_info(self) -> Dict[str, float]:
        """获取内存信息"""
        process = psutil.Process()
        memory_info = process.memory_info()

        return {
            'rss_mb': memory_info.rss / 1024 / 1024,
            'vms_mb': memory_info.vms / 1024 / 1024,
            'percent': process.memory_percent(),
            'threshold_mb': self.memory_threshold_mb
        }


class ResourceManager:
    """
    资源管理器 - 统一管理所有系统资源

    功能：
    1. 资源注册和跟踪
    2. 自动资源清理
    3. 内存使用监控
    4. 资源泄漏检测
    5. 优雅关闭处理
    """

    def __init__(self):
        self.resource_tracker = ResourceTracker()
        self.memory_manager = MemoryManager()
        self.cleanup_tasks: List[Callable] = []
        self._initialized = False

    async def initialize(self) -> None:
        """初始化资源管理器"""
        if self._initialized:
            return

        # 启动内存监控
        await self.memory_manager.start_monitoring()

        # 注册清理回调
        self.resource_tracker.add_cleanup_callback(self._on_resource_cleanup)

        logger.info("资源管理器初始化完成")
        self._initialized = True

    async def register_async_task(self,
                                 task: asyncio.Task,
                                 task_name: Optional[str] = None,
                                 **metadata) -> str:
        """注册异步任务"""
        task_name = task_name or f"task_{id(task)}"

        async def cleanup_task():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self.resource_tracker.register_resource(
            resource_id=task_name,
            resource=task,
            resource_type=ResourceType.ASYNC_TASK,
            cleanup_func=cleanup_task,
            **metadata
        )

        return task_name

    async def register_subprocess(self,
                                 process: asyncio.subprocess.Process,
                                 process_name: Optional[str] = None,
                                 **metadata) -> str:
        """注册子进程"""
        process_name = process_name or f"process_{process.pid}"

        async def cleanup_process():
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()

        self.resource_tracker.register_resource(
            resource_id=process_name,
            resource=process,
            resource_type=ResourceType.SUBPROCESS,
            cleanup_func=cleanup_process,
            **metadata
        )

        return process_name

    async def unregister_resource(self, resource_id: str) -> bool:
        """注销资源"""
        return await self.resource_tracker.cleanup_resource(resource_id)

    async def cleanup_resource(self, resource_id: str) -> bool:
        """清理指定资源"""
        return await self.resource_tracker.cleanup_resource(resource_id)

    async def cleanup_all(self) -> None:
        """清理所有资源"""
        logger.info("开始清理所有资源...")

        # 执行自定义清理任务
        for cleanup_task in self.cleanup_tasks:
            try:
                if asyncio.iscoroutinefunction(cleanup_task):
                    await cleanup_task()
                else:
                    cleanup_task()
            except Exception as e:
                logger.error(f"自定义清理任务异常: {e}")

        # 清理跟踪的资源
        await self.resource_tracker.cleanup_all()

        # 停止内存监控
        await self.memory_manager.stop_monitoring()

        logger.info("所有资源清理完成")

    def add_cleanup_task(self, cleanup_func: Callable) -> None:
        """添加清理任务"""
        self.cleanup_tasks.append(cleanup_func)

    def _on_resource_cleanup(self, resource_id: str, resource_info: ResourceInfo) -> None:
        """资源清理回调"""
        logger.debug(f"资源 {resource_id} 已清理")

    def get_resource_stats(self) -> Dict[str, Any]:
        """获取资源统计"""
        stats = self.resource_tracker.get_resource_stats()
        stats['memory'] = self.memory_manager.get_memory_info()
        return stats

    @asynccontextmanager
    async def managed_resource(self,
                             resource_id: str,
                             resource_factory: Callable,
                             resource_type: ResourceType,
                             cleanup_func: Optional[Callable] = None):
        """
        资源管理上下文管理器

        Args:
            resource_id: 资源ID
            resource_factory: 资源创建函数
            resource_type: 资源类型
            cleanup_func: 自定义清理函数

        Usage:
            async with resource_manager.managed_resource(
                "my_task",
                lambda: asyncio.create_task(my_coroutine()),
                ResourceType.ASYNC_TASK
            ) as task:
                await task
        """
        resource = resource_factory()

        # 注册资源
        self.resource_tracker.register_resource(
            resource_id=resource_id,
            resource=resource,
            resource_type=resource_type,
            cleanup_func=cleanup_func
        )

        try:
            yield resource
        finally:
            # 自动清理资源
            await self.resource_tracker.cleanup_resource(resource_id)


# 全局资源管理器实例
resource_manager = ResourceManager()