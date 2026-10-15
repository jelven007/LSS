# 盘中连续采集监控上线记录

2026-10-15 09:29（Asia/Shanghai）完成采集器及规则评估器验收。
环境：VKE `cdaus2p98m08ce3b12nn0`，命名空间 `banxia`，
生产入口 <https://shanao.asia>。

## 版本与范围

| 项目 | 值 |
| --- | --- |
| 采集器镜像 | `xm1001-cn-shanghai.cr.volces.com/banxia/banxia-strategy:rustdx-20261015-r2` |
| 源码提交 | `7be3ebdc2f6fd045c83cac12d372ec997262d39d` |
| 平台 | `linux/amd64` |
| OCI 索引摘要 | `sha256:4ac50e170784a6e4b8f0ba74c994c4005ee4a2fb8dee8bbfa99b3705e8285379` |
| amd64 Manifest | `sha256:84cad6067da016244675dc16fd6de824e359031c6a4dca367a3cdbf4265b6951` |
| Prometheus | `v2.55.1`，amd64 Manifest `sha256:b1935d181b6dd8e9c827705e89438815337e1b10ae35605126f05f44e5c6940f` |
| 上一采集器镜像 | `rustdx-20261015-r1` |

应用镜像由已提交源码的独立快照构建。本次只更新 StatefulSet `market-collector`
的两个副本，先更新 ordinal 1 验证指标，再更新 ordinal 0。
采集器通过显式 `BANXIA_CODE_COMMIT` 记录新源码；其他应用保留 r1 和原源码标识。
既有 WAL 持久卷和策略监控计划继续使用。

新增 `intraday-monitor` Deployment、Service、两个 ConfigMap 及内部访问策略，
持续运行统一的 11 条 Prometheus 告警规则。
另启用 API、worker、Flink 的 ServiceMonitor/PodMonitor，
并允许 kube-system 的监控 Agent 访问指定指标端口。
全部 Monitor 带当前 allocator 要求的 `volcengine.vmp: "true"`。

未提交的增量回填修改没有进入发布源码；没有重跑迁移、回填或改写候选计划。
运行方式及处置说明见 [Runbook](../31-intraday-monitoring.md)。

## 实际验收

| 检查 | 结果 |
| --- | --- |
| 精确发布源码测试 | 370 项：350 通过、20 项按环境跳过 |
| 新指标测试 | 10 项通过：异常、空报价、部分无效时间、WAL、主备、日历、无抓取 I/O |
| promtool | 11 条规则的触发、恢复及休市/空名单门控验证通过 |
| 两个采集器 | 新镜像及源码匹配；x86_64；Ready；重启 0 |
| 抓取目标 | 两个目标均 `up`，`lastError=""` |
| 运行中规则 | 11/11 加载，`health=ok`，验收时均 inactive |
| 心跳 | 两实例查询年龄约 3.63 秒、8.78 秒，小于 90 秒阈值 |
| 交易日历 | 验证成功；09:29 尚未进入连续交易窗口 |
| 候选数及 WAL | 两实例均为 0 |
| 应用常驻副本 | 原有 13 个应用副本加评估器，共 14/14 Ready，重启 0 |
| 公网就绪接口 | HTTP 200；PostgreSQL、Redis、Kafka、ClickHouse 均 `ok` |
| VMP 发现 | allocator 将两个采集器分配给 `prometheus-agent-0`；发现全部业务 Monitor |
| 告警状态抓取 | allocator 已发现评估器 `/metrics` 及 `/federate` 两个抓取任务 |

规则测试使用受控样本，没有停止真实采集器或注入生产故障，也没有发送测试通知。
验收时监控计划候选为空，所以尚无当日持续采集成功时间；
这不应被报告为已经验证了真实候选下的盘中采集及主备切换。
连续交易开始后，空名单持续 5 分钟会产生 info 告警；
其他交易时效类告警要求存在候选，避免空闲状态误报。

## 待完成项

云控制台实际检查显示没有通知策略、联系人或既有告警规则。
本次启用的是**集群内规则评估**，尚未创建 VMP 原生通知转发规则。
用户需指定飞书 webhook、接收邮箱或其他目标，再绑定对应通知策略并验收投递。

VMP 工作区为上海 `embodied-data`，
ID `691f39a8-48d5-48ae-b961-f78bb6009498`。
已验证监控资源选择、目标分配和本地指标抓取；VMP 远端指标查询未完成验收。
插件提供的控制面凭据为空，不能用于原生告警 API；没有更改认证或尝试绕过权限。
云端回查及通知验收完成前，不宣称消息投递链路已经完整接通。

评估器为单副本，本地时序为临时存储；重启会重新计算 pending 时长。
长期时序依赖 VMP，评估器自身失效的独立外部告警仍需随云端通知配置补齐。

## 回滚

如新采集器异常，将两个副本恢复为 r1 的固定摘要
`sha256:9c167efeadef501f69ec6d520042de6a6972ebb8f460e818517a8dddde67da4b`，
并恢复 `BANXIA_CODE_COMMIT=3414ecbd6a2f15c7dc9f5657eb047b620f69d890`。
不删除 WAL 或改写数据库。

r1 不导出新增业务指标，回滚采集器时应同时暂停 `intraday-monitor`，
防止因缺失新指标产生采集器不可用告警。
必要时删除本次新增的评估器 Service/ConfigMap/ServiceMonitor；
保留 API、worker 和 Flink 的指标抓取不影响业务执行。

构建、规则测试及线上查询原始证据保留在操作机 `/tmp/agent-intraday-*`；
该临时目录不是长期归档。
