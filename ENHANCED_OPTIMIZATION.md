# StreamCap 增强优化总结

## 基于反馈的增强优化

### 1. 缓存分层系统 (TieredCache)
**文件**: `app/core/recording/stream_manager.py`

解决 TTL 缓存和在线状态变化之间的冲突：

| 层级 | 缓存内容 | TTL | 用途 |
|------|---------|-----|------|
| L1 | room_id/unique_id | 10分钟 | 解析结果复用 |
| L2 | is_live 状态 | 30秒 | 快速检测 |
| L3 | play_url/flv_url | 15秒或根据expires | 录制流地址 |

**关键改进**：
- 每5次离线强制刷新一次（轻量探测）
- play_url 根据 expires 自动计算 TTL
- 离线→在线切换不会被延迟 60 秒

### 2. Sticky Session 管理器 (StickySessionManager)
**文件**: `app/core/recording/stream_manager.py`

解决 UA 池一致性问题：

- 同一个 URL 固定使用同一个 UA（1小时）
- 成功 3 次以上 UA 保持不变
- 连续失败 3 次自动切换 UA
- Cookie 同样 sticky

### 3. 风控自愈闭环 (RiskControlManager)
**文件**: `app/core/recording/stream_manager.py`

风控检测后的自动化处理：

```python
# 检测到风控时
risk_control.on_risk_detected(url, risk_type='captcha')

# 自动进入 cooldown
# 降速处理
# 限制 fallback 调用次数（每主播每小时5次）
```

### 4. 增强的 Response 摘要日志
**文件**: `app/core/recording/enhanced_parser.py`

改进日志安全性：

```python
# 敏感信息脱敏
preview = ResponseAnalyzer._mask_sensitive(preview)

# 内容截断（最多300字符）
result['content_preview'] = content[:300]
```

**脱敏内容**：
- ticket=***
- challenge=***
- s_v_web_id=***
- session_id=***
- token=***

### 5. 动态轮询间隔增强
**文件**: `app/core/recording/stream_manager.py`

支持优先级和时间段策略：

```python
def get_poll_interval(self, url: str, base_interval: int = 60,
                      is_priority: bool = False) -> int:
    # 优先级主播：60-300秒
    # 非优先级主播：
    #   - 黄金时段(19-23点): 60-300秒
    #   - 其他时段: 120-600秒
```

### 6. 结构化 Metrics 输出
**文件**: `app/core/recording/stream_manager.py`

MetricsCollector 提供 JSON 格式报告：

```json
{
  "total_fetches": 1000,
  "success_rate": "95.2%",
  "cache_hit_rate": "45.0%",
  "parse_failures": 15,
  "http_errors": 8,
  "captcha_errors": 5,
  "risk_control_errors": 3,
  "avg_latency_ms": "125.3",
  "strategy_stats": {
    "url_parse": {"success": 500, "fail": 10},
    "html_parse": {"success": 300, "fail": 5}
  }
}
```

### 7. Fallback API 成本控制
**文件**: `app/core/recording/stream_manager.py`

```python
# 每主播每小时最多5次 fallback
FALLBACK_LIMIT = 5

# 超过限制后进入 15分钟 cooldown
risk_control.on_risk_detected(url)
# -> cooldown 900秒
```

## 新增模块

| 模块 | 文件 | 功能 |
|-----|------|------|
| TieredCache | stream_manager.py | 分层缓存系统 |
| StickySessionManager | stream_manager.py | Sticky Session |
| RiskControlManager | stream_manager.py | 风控自愈闭环 |
| MetricsCollector | stream_manager.py | 性能指标收集 |
| StickyUserAgentPool | request_headers.py | Sticky UA 池 |
| ResponseAnalyzer | enhanced_parser.py | 响应分析+脱敏 |

## 改进点总结

| 问题 | 解决方案 |
|------|---------|
| 缓存延迟开播 | 分层缓存 + 每5次强制刷新 |
| 随机 UA 行为异常 | Sticky UA（同一 URL 固定 UA） |
| 风控扩散 | 风险域名降速 + cooldown |
| 敏感信息泄露 | 脱敏 + 截断 |
| Fallback 滥用 | 次数限制 + 熔断 |
| 热点主播延迟 | 优先级 + 时间段策略 |

## 配置建议

在 `.env` 文件中可以配置：

```bash
# 缓存 TTL 配置
CACHE_L1_TTL=600      # 10分钟
CACHE_L2_TTL=30       # 30秒
CACHE_L3_TTL=15       # 15秒

# 风控配置
RISK_COOLDOWN=900     # 15分钟
FALLBACK_LIMIT=5      # 每小时5次

# 轮询配置
PRIME_TIME_START=19   # 黄金时段开始
PRIME_TIME_END=23     # 黄金时段结束
```
