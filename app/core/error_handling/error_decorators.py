"""
错误处理装饰器，简化异常处理的使用

提供各种装饰器来自动处理异常、重试和恢复
"""

import asyncio
import functools
import inspect
from typing import Any, Callable, Dict, List, Optional, Type, Union, TypeVar
from dataclasses import dataclass
from enum import Enum
import time

from .error_handler import error_handler, ErrorSeverity, ErrorCategory
from ...utils.logger import logger

T = TypeVar('T')


class RetryStrategy(Enum):
    """重试策略"""
    FIXED = "fixed"         # 固定间隔
    EXPONENTIAL = "exponential"  # 指数退避
    LINEAR = "linear"       # 线性增长


@dataclass
class RetryConfig:
    """重试配置"""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    allowed_exceptions: List[Type[Exception]] = None
    stop_on: List[Type[Exception]] = None

    def __post_init__(self):
        if self.allowed_exceptions is None:
            self.allowed_exceptions = [Exception]
        if self.stop_on is None:
            self.stop_on = []


def calculate_delay(attempt: int, config: RetryConfig) -> float:
    """计算重试延迟"""
    if config.strategy == RetryStrategy.FIXED:
        delay = config.base_delay
    elif config.strategy == RetryStrategy.EXPONENTIAL:
        delay = config.base_delay * (2 ** (attempt - 1))
    elif config.strategy == RetryStrategy.LINEAR:
        delay = config.base_delay * attempt
    else:
        delay = config.base_delay

    return min(delay, config.max_delay)


def robust_error_handling(
    severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    category: Optional[ErrorCategory] = None,
    max_recovery_attempts: int = 3,
    include_args: bool = False,
    stop_on_critical: bool = True
):
    """
    健壮的错误处理装饰器

    Args:
        severity: 错误严重程度
        category: 错误类别
        max_recovery_attempts: 最大恢复尝试次数
        include_args: 是否包含函数参数
        stop_on_critical: 遇到严重错误时是否停止重试
    """
    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                attempt = 0
                last_exception = None

                while attempt < max_recovery_attempts:
                    try:
                        return await func(*args, **kwargs)

                    except Exception as e:
                        last_exception = e
                        attempt += 1

                        # 提取上下文信息
                        context = {
                            'function': func.__name__,
                            'attempt': attempt,
                            'max_attempts': max_recovery_attempts
                        }

                        if include_args:
                            # 简化参数，避免敏感信息
                            safe_args = str(args)[:200]  # 限制长度
                            safe_kwargs = {k: str(v)[:100] for k, v in kwargs.items() if not callable(v)}
                            context.update({
                                'args': safe_args,
                                'kwargs': safe_kwargs
                            })

                        # 处理错误
                        error_report = await error_handler.handle_error(
                            exception=e,
                            function_args=context
                        )

                        # 如果错误已恢复，继续
                        if error_report.resolved:
                            logger.info(f"函数 {func.__name__} 错误已恢复，重试成功")
                            continue

                        # 检查是否应该停止重试
                        if stop_on_critical and error_report.severity == ErrorSeverity.CRITICAL:
                            logger.critical(f"函数 {func.__name__} 遇到严重错误，停止重试")
                            break

                        # 如果达到最大重试次数，停止
                        if attempt >= max_recovery_attempts:
                            logger.error(f"函数 {func.__name__} 达到最大重试次数 {max_recovery_attempts}")
                            break

                        # 等待后重试
                        wait_time = min(1.0 * attempt, 5.0)  # 最多等待5秒
                        logger.info(f"函数 {func.__name__} 将在 {wait_time} 秒后重试 (第 {attempt}/{max_recovery_attempts} 次)")
                        await asyncio.sleep(wait_time)

                # 重试失败，抛出最后的异常
                logger.error(f"函数 {func.__name__} 重试失败，最后错误: {last_exception}")
                raise last_exception

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                attempt = 0
                last_exception = None

                # 同步函数的重试逻辑（简化版）
                while attempt < max_recovery_attempts:
                    try:
                        return func(*args, **kwargs)

                    except Exception as e:
                        last_exception = e
                        attempt += 1
                        logger.error(f"函数 {func.__name__} 异常 (第 {attempt} 次): {e}")

                        if attempt >= max_recovery_attempts:
                            break

                        time.sleep(min(0.5 * attempt, 2.0))

                raise last_exception

            return wrapper

    return decorator


def retry_on_failure(
    max_attempts: int = 3,
    delay: float = 1.0,
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
    allowed_exceptions: List[Type[Exception]] = None,
    stop_on: List[Type[Exception]] = None
):
    """
    失败重试装饰器

    Args:
        max_attempts: 最大重试次数
        delay: 基础延迟时间
        strategy: 重试策略
        allowed_exceptions: 允许重试的异常类型
        stop_on: 遇到这些异常时停止重试
    """
    config = RetryConfig(
        max_attempts=max_attempts,
        base_delay=delay,
        strategy=strategy,
        allowed_exceptions=allowed_exceptions or [Exception],
        stop_on=stop_on or []
    )

    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_exception = None

                for attempt in range(1, max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)

                    except Exception as e:
                        last_exception = e

                        # 检查是否应该停止重试
                        for stop_exception in config.stop_on:
                            if isinstance(e, stop_exception):
                                logger.error(f"函数 {func.__name__} 遇到停止异常 {type(e).__name__}，停止重试")
                                raise

                        # 检查是否是允许重试的异常类型
                        if not any(isinstance(e, exc_type) for exc_type in config.allowed_exceptions):
                            logger.error(f"函数 {func.__name__} 遇到不允许重试的异常 {type(e).__name__}")
                            raise

                        if attempt < max_attempts:
                            wait_time = calculate_delay(attempt, config)
                            logger.info(f"函数 {func.__name__} 重试 {attempt}/{max_attempts}，等待 {wait_time:.2f}s: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"函数 {func.__name__} 重试次数达上限: {e}")

                raise last_exception

            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                last_exception = None

                for attempt in range(1, max_attempts + 1):
                    try:
                        return func(*args, **kwargs)

                    except Exception as e:
                        last_exception = e

                        for stop_exception in config.stop_on:
                            if isinstance(e, stop_exception):
                                raise

                        if not any(isinstance(e, exc_type) for exc_type in config.allowed_exceptions):
                            raise

                        if attempt < max_attempts:
                            wait_time = calculate_delay(attempt, config)
                            logger.info(f"函数 {func.__name__} 重试 {attempt}/{max_attempts}，等待 {wait_time:.2f}s: {e}")
                            time.sleep(wait_time)

                raise last_exception

            return wrapper

    return decorator


def timeout_protection(timeout_seconds: float, default_value: Any = None):
    """
    超时保护装饰器

    Args:
        timeout_seconds: 超时时间（秒）
        default_value: 超时时的默认返回值
    """
    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    logger.warning(f"函数 {func.__name__} 执行超时 ({timeout_seconds}s)")
                    if default_value is not None:
                        return default_value
                    raise TimeoutError(f"函数 {func.__name__} 执行超时")
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                import threading
                import queue

                result_queue = queue.Queue()
                exception_queue = queue.Queue()

                def run_func():
                    try:
                        result = func(*args, **kwargs)
                        result_queue.put(result)
                    except Exception as e:
                        exception_queue.put(e)

                thread = threading.Thread(target=run_func)
                thread.start()
                thread.join(timeout_seconds)

                if thread.is_alive():
                    logger.warning(f"函数 {func.__name__} 执行超时 ({timeout_seconds}s)")
                    if default_value is not None:
                        return default_value
                    raise TimeoutError(f"函数 {func.__name__} 执行超时")

                if not exception_queue.empty():
                    raise exception_queue.get()

                return result_queue.get()

            return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

        return decorator


def circuit_breaker(
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    expected_exception: Type[Exception] = Exception
):
    """
    熔断器装饰器，防止级联失败

    Args:
        failure_threshold: 失败阈值
        recovery_timeout: 恢复超时时间
        expected_exception: 预期的异常类型
    """
    class CircuitBreakerState:
        def __init__(self):
            self.failure_count = 0
            self.last_failure_time = None
            self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    breaker_state = CircuitBreakerState()

    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                current_time = time.time()

                # 检查熔断器状态
                if breaker_state.state == "OPEN":
                    if current_time - breaker_state.last_failure_time < recovery_timeout:
                        raise Exception(f"熔断器开启，函数 {func.__name__} 暂时不可用")
                    else:
                        breaker_state.state = "HALF_OPEN"
                        logger.info(f"熔断器进入半开状态: {func.__name__}")

                try:
                    result = await func(*args, **kwargs)

                    # 成功执行，重置失败计数
                    if breaker_state.state == "HALF_OPEN":
                        breaker_state.state = "CLOSED"
                        breaker_state.failure_count = 0
                        logger.info(f"熔断器恢复正常: {func.__name__}")

                    return result

                except expected_exception as e:
                    breaker_state.failure_count += 1
                    breaker_state.last_failure_time = current_time

                    if breaker_state.failure_count >= failure_threshold:
                        breaker_state.state = "OPEN"
                        logger.error(f"熔断器开启: {func.__name__} (失败次数: {breaker_state.failure_count})")

                    raise

                except Exception as e:
                    # 非预期异常，不触发熔断器
                    raise

        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                current_time = time.time()

                if breaker_state.state == "OPEN":
                    if current_time - breaker_state.last_failure_time < recovery_timeout:
                        raise Exception(f"熔断器开启，函数 {func.__name__} 暂时不可用")
                    else:
                        breaker_state.state = "HALF_OPEN"

                try:
                    result = func(*args, **kwargs)

                    if breaker_state.state == "HALF_OPEN":
                        breaker_state.state = "CLOSED"
                        breaker_state.failure_count = 0

                    return result

                except expected_exception as e:
                    breaker_state.failure_count += 1
                    breaker_state.last_failure_time = current_time

                    if breaker_state.failure_count >= failure_threshold:
                        breaker_state.state = "OPEN"

                    raise

                except Exception as e:
                    raise

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


def async_error_boundary(default_return: Any = None):
    """
    异步错误边界装饰器，捕获所有异常并提供默认返回值

    Args:
        default_return: 异常发生时的默认返回值
    """
    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"异步错误边界捕获异常 {func.__name__}: {e}")
                    await error_handler.handle_error(e, {'function': func.__name__})
                    return default_return
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"同步错误边界捕获异常 {func.__name__}: {e}")
                    # 同步函数中异步处理错误可能会复杂化，这里简化处理
                    return default_return

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


def performance_boundary(max_execution_time: float = 30.0):
    """
    性能边界装饰器，监控和限制函数执行时间

    Args:
        max_execution_time: 最大执行时间（秒）
    """
    def decorator(func: T) -> T:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = await asyncio.wait_for(
                        func(*args, **kwargs),
                        timeout=max_execution_time
                    )
                    execution_time = time.time() - start_time
                    logger.debug(f"函数 {func.__name__} 执行时间: {execution_time:.2f}s")
                    return result
                except asyncio.TimeoutError:
                    execution_time = time.time() - start_time
                    logger.warning(f"函数 {func.__name__} 执行超时: {execution_time:.2f}s (限制: {max_execution_time}s)")
                    raise TimeoutError(f"函数 {func.__name__} 执行超时")

        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                logger.debug(f"函数 {func.__name__} 执行时间: {execution_time:.2f}s")
                return result

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


# 便捷的装饰器别名
handle_errors = robust_error_handling
auto_retry = retry_on_failure
with_timeout = timeout_protection
circuit_break = circuit_breaker
error_boundary = async_error_boundary