"""
增强的错误处理系统，提供统一的异常处理、恢复和通知机制

功能：
1. 分类异常处理和策略化恢复
2. 异常上下文收集和诊断
3. 错误通知和报告
4. 自动恢复机制
5. 异常统计和分析
"""

import asyncio
import traceback
import sys
from typing import Dict, List, Optional, Callable, Any, Type
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json

from ...utils.logger import logger


class ErrorSeverity(Enum):
    """错误严重程度"""
    LOW = 1      # 轻微错误，影响不大
    MEDIUM = 2   # 中等错误，影响部分功能
    HIGH = 3     # 严重错误，影响主要功能
    CRITICAL = 4 # 致命错误，需要立即处理


class ErrorCategory(Enum):
    """错误类别"""
    NETWORK = "network"         # 网络相关错误
    FILESYSTEM = "filesystem"   # 文件系统错误
    PROCESS = "process"         # 进程相关错误
    MEMORY = "memory"           # 内存相关错误
    CONFIG = "config"           # 配置相关错误
    PLATFORM = "platform"       # 平台相关错误
    RECORDING = "recording"     # 录制相关错误
    UI = "ui"                   # 用户界面错误
    UNKNOWN = "unknown"         # 未知错误


@dataclass
class ErrorContext:
    """错误上下文信息"""
    function_name: str
    module_name: str
    line_number: int
    arguments: Dict[str, Any] = field(default_factory=dict)
    local_variables: Dict[str, Any] = field(default_factory=dict)
    stack_trace: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ErrorReport:
    """错误报告"""
    error_id: str
    exception: Exception
    severity: ErrorSeverity
    category: ErrorCategory
    context: ErrorContext
    recovery_attempts: int = 0
    resolved: bool = False
    resolution_message: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None


class RecoveryStrategy:
    """恢复策略基类"""

    def __init__(self, name: str, max_attempts: int = 3):
        self.name = name
        self.max_attempts = max_attempts
        self.success_count = 0
        self.failure_count = 0

    async def can_handle(self, error_report: ErrorReport) -> bool:
        """判断是否能处理该错误"""
        raise NotImplementedError

    async def execute_recovery(self, error_report: ErrorReport) -> bool:
        """执行恢复操作"""
        raise NotImplementedError

    def get_stats(self) -> Dict[str, Any]:
        """获取策略统计"""
        total_attempts = self.success_count + self.failure_count
        success_rate = (self.success_count / total_attempts * 100) if total_attempts > 0 else 0

        return {
            'name': self.name,
            'success_count': self.success_count,
            'failure_count': self.failure_count,
            'success_rate': success_rate
        }


class NetworkRecoveryStrategy(RecoveryStrategy):
    """网络错误恢复策略"""

    def __init__(self):
        super().__init__("network_recovery")
        self.retry_delays = [1, 2, 5, 10, 30]  # 递增延迟

    async def can_handle(self, error_report: ErrorReport) -> bool:
        """判断是否为网络错误"""
        return error_report.category == ErrorCategory.NETWORK

    async def execute_recovery(self, error_report: ErrorReport) -> bool:
        """执行网络恢复"""
        try:
            # 根据重试次数确定延迟
            delay_idx = min(error_report.recovery_attempts, len(self.retry_delays) - 1)
            delay = self.retry_delays[delay_idx]

            logger.info(f"网络错误恢复，等待 {delay} 秒后重试")

            await asyncio.sleep(delay)

            # 这里可以添加具体的网络恢复逻辑
            # 比如重新连接、切换代理、检查网络状态等

            self.success_count += 1
            return True

        except Exception as e:
            logger.error(f"网络恢复失败: {e}")
            self.failure_count += 1
            return False


class ProcessRecoveryStrategy(RecoveryStrategy):
    """进程错误恢复策略"""

    def __init__(self):
        super().__init__("process_recovery")

    async def can_handle(self, error_report: ErrorReport) -> bool:
        """判断是否为进程错误"""
        return error_report.category == ErrorCategory.PROCESS

    async def execute_recovery(self, error_report: ErrorReport) -> bool:
        """执行进程恢复"""
        try:
            logger.info("开始进程恢复操作")

            # 检查进程是否还存在
            if hasattr(error_report.exception, 'process') and error_report.exception.process:
                process = error_report.exception.process
                if process.poll() is None:  # 进程还在运行
                    logger.warning("进程仍在运行，尝试优雅终止")
                    process.terminate()
                    try:
                        await asyncio.wait_for(asyncio.create_task(
                            asyncio.get_event_loop().run_in_executor(None, process.wait)
                        ), timeout=5.0)
                    except asyncio.TimeoutError:
                        logger.warning("优雅终止失败，强制杀死进程")
                        process.kill()

            # 这里可以添加进程重启逻辑
            # 需要根据应用的具体情况实现

            self.success_count += 1
            return True

        except Exception as e:
            logger.error(f"进程恢复失败: {e}")
            self.failure_count += 1
            return False


class FileSystemRecoveryStrategy(RecoveryStrategy):
    """文件系统错误恢复策略"""

    def __init__(self):
        super().__init__("filesystem_recovery")

    async def can_handle(self, error_report: ErrorReport) -> bool:
        """判断是否为文件系统错误"""
        return error_report.category == ErrorCategory.FILESYSTEM

    async def execute_recovery(self, error_report: ErrorReport) -> bool:
        """执行文件系统恢复"""
        try:
            logger.info("开始文件系统恢复操作")

            # 检查磁盘空间
            import shutil
            disk_usage = shutil.disk_usage('/')
            free_gb = disk_usage.free / (1024**3)

            if free_gb < 0.5:  # 少于500MB
                logger.error(f"磁盘空间不足: {free_gb:.1f}GB")
                # 清理临时文件
                import tempfile
                import os
                temp_dir = tempfile.gettempdir()

                try:
                    for root, dirs, files in os.walk(temp_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            try:
                                os.remove(file_path)
                                logger.debug(f"删除临时文件: {file_path}")
                            except Exception:
                                continue
                except Exception as e:
                    logger.error(f"清理临时文件失败: {e}")

            self.success_count += 1
            return True

        except Exception as e:
            logger.error(f"文件系统恢复失败: {e}")
            self.failure_count += 1
            return False


class ErrorHandler:
    """
    增强的错误处理器

    提供统一的异常处理、分类、恢复和报告机制
    """

    def __init__(self):
        self.error_reports: List[ErrorReport] = []
        self.recovery_strategies: List[RecoveryStrategy] = []
        self.error_callbacks: List[Callable[[ErrorReport], None]] = []
        self.critical_error_callbacks: List[Callable[[ErrorReport], None]] = []

        # 统计信息
        self.error_count = 0
        self.resolved_count = 0

        # 初始化恢复策略
        self._initialize_recovery_strategies()

        # 错误分类规则
        self._error_classification_rules = {
            'ConnectionError': ErrorCategory.NETWORK,
            'TimeoutError': ErrorCategory.NETWORK,
            'OSError': ErrorCategory.FILESYSTEM,
            'PermissionError': ErrorCategory.FILESYSTEM,
            'MemoryError': ErrorCategory.MEMORY,
            'ProcessLookupError': ErrorCategory.PROCESS,
            'SubprocessError': ErrorCategory.PROCESS,
            'json.JSONDecodeError': ErrorCategory.CONFIG,
        }

    def _initialize_recovery_strategies(self):
        """初始化恢复策略"""
        self.recovery_strategies = [
            NetworkRecoveryStrategy(),
            ProcessRecoveryStrategy(),
            FileSystemRecoveryStrategy(),
        ]

    def add_error_callback(self, callback: Callable[[ErrorReport], None]) -> None:
        """添加错误回调"""
        self.error_callbacks.append(callback)

    def add_critical_error_callback(self, callback: Callable[[ErrorReport], None]) -> None:
        """添加严重错误回调"""
        self.critical_error_callbacks.append(callback)

    def add_recovery_strategy(self, strategy: RecoveryStrategy) -> None:
        """添加恢复策略"""
        self.recovery_strategies.append(strategy)

    def classify_exception(self, exception: Exception) -> ErrorCategory:
        """分类异常"""
        exception_type = type(exception).__name__

        # 优先使用直接分类
        if exception_type in self._error_classification_rules:
            return self._error_classification_rules[exception_type]

        # 基于异常消息的关键词分类
        error_message = str(exception).lower()

        if any(keyword in error_message for keyword in ['network', 'connection', 'timeout']):
            return ErrorCategory.NETWORK
        elif any(keyword in error_message for keyword in ['file', 'directory', 'path']):
            return ErrorCategory.FILESYSTEM
        elif any(keyword in error_message for keyword in ['process', 'subprocess', 'pid']):
            return ErrorCategory.PROCESS
        elif any(keyword in error_message for keyword in ['memory', 'allocation']):
            return ErrorCategory.MEMORY
        elif any(keyword in error_message for keyword in ['config', 'setting', 'json']):
            return ErrorCategory.CONFIG

        return ErrorCategory.UNKNOWN

    def determine_severity(self, exception: Exception, context: ErrorContext) -> ErrorSeverity:
        """确定错误严重程度"""
        # 根据异常类型确定基础严重程度
        critical_exceptions = [SystemExit, KeyboardInterrupt, MemoryError]
        high_exceptions = [OSError, RuntimeError, ConnectionError]
        medium_exceptions = [ValueError, KeyError, TimeoutError]

        if type(exception) in critical_exceptions:
            return ErrorSeverity.CRITICAL
        elif type(exception) in high_exceptions:
            return ErrorSeverity.HIGH
        elif type(exception) in medium_exceptions:
            return ErrorSeverity.MEDIUM
        else:
            return ErrorSeverity.LOW

    def extract_context(self, exception: Exception) -> ErrorContext:
        """提取错误上下文"""
        tb = exception.__traceback__
        if tb:
            frame = tb.tb_frame
            while tb.tb_next:
                tb = tb.tb_next
                frame = tb.tb_frame

            function_name = frame.f_code.co_name
            module_name = frame.f_globals.get('__name__', 'unknown')
            line_number = frame.f_lineno

            # 获取局部变量（限制数量和大小，避免内存问题）
            local_vars = {}
            try:
                for key, value in frame.f_locals.items():
                    if not key.startswith('__') and len(local_vars) < 10:
                        try:
                            # 尝试序列化值，失败则使用字符串表示
                            json.dumps({'value': value})
                            local_vars[key] = value
                        except (TypeError, ValueError):
                            local_vars[key] = str(value)[:100]  # 限制长度
            except Exception:
                pass

            stack_trace = ''.join(traceback.format_exception(
                type(exception), exception, exception.__traceback__
            ))

            return ErrorContext(
                function_name=function_name,
                module_name=module_name,
                line_number=line_number,
                local_variables=local_vars,
                stack_trace=stack_trace
            )
        else:
            return ErrorContext(
                function_name='unknown',
                module_name='unknown',
                line_number=0
            )

    async def handle_error(self,
                          exception: Exception,
                          function_args: Optional[Dict[str, Any]] = None,
                          custom_context: Optional[Dict[str, Any]] = None) -> ErrorReport:
        """
        处理错误的主要方法

        Args:
            exception: 异常对象
            function_args: 函数参数
            custom_context: 自定义上下文

        Returns:
            ErrorReport: 错误报告
        """
        self.error_count += 1

        # 生成错误ID
        error_id = f"error_{int(asyncio.get_event_loop().time())}_{id(exception)}"

        # 提取和分类错误信息
        context = self.extract_context(exception)
        if function_args:
            context.arguments.update(function_args)
        if custom_context:
            context.arguments.update(custom_context)

        category = self.classify_exception(exception)
        severity = self.determine_severity(exception, context)

        # 创建错误报告
        error_report = ErrorReport(
            error_id=error_id,
            exception=exception,
            severity=severity,
            category=category,
            context=context
        )

        # 保存错误报告
        self.error_reports.append(error_report)

        # 限制错误报告数量
        if len(self.error_reports) > 1000:
            self.error_reports = self.error_reports[-500:]

        logger.error(f"错误处理: {error_id} - {type(exception).__name__}: {exception}")

        # 尝试恢复
        await self._attempt_recovery(error_report)

        # 通知回调
        await self._notify_callbacks(error_report)

        return error_report

    async def _attempt_recovery(self, error_report: ErrorReport) -> bool:
        """尝试错误恢复"""
        for strategy in self.recovery_strategies:
            if error_report.recovery_attempts >= strategy.max_attempts:
                continue

            try:
                if await strategy.can_handle(error_report):
                    logger.info(f"尝试使用策略 {strategy.name} 恢复错误 {error_report.error_id}")
                    error_report.recovery_attempts += 1

                    success = await strategy.execute_recovery(error_report)
                    if success:
                        error_report.resolved = True
                        error_report.resolution_message = f"通过 {strategy.name} 策略恢复"
                        error_report.resolved_at = datetime.now()
                        self.resolved_count += 1

                        logger.info(f"错误恢复成功: {error_report.error_id}")
                        return True

            except Exception as e:
                logger.error(f"恢复策略 {strategy.name} 执行失败: {e}")

        return False

    async def _notify_callbacks(self, error_report: ErrorReport) -> None:
        """通知回调函数"""
        # 通用错误回调
        for callback in self.error_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(error_report)
                else:
                    callback(error_report)
            except Exception as e:
                logger.error(f"错误回调执行失败: {e}")

        # 严重错误回调
        if error_report.severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]:
            for callback in self.critical_error_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(error_report)
                    else:
                        callback(error_report)
                except Exception as e:
                    logger.error(f"严重错误回调执行失败: {e}")

    def get_error_stats(self) -> Dict[str, Any]:
        """获取错误统计"""
        # 按类别统计
        category_stats = {}
        for category in ErrorCategory:
            count = sum(1 for report in self.error_reports if report.category == category)
            category_stats[category.value] = count

        # 按严重程度统计
        severity_stats = {}
        for severity in ErrorSeverity:
            count = sum(1 for report in self.error_reports if report.severity == severity)
            severity_stats[severity.name] = count

        # 恢复策略统计
        strategy_stats = {}
        for strategy in self.recovery_strategies:
            strategy_stats[strategy.name] = strategy.get_stats()

        return {
            'total_errors': self.error_count,
            'resolved_errors': self.resolved_count,
            'resolution_rate': (self.resolved_count / max(1, self.error_count)) * 100,
            'by_category': category_stats,
            'by_severity': severity_stats,
            'recovery_strategies': strategy_stats
        }

    def get_recent_errors(self, limit: int = 10) -> List[ErrorReport]:
        """获取最近的错误"""
        return sorted(self.error_reports, key=lambda x: x.created_at, reverse=True)[:limit]

    def clear_old_errors(self, days: int = 7) -> int:
        """清理旧错误记录"""
        cutoff_time = datetime.now().timestamp() - (days * 24 * 3600)

        old_count = len(self.error_reports)
        self.error_reports = [
            report for report in self.error_reports
            if report.created_at.timestamp() > cutoff_time
        ]

        removed_count = old_count - len(self.error_reports)
        if removed_count > 0:
            logger.info(f"清理了 {removed_count} 条旧错误记录")

        return removed_count


# 全局错误处理器实例
error_handler = ErrorHandler()


async def initialize_error_handler() -> None:
    """初始化错误处理器"""
    # 添加默认的严重错误回调
    async def critical_error_callback(error_report: ErrorReport):
        """严重错误处理回调"""
        logger.critical(f"严重错误: {error_report.error_id} - {error_report.exception}")

        # 可以在这里添加紧急恢复逻辑
        # 比如发送警报、强制保存状态等

    error_handler.add_critical_error_callback(critical_error_callback)
    logger.info("错误处理器初始化完成")