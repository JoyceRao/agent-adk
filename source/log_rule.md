# 日志标准

## 日志json结构与字段含义。

1、日志示例：
{"c":"-:1|I|-|2|[CSPContainerMessageDispatcher,-[CSPContainerMessageDispatcher registContainer:block:],CSPContainerMessageDispatcher.m:64]|[CONTAINER]regist:Native","f":1,"l":1775530235234,"n":"","i":1,"m":true}

2、基础日志字段：
| Key | 含义 | 类型 | 备注 |
| :--- | :--- | :--- | :--- |
| **l** | logTime | int | 时间戳 |
| **f** | logType | int | 日志类型：<br>0: UNKNOWN<br>1: Native<br>2: RN<br>3: H5<br>在 logan_fe 中展示 |
| **n** | threadName | string | 日志产生的线程名称 |
| **i** | threadId | string | 线程 ID，兼容 Java |
| **m** | 是否主线程 | int | 标识是否为主线程 |
| **c** | content | string | 日志内容，记录的实际日志信息 |

3、业务字段：
| Key | 含义 | 类型 | 备注 |
| :--- | :--- | :--- | :--- |
| **level** | 日志等级 | string | 【D】Debug: 仅调试模式上报<br>【I】Info: 系统关键信息<br>【W】Warn: 可预知异常（接口/参数错误）<br>【E】Error: 不可预知异常（崩溃/报警） |
| **userid** | 用户 ID | string | 产生记录的用户 ID，为空则用 `-` |
| **category** | 日志类别 | int | 0:通用 1:业务 2:RPC 3:网络 4:性能 5:技术 6:异常 7:错误 (支持扩展) |
| **event** | 二级日志类型 | string | 细分场景（如网络日志下的 req 或 resp），为空则用 `-` |
| **bizmodule** | 业务模块 | string | 业务逻辑所属模块，为空则用 `-` |
| **function** | 调用方法 | string | 业务执行方法，为空则用 `-` |
| **location** | 代码定位 | string | 格式为 `filename:line`，为空则用 `-` |
| **message** | 业务日志内容 | string | 实际的业务日志详细信息 |

