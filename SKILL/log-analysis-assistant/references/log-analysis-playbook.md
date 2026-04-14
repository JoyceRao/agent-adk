# 日志分析作战手册（重构版）

## 1. 适用范围

| 项 | 说明 |
|---|---|
| 输入日志规范 | 以 `source/log_rule.md` 为准，日志行为 JSON 行 |
| 本次对齐样本 | `source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log` |
| 样本规模 | 1717 行 |
| 时间范围 | `l=1775530235242` 到 `l=1775544586697` |

## 2. 数据结构与解析优先级

| 层级 | 识别方式 | 关键字段 | 解析说明 |
|---|---|---|---|
| 外层 JSON | 每行为 `{"c":...,"f":...,"l":...}` | `c/f/l/n/i/m` | 基础结构，先解析外层再解析 `c` |
| `c` 管道格式 | `c` 以 `-:` 或 `线程名:线程ID` 开头，含 `|` 分段 | `level/category/location/message` | 典型如 `-:1|I|-|2|[...m:64]|...` |
| `c` 字符串化 JSON | `c` 以 `{\"level\":...}` 开头 | `level/userId/msg` | 需先反转义再提取业务字段 |

## 3. 字段对齐规则（按 `log_rule.md` 修订）

| 规则 | 标准字段 | 样本兼容字段 | 处理方式 |
|---|---|---|---|
| 日志时间 | `l` | `l` | 直接使用毫秒时间戳 |
| 日志类型 | `f` | `f=1/99` 为主 | `f=1` 多为 Native/RN 调试轨迹，`f=99` 多为业务网络摘要 |
| 线程信息 | `n/i/m` | `n/i/m` | 用于判断主线程与并发行为 |
| 业务级别 | `level` | `level` | 在 `c` 为字符串化 JSON 时直接提取 |
| 用户字段 | `userid` | `userId` | 统一映射为 `user_id`（注意大小写差异） |
| 业务消息 | `message` | `msg` | 统一映射为 `message` |

## 4. 样本画像（可直接用于告警与巡检）

| 指标 | 数值 | 说明 |
|---|---|---|
| `f=1` 行数 | 1578 | 技术轨迹日志为主 |
| `f=99` 行数 | 139 | 业务网络摘要日志 |
| `[RN_NET]OldSign req` | 86 | 发起请求轨迹 |
| `[RN_NET]Resp` | 52 | 收到 HTTP 响应轨迹 |
| `[RN_NET]Finish req` | 52 | 请求完成与业务响应轨迹 |
| `Task orphaned` | 114 | RN 图片请求被 orphan，属于高频性能噪音 |
| `kCFErrorDomainCFNetwork错误310` | 8 | 网络层失败，出现在 WARN 业务日志中 |
| `reactnative_exception` | 3 | 同一接口出现 request_catch 异常上报 |
| `applicationWillTerminate` | 2 | 存在应用终止事件 |

## 5. 重点异常模式（本样本）

| 模式 | 证据关键词 | 样本信号 | 研判建议 |
|---|---|---|---|
| RN 网络链路不闭合 | `OldSign req` 多于 `Resp/Finish` | 86 vs 52/52 | 结合应用切后台/终止事件判断是否为中断导致 |
| RN 图片加载异常 | `Task orphaned for request` | 114 次 | 优先归类为性能/资源取消类，不直接判定业务故障 |
| 弱网/系统网络错误 | `kCFErrorDomainCFNetwork错误310` | 8 次，均为 WARN | 与 `net=data/wifi`、页面切换、前后台切换联合分析 |
| 接口请求异常上报 | `reactnative_exception` + `code="-100"` | 3 次，URI 均为 `/apicenter/AbTest/batchQuery` | 判定为接口级异常，需联查服务端与网关 |

## 6. 快速检索命令（固定模板）

| 目的 | 命令 |
|---|---|
| 统计日志类型分布 | `rg -o '"f":[0-9]+' <log> \| sort \| uniq -c` |
| 提取 RN 网络三段日志 | `rg -n -F '[RN_NET]OldSign req' <log>` |
|  | `rg -n -F '[RN_NET]Resp' <log>` |
|  | `rg -n -F '[RN_NET]Finish req' <log>` |
| 提取弱网错误 | `rg -n -F 'kCFErrorDomainCFNetwork错误310' <log>` |
| 提取 RN 异常上报 | `rg -n -F 'reactnative_exception' <log>` |
| 提取应用生命周期 | `rg -n -E 'applicationWillTerminate|isBackground = 1|isBackground = 0' <log>` |

## 7. 归因决策表（执行顺序）

| 步骤 | 判断条件 | 结论方向 | 下一步 |
|---|---|---|---|
| 1 | 是否存在网络错误关键词（310/timeout/reset） | 网络链路问题优先 | 关联同时间段请求成功率 |
| 2 | 是否存在 `reactnative_exception` 且 URI 集中 | 接口级异常优先 | 拉取对应接口服务端日志 |
| 3 | `OldSign req` 与 `Resp/Finish` 是否显著不平衡 | 客户端中断/切后台可能 | 叠加生命周期日志验证 |
| 4 | `Task orphaned` 是否高频但业务请求成功 | 性能噪音优先 | 单独记入性能看板，不混入主故障根因 |

## 8. 输出模板（CRISP-L 强制）

| 模块 | 要求 |
|---|---|
| 0. 快速摘要 | 先给“结论 + 修复优先动作”，一屏可读 |
| C Conclusion | 问题ID/结论/严重级别/影响/置信度/关键证据 |
| R Reproduction | 触发条件 + 复现建议 + 关键证据 |
| I Indicators | 指标值 + 公式 + 样本量 + 统计说明（区间或显著性判断） |
| S Source Correlation | 日志行号与源码文件:行号映射，附节选片段 |
| P Plan | 修复动作 + 优先级 + Owner 建议 + 验收标准 |
| L Loop Closure | T+1h/T+24h/T+72h 观察指标、告警阈值、回滚规则 |
| 其他 | 数据局限性、证据预览、脱敏声明 |

## 9. 安全与脱敏

| 类型 | 规则 |
|---|---|
| Token/Cookie/JWT | 只保留前 6 后 4，中间打码 |
| 设备标识（`deviceId/idfa`） | 对外输出时脱敏 |
| 用户标识（`userId`） | 仅在必要场景输出，默认部分脱敏 |
