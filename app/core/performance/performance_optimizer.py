"""
性能优化器，提供缓存、批处理和性能监控功能

功能：
1. 智能缓存机制（内存LRU + 基于时间的缓存）
2. 性能监控和分析
3. 批量操作优化
4. 内存使用优化
5. 热路径识别和优化
"""

import time
import functools
import asyncio
import weakref
from typing import Any, Callable, Dict, Optional, List, TypeVar, Union
from dataclasses import dataclass, field
from collections import OrderedDict
import inspect
import threading

from ...utils.logger import logger

T = TypeVar('T')


@dataclass
class PerformanceMetrics:
    """性能指标"""
    call_count: int = 0
    total_time: float = 0.0
    avg_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0
    last_call_time: float = 0.0
    hit_rate: float = 0.0
    error_count: int = 0


class TimedLRUCache:
    """
    基于时间和LRU的混合缓存
    """

    def __init__(self, maxsize: int = 128, ttl: float = 300.0):
        self.maxsize = maxsize
        self.ttl = ttl  # 生存时间（秒）

        # 使用OrderedDict实现LRU
        self.cache: OrderedDict = OrderedDict()
        self.timestamps: Dict[Any, float] = {}
        self.lock = threading.RLock()

        # 统计信息
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: Any) -> Optional[Any]:
        """获取缓存值"""
        with self.lock:
            if key not in self.cache:
                self.misses += 1
                return None

            # 检查是否过期
            if time.time() - self.timestamps[key] > self.ttl:
                self.evictions += 1
                del self.cache[key]
                del self.timestamps[key]
                self.misses += 1
                return None

            # 更新LRU顺序
            value = self.cache.pop(key)
            self.cache[key] = value
            self.hits += 1

            return value

    def put(self, key: Any, value: Any) -> None:
        """存放缓存值"""
        with self.lock:
            current_time = time.time()

            # 如果已存在，更新
            if key in self.cache:
                self.cache.pop(key)
            # 如果缓存已满，移除最旧的
            elif len(self.cache) >= self.maxsize:
                oldest_key, _ = self.cache.popitem(last=False)
                del self.timestamps[oldest_key]
                self.evictions += 1

            self.cache[key] = value
            self.timestamps[key] = current_time

    def clear(self) -> None:
        """清空缓存"""
        with self.lock:
            self.cache.clear()
            self.timestamps.clear()

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0

        return {
            'size': len(self.cache),
            'maxsize': self.maxsize,
            'hits': self.hits,
            'misses': self.misses,
            'evictions': self.evictions,
            'hit_rate': hit_rate,
            'ttl': self.ttl
        }


class PerformanceMonitor:
    """性能监控器"""

    def __init__(self):
        self.metrics: Dict[str, PerformanceMetrics] = {}
        self.function_calls: Dict[str, List[float]] = {}
        self.lock = threading.RLock()

    def record_call(self, func_name: str, duration: float, success: bool = True) -> None:
        """记录函数调用"""
        with self.lock:
            if func_name not in self.metrics:
                self.metrics[func_name] = PerformanceMetrics()

            metric = self.metrics[func_name]
            metric.call_count += 1
            metric.total_time += duration
            metric.avg_time = metric.total_time / metric.call_count
            metric.min_time = min(metric.min_time, duration)
            metric.max_time = max(metric.max_time, duration)
            metric.last_call_time = time.time()

            if not success:
                metric.error_count += 1

            # 记录调用时间历史（用于趋势分析）
            if func_name not in self.function_calls:
                self.function_calls[func_name] = []
            self.function_calls[func_name].append(duration)

            # 保持最近100次调用记录
            if len(self.function_calls[func_name]) > 100:
                self.function_calls[func_name] = self.function_calls[func_name][-100:]

    def get_metrics(self, func_name: str) -> Optional[PerformanceMetrics]:
        """获取性能指标"""
        with self.lock:
            return self.metrics.get(func_name)

    def get_all_metrics(self) -> Dict[str, PerformanceMetrics]:
        """获取所有性能指标"""
        with self.lock:
            return self.metrics.copy()

    def identify_hot_paths(self, threshold: float = 0.1) -> List[str]:
        """识别热路径（平均执行时间超过阈值的函数）"""
        hot_paths = []
        with self.lock:
            for func_name, metric in self.metrics.items():
                if metric.avg_time > threshold:
                    hot_paths.append(func_name)

        return sorted(hot_paths, key=lambda x: self.metrics[x].avg_time, reverse=True)

    def get_performance_report(self) -> str:
        """生成性能报告"""
        with self.lock:
            if not self.metrics:
                return "暂无性能数据"

            total_calls = sum(m.call_count for m in self.metrics.values())
            total_time = sum(m.total_time for m in self.metrics.values())

            report = [
                "=== 性能报告 ===",
                f"总调用次数: {total_calls}",
                f"总执行时间: {total_time:.3f}s",
                f"平均每次调用: {(total_time / total_calls) * 1000:.2f}ms",
                ""
            ]

            # 找出最耗时的函数
            sorted_metrics = sorted(
                self.metrics.items(),
                key=lambda x: x[1].avg_time,
                reverse=True
            )[:10]

            report.append("=== 最耗时的函数 ===")
            for func_name, metric in sorted_metrics:
                report.append(
                    f"{func_name}: "
                    f"调用{metric.call_count}次, "
                    f"平均{metric.avg_time * 1000:.2f}ms, "
                    f"总计{metric.total_time:.3f}s"
                )

            # 找出调用次数最多的函数
            sorted_by_count = sorted(
                self.metrics.items(),
                key=lambda x: x[1].call_count,
                reverse=True
            )[:10]

            report.append("\n=== 调用次数最多的函数 ===")
            for func_name, metric in sorted_by_count:
                report.append(
                    f"{func_name}: {metric.call_count}次"
                )

            return "\n".join(report)


# 全局性能监控器
performance_monitor = PerformanceMonitor()


def performance_cache(maxsize: int = 128, ttl: float = 300.0):
    """
    性能缓存装饰器

    Args:
        maxsize: 最大缓存大小
        ttl: 缓存生存时间（秒）
    """
    cache = TimedLRUCache(maxsize, ttl)

    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                # 创建缓存键
                cache_key = _make_cache_key(func.__name__, args, kwargs)

                # 尝试从缓存获取
                result = cache.get(cache_key)
                if result is not None:
                    return result

                # 执行函数
                start_time = time.time()
                try:
                    result = await func(*args, **kwargs)
                    cache.put(cache_key, result)
                    return result
                except Exception as e:
                    # 缓存异常结果的时间较短
                    cache.put(cache_key, e)
                    raise
                finally:
                    duration = time.time() - start_time
                    performance_monitor.record_call(func.__name__, duration, True)

        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                cache_key = _make_cache_key(func.__name__, args, kwargs)

                result = cache.get(cache_key)
                if result is not None:
                    if isinstance(result, Exception):
                        raise result
                    return result

                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    cache.put(cache_key, result)
                    return result
                except Exception as e:
                    cache.put(cache_key, e)
                    raise
                finally:
                    duration = time.time() - start_time
                    performance_monitor.record_call(func.__name__, duration, True)

        # 添加缓存统计到函数
        wrapper._cache = cache
        return wrapper

    return decorator


def performance_monitoring(include_args: bool = False):
    """
    性能监控装饰器

    Args:
        include_args: 是否在统计中包含参数信息
    """
    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                func_name = func.__name__

                if include_args:
                    func_name_with_args = f"{func_name}{args}{kwargs}"
                else:
                    func_name_with_args = func_name

                try:
                    result = await func(*args, **kwargs)
                    duration = time.time() - start_time
                    performance_monitor.record_call(func_name_with_args, duration, True)
                    return result
                except Exception as e:
                    duration = time.time() - start_time
                    performance_monitor.record_call(func_name_with_args, duration, False)
                    raise

        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                func_name = func.__name__

                if include_args:
                    func_name_with_args = f"{func_name}{args}{kwargs}"
                else:
                    func_name_with_args = func_name

                try:
                    result = func(*args, **kwargs)
                    duration = time.time() - start_time
                    performance_monitor.record_call(func_name_with_args, duration, True)
                    return result
                except Exception as e:
                    duration = time.time() - start_time
                    performance_monitor.record_call(func_name_with_args, duration, False)
                    raise

        return wrapper

    return decorator


def batch_processing(min_batch_size: int = 3, max_wait_time: float = 1.0):
    """
    批处理装饰器，将多个调用批量处理

    Args:
        min_batch_size: 最小批量大小
        max_wait_time: 最大等待时间（秒）
    """
    def decorator(func: T) -> T:
        if not inspect.iscoroutinefunction(func):
            raise ValueError("批处理装饰器只能用于异步函数")

        # 批处理队列
        batch_queue = []
        batch_event = asyncio.Event()
        batch_lock = asyncio.Lock()

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            # 创建 Future 用于返回结果
            future = asyncio.Future()

            async def process_batch():
                """处理批量任务"""
                await asyncio.sleep(max_wait_time)

                async with batch_lock:
                    if not batch_queue:
                        return

                    # 取出批量任务
                    current_batch = batch_queue[:]
                    batch_queue.clear()

                if len(current_batch) >= min_batch_size:
                    try:
                        # 重新组织参数为批量格式
                        batch_args = []
                        batch_futures = []
                        for future_args, future_kwargs, future in current_batch:
                            batch_args.append((future_args, future_kwargs))
                            batch_futures.append(future)

                        # 调用批处理函数
                        results = await func(batch_args)

                        # 返回结果
                        for future, result in zip(batch_futures, results):
                            if not future.done():
                                future.set_result(result)

                    except Exception as e:
                        # 设置异常到所有 futures
                        for future in batch_futures:
                            if not future.done():
                                future.set_exception(e)

                else:
                    # 批量大小不足，单独处理
                    for future_args, future_kwargs, future in current_batch:
                        if not future.done():
                            try:
                                result = await func(*future_args, **future_kwargs)
                                future.set_result(result)
                            except Exception as e:
                                future.set_exception(e)

            # 异步任务排队
            async with batch_lock:
                batch_queue.append((args, kwargs, future))
                queue_size = len(batch_queue)

                # 如果达到最小批量大小，立即处理
                if queue_size >= min_batch_size:
                    asyncio.create_task(process_batch())
                else:
                    # 否则等待更多任务或超时
                    if not batch_event.is_set():
                        asyncio.create_task(process_batch())

            return await future

        return async_wrapper

    return decorator


def _make_cache_key(func_name: str, args: tuple, kwargs: dict) -> str:
    """创建缓存键"""
    try:
        # 尝试创建可哈希的键
        key_parts = [func_name]

        # 处理位置参数
        for arg in args:
            if isinstance(arg, (str, int, float, bool, type(None))):
                key_parts.append(str(arg))
            else:
                key_parts.append(str(hash(str(arg))))

        # 处理关键字参数
        if kwargs:
            for k, v in sorted(kwargs.items()):
                key_parts.append(f"{k}={v}")

        return "|".join(key_parts)
    except Exception:
        # 如果创建键失败，使用默认键
        return f"{func_name}|{hash(str(args) + str(kwargs))}"


def optimize_memory_usage():
    """
    内存使用优化装饰器
    """
    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                # 执行前清理循环引用
                import gc
                gc.collect()

                start_time = time.time()
                try:
                    result = await func(*args, **kwargs)

                    # 执行后再次清理
                    gc.collect()
                    return result
                finally:
                    duration = time.time() - start_time
                    performance_monitor.record_call(f"{func.__name__}_optimized", duration, True)

        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                import gc
                gc.collect()

                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    gc.collect()
                    return result
                finally:
                    duration = time.time() - start_time
                    performance_monitor.record_call(f"{func.__name__}_optimized", duration, True)

        return wrapper

    return decorator


# 导出便捷的装饰器别名
cache = performance_cache
monitor = performance_monitoring
batch = batch_processing
memory_optimized = optimize_memory_usage