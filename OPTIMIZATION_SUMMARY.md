# StreamCap 性能优化总结

根据 2025-01-03 的代码审查报告，已实现以下优化：

## 1. URL 级别去重 + TTL 缓存
**文件**: `app/core/recording/stream_manager.py`

- 添加 `StreamCache` 类实现 URL 级别缓存
- 60秒 TTL 缓存，同一 URL 在 TTL 内不重复请求
- 缓存命中时直接返回结果，减少网络请求

## 2. 动态轮询间隔（指数退避）
**文件**: `app/core/recording/stream_manager.py`

- `get_poll_interval()` 方法实现指数退避策略
- 连续离线次数与轮询间隔关系：
  - 0次离线: 60秒
  - 1次离线: 180秒(3分钟)
  - 2次离线: 300秒(5分钟)
  - 3次以上: 600秒(10分钟)上限

## 3. 批次调度 + 随机抖动
**文件**: `app/core/recording/record_manager.py`

- `AdaptiveScheduler` 类实现批次调度
- 每批 5 个录制项，批间随机抖动 0-5 秒
- 避免瞬时集中请求

## 4. 多策略解析 + Fallback
**文件**: `app/core/recording/enhanced_parser.py`

- `MultiStrategyParser` 类支持三种解析策略：
  - 策略A: 从最终跳转 URL 中提取 roomId/unique_id
  - 策略B: 从 HTML/JSON blob 中提取
  - 策略C: 调用备用 API

## 5. 错误响应摘要日志
**文件**: `app/core/recording/enhanced_parser.py`

- `ResponseAnalyzer` 类分析响应并提取诊断信息
- 自动检测风控、验证码、页面结构调整等错误类型
- 失败时记录 HTTP status、最终 URL、content-type、body 预览

## 6. 分层检测
**文件**: `app/core/recording/record_manager.py`

- `check_all_live_status()` 方法支持批量检测
- Active 状态的录制跳过检测，避免重复 fetch

## 7. UA 池 + Header 规范化
**文件**: `app/core/recording/request_headers.py`

- `UserAgentPool` 类包含 15+ 个 User-Agent
- 支持 Chrome、Firefox、移动端 UA
- `HeaderBuilder` 类规范化请求头

## 8. 结构化日志 + Metrics
**文件**: `app/core/recording/stream_manager.py`

- `MetricsCollector` 类收集性能指标
- 记录成功率、失败类型分布等

## 9. 日志脱敏
**文件**: `app/utils/logger.py`

- `sanitize_url()` 函数隐藏敏感参数(expires、sign、token 等)
- `log_stream_url()` 函数用于安全记录流地址
- play_url.log 不再暴露完整敏感信息

## 10. 自适应并发
**文件**: `app/core/recording/record_manager.py`

- 使用 `platform_semaphores` 控制每个平台的并发数
- 默认最大并发: 3

## 新增文件
- `app/core/recording/enhanced_parser.py` - 增强解析模块
- `app/core/recording/request_headers.py` - 请求头规范化模块

## 配置建议
在 `.env` 文件中可以配置：
- `PLATFORM_MAX_CONCURRENT_REQUESTS`: 最大并发请求数(默认3)
- `LOOP_TIME_SECONDS`: 默认轮询间隔(默认300秒)
- `PROXY_ADDRESS`: 代理地址
