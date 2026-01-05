"""
批量配置保存器，解决频繁IO操作导致的性能问题

功能：
1. 批量保存操作，减少磁盘IO
2. 防抖机制，避免短时间内重复保存
3. 异步保存，不阻塞主线程
4. 错误重试机制
5. 优先级队列，重要数据优先保存
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
import aiofiles
from collections import deque

from ...utils.logger import logger


class SavePriority(Enum):
    """保存优先级"""
    LOW = 1      # 一般配置
    NORMAL = 2   # 用户偏好
    HIGH = 3     # 录制状态
    CRITICAL = 4 # 重要记录


@dataclass
class SaveTask:
    """保存任务"""
    task_id: str
    data: Any
    file_path: Path
    priority: SavePriority
    created_time: float
    retry_count: int = 0
    max_retries: int = 3
    callback: Optional[Callable] = None


class BatchConfigSaver:
    """
    批量配置保存器

    使用防抖和批量技术优化配置保存性能
    """

    def __init__(self,
                 debounce_time: float = 2.0,
                 batch_size: int = 10,
                 max_queue_size: int = 100,
                 save_timeout: float = 30.0):
        self.debounce_time = debounce_time          # 防抖时间（秒）
        self.batch_size = batch_size                # 批量大小
        self.max_queue_size = max_queue_size        # 最大队列大小
        self.save_timeout = save_timeout            # 保存超时时间

        # 任务队列（按优先级排序）
        self.task_queue: deque[SaveTask] = deque(maxlen=max_queue_size)
        self.pending_save: Optional[SaveTask] = None

        # 任务调度
        self.save_timer: Optional[asyncio.Task] = None
        self.batch_processor: Optional[asyncio.Task] = None
        self.is_running = False

        # 统计信息
        self.save_count = 0
        self.error_count = 0
        self.last_save_time = 0

        # 回调函数
        self.error_callbacks: List[Callable[[Exception, SaveTask], None]] = []

        logger.info(f"批量配置保存器初始化: 防抖={debounce_time}s, 批量={batch_size}")

    def add_error_callback(self, callback: Callable[[Exception, SaveTask], None]) -> None:
        """添加错误回调"""
        self.error_callbacks.append(callback)

    async def start(self) -> None:
        """启动保存器"""
        if self.is_running:
            return

        self.is_running = True
        self.batch_processor = asyncio.create_task(self._batch_processor_loop())
        logger.info("批量配置保存器已启动")

    async def stop(self) -> None:
        """停止保存器"""
        self.is_running = False

        # 取消定时器
        if self.save_timer:
            self.save_timer.cancel()
            try:
                await self.save_timer
            except asyncio.CancelledError:
                pass

        # 取消批量处理器
        if self.batch_processor:
            self.batch_processor.cancel()
            try:
                await self.batch_processor
            except asyncio.CancelledError:
                pass

        # 保存剩余的任务
        await self._process_remaining_tasks()
        logger.info("批量配置保存器已停止")

    async def request_save(self,
                          task_id: str,
                          data: Any,
                          file_path: Path,
                          priority: SavePriority = SavePriority.NORMAL,
                          callback: Optional[Callable] = None) -> None:
        """
        请求保存配置

        Args:
            task_id: 任务ID
            data: 要保存的数据
            file_path: 保存路径
            priority: 保存优先级
            callback: 完成回调
        """
        if not self.is_running:
            logger.warning("保存器未运行，执行同步保存")
            await self._save_sync(data, file_path, callback)
            return

        # 创建保存任务
        task = SaveTask(
            task_id=task_id,
            data=data,
            file_path=file_path,
            priority=priority,
            created_time=time.time(),
            callback=callback
        )

        # 检查是否已存在相同任务
        existing_task = self._find_existing_task(task_id)
        if existing_task:
            # 更新现有任务（保留更高的优先级）
            existing_task.data = data
            existing_task.priority = max(existing_task.priority, priority)
            existing_task.created_time = time.time()
            logger.debug(f"更新现有保存任务: {task_id}")
        else:
            # 添加新任务到队列
            self._add_task_to_queue(task)
            logger.debug(f"添加保存任务: {task_id} (优先级: {priority.name})")

        # 重置防抖定时器
        await self._schedule_save()

    def _find_existing_task(self, task_id: str) -> Optional[SaveTask]:
        """查找现有任务"""
        for task in self.task_queue:
            if task.task_id == task_id:
                return task
        return None

    def _add_task_to_queue(self, task: SaveTask) -> None:
        """按优先级添加任务到队列"""
        # 找到插入位置（优先级从高到低）
        insert_pos = len(self.task_queue)
        for i, existing_task in enumerate(self.task_queue):
            if task.priority.value > existing_task.priority.value:
                insert_pos = i
                break

        self.task_queue.insert(insert_pos, task)

        # 如果队列满，移除最旧的低优先级任务
        if len(self.task_queue) == self.max_queue_size:
            oldest_task = self.task_queue.pop()
            logger.warning(f"队列已满，移除最旧任务: {oldest_task.task_id}")

    async def _schedule_save(self) -> None:
        """调度保存操作"""
        if self.save_timer:
            self.save_timer.cancel()

        self.save_timer = asyncio.create_task(self._debounced_save())

    async def _debounced_save(self) -> None:
        """防抖保存"""
        try:
            await asyncio.sleep(self.debounce_time)

            if self.task_queue:
                logger.debug(f"防抖保存触发，待处理任务数: {len(self.task_queue)}")
                await self._trigger_batch_save()
        except asyncio.CancelledError:
            logger.debug("防抖保存被取消")

    async def _trigger_batch_save(self) -> None:
        """触发批量保存"""
        # 获取一批任务
        batch_tasks = []
        for _ in range(min(self.batch_size, len(self.task_queue))):
            if self.task_queue:
                batch_tasks.append(self.task_queue.popleft())

        if batch_tasks:
            logger.info(f"开始批量保存 {len(batch_tasks)} 个任务")

            # 并行执行保存任务
            save_coroutines = [self._save_task(task) for task in batch_tasks]
            results = await asyncio.gather(*save_coroutines, return_exceptions=True)

            # 处理结果
            for i, result in enumerate(results):
                task = batch_tasks[i]
                if isinstance(result, Exception):
                    await self._handle_save_error(result, task)
                else:
                    self.save_count += 1
                    if task.callback:
                        try:
                            task.callback(True)
                        except Exception as e:
                            logger.error(f"保存回调执行失败: {e}")

            self.last_save_time = time.time()

    async def _save_task(self, task: SaveTask) -> None:
        """保存单个任务"""
        try:
            # 确保目录存在
            task.file_path.parent.mkdir(parents=True, exist_ok=True)

            # 序列化数据
            if not isinstance(task.data, str):
                json_data = json.dumps(task.data, ensure_ascii=False, indent=2)
            else:
                json_data = task.data

            # 异步写入文件
            async with aiofiles.open(task.file_path, mode='w', encoding='utf-8') as f:
                await f.write(json_data)

            logger.debug(f"配置保存成功: {task.task_id} -> {task.file_path}")

        except Exception as e:
            logger.error(f"保存任务失败: {task.task_id} -> {e}")
            raise

    async def _handle_save_error(self, error: Exception, task: SaveTask) -> None:
        """处理保存错误"""
        self.error_count += 1

        # 重试机制
        if task.retry_count < task.max_retries:
            task.retry_count += 1
            retry_delay = min(2 ** task.retry_count, 10)  # 指数退避，最大10秒

            logger.warning(f"保存任务 {task.task_id} 失败，{retry_delay}秒后重试 (第{task.retry_count}次)")

            await asyncio.sleep(retry_delay)
            self.task_queue.appendleft(task)  # 插入队列前端，优先处理
        else:
            logger.error(f"保存任务 {task.task_id} 重试次数超限，放弃保存")

            # 执行错误回调
            for callback in self.error_callbacks:
                try:
                    callback(error, task)
                except Exception as cb_error:
                    logger.error(f"错误回调执行失败: {cb_error}")

            # 执行任务回调（表示失败）
            if task.callback:
                try:
                    task.callback(False)
                except Exception as cb_error:
                    logger.error(f"任务错误回调执行失败: {cb_error}")

    async def _batch_processor_loop(self) -> None:
        """批量处理器循环"""
        while self.is_running:
            try:
                # 检查是否有待处理任务
                if self.task_queue:
                    # 检查是否有高优先级任务需要立即处理
                    should_process = False

                    for task in list(self.task_queue)[:self.batch_size]:
                        if task.priority.value >= SavePriority.HIGH.value:
                            should_process = True
                            break

                    # 或检查是否超过批量大小
                    if not should_process and len(self.task_queue) >= self.batch_size:
                        should_process = True

                    if should_process:
                        await self._trigger_batch_save()

                await asyncio.sleep(0.1)  # 减少CPU使用

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"批量处理器异常: {e}")
                await asyncio.sleep(1.0)

    async def _process_remaining_tasks(self) -> None:
        """处理剩余任务"""
        if not self.task_queue:
            return

        logger.info(f"处理剩余 {len(self.task_queue)} 个保存任务")

        # 按优先级处理剩余任务
        remaining_tasks = list(self.task_queue)
        self.task_queue.clear()

        save_coroutines = [self._save_task(task) for task in remaining_tasks]
        results = await asyncio.gather(*save_coroutines, return_exceptions=True)

        success_count = sum(1 for result in results if not isinstance(result, Exception))
        logger.info(f"剩余任务处理完成: 成功 {success_count}/{len(remaining_tasks)}")

    async def _save_sync(self, data: Any, file_path: Path, callback: Optional[Callable]) -> None:
        """同步保存（备用方案）"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            if not isinstance(data, str):
                json_data = json.dumps(data, ensure_ascii=False, indent=2)
            else:
                json_data = data

            with open(file_path, mode='w', encoding='utf-8') as f:
                f.write(json_data)

            if callback:
                callback(True)

        except Exception as e:
            logger.error(f"同步保存失败: {e}")
            if callback:
                callback(False)

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'is_running': self.is_running,
            'queue_size': len(self.task_queue),
            'save_count': self.save_count,
            'error_count': self.error_count,
            'success_rate': (self.save_count / max(1, self.save_count + self.error_count)) * 100,
            'last_save_time': self.last_save_time,
            'debounce_time': self.debounce_time,
            'batch_size': self.batch_size
        }

    def get_queue_status(self) -> Dict[str, Any]:
        """获取队列状态"""
        priority_counts = {}
        for priority in SavePriority:
            priority_counts[priority.name] = 0

        for task in self.task_queue:
            priority_counts[task.priority.name] += 1

        return {
            'total_tasks': len(self.task_queue),
            'by_priority': priority_counts,
            'pending_task': self.pending_save.task_id if self.pending_save else None
        }


# 全局配置保存器实例
batch_config_saver = BatchConfigSaver()


async def initialize_batch_saver() -> None:
    """初始化批量配置保存器"""
    await batch_config_saver.start()
    logger.info("批量配置保存器已初始化")


async def shutdown_batch_saver() -> None:
    """关闭批量配置保存器"""
    await batch_config_saver.stop()
    logger.info("批量配置保存器已关闭")