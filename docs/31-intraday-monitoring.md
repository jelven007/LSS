# 盘中连续采集监控

## 运行方式

两个 `market-collector` 副本在 9100 端口提供 `banxia_collector_*` 指标。
`intraday-monitor` 使用 Prometheus 每 10 秒采集、每 15 秒评估规则。
VKE 的 O11y Agent 通过 PodMonitor 采集业务指标，并通过 ServiceMonitor 的
`/federate` 接口采集 `ALERTS`、`ALERTS_FOR_STATE`，送入已有 VMP 工作区。
PrometheusRule CRD 不是这个部署的前提。

规则由 `deploy/observability/collector-alerts.yaml` 统一维护，通过 ConfigMap
挂载到评估器。修改后应用配置，并重启 `intraday-monitor`。评估器保存最近 6 小时、
最多 100 MB 的本地指标；长期指标使用 VMP。

**规则评估与通知投递是两个独立状态。** 评估器可直接查询 pending/firing 状态。
接收联系人、飞书 webhook、邮件或其他通知策略需要配置后才会向外投递。
初次部署没有预设收件人，不以 Git 邮箱推断通知接收人。

## 告警规则

| 规则 | 条件与持续时间 | 级别 |
| --- | --- | --- |
| CollectorUnavailable | 所有采集器不可用或指标全部消失，1 分钟 | critical |
| HeartbeatStale | 心跳超过 max(90 秒, 当前循环间隔×3)，1 分钟 | critical |
| NoLeader | 连续交易、有候选且没有主节点，1 分钟 | critical |
| MultipleLeaders | 同一分片有多个主节点，1 分钟 | critical |
| Stalled | 连续交易、有候选的主节点 30 秒未发布有效行情，1 分钟 | critical |
| QuoteStale | 连续交易主节点行情源时间 P95 延迟大于 10 秒，2 分钟 | warning |
| QuoteCoverageLow | 连续交易主节点候选有效行情覆盖率低于 100%，2 分钟 | warning |
| PublishFailures | 连续交易期间最近 2 分钟发生 Kafka 发布失败，持续 1 分钟 | critical |
| WalPending | WAL 有未确认事件，2 分钟 | warning |
| CalendarUnavailable | 工作日连续交易时钟窗口无法验证当天交易日历，1 分钟 | critical |
| NoCandidates | 已验证的连续交易时段候选数为 0，5 分钟 | info |

完整规则名称均带 `BanxiaCollector` 前缀。异常指标至少包含 `environment`、
`shard`；逐实例异常保留 `pod`、`instance` 标签。错误文本在采集器日志中，
不作为 Prometheus 标签，防止基数失控。`errors_total` 的 stage 固定为
calendar/candidates/lease/source/normalize/publish/status/config。

## 交易时段与数据含义

连续交易窗口为上海时间 09:30–11:30、13:00–15:00，并要求当天交易日历验证成功。
午休、周末、节假日不会触发行情时效、采集停滞或缺主节点告警。
交易日历无法读取与正常休市分别记录；无法确认日期时，停止采集并触发日历告警。
开盘或午休后告警持续时间提供恢复窗口。

空名单单独产生 info 告警，不等同于采集停滞，也不会强制创建候选。
只有有效价格、合法且不超过本地时钟 5 秒的源时间、实际报价事件及发布成功，
才能更新时间戳；部分候选缺行情由覆盖率告警反映。
P95 使用最后一次有效批次的源时间，指标抓取时计算其当前年龄，
因此源请求停住后年龄继续增长。HTTP 指标抓取不访问市场源、数据库或 WAL。
WAL 与发布计数在采集循环结束后缓存；交易日历暂停时保留最后一次缓存。

源端 `servertime` 只有时分秒，日期由当天本地上海时间赋予。
监控能发现延迟、缺失和明显未来时间，不能仅凭这个字段识别被源端重放的同时间旧日行情。
上游分钟线及策略的交易日有效性判断继续保留。

## 检查与处置

```bash
kubectl -n banxia get pods -l app=banxia-market-collector
kubectl -n banxia logs market-collector-0 --tail=100
kubectl -n banxia get podmonitor,servicemonitor
kubectl -n banxia port-forward service/intraday-monitor 19090:9090
```

访问 `http://localhost:19090/alerts` 或 `/api/v1/rules?type=alert` 查看运行中规则，
访问 `/api/v1/targets` 确认两个采集器抓取健康。
评估器端口只用于集群内部及本机 port-forward，没有公网入口。
VMP 工作区：上海 `embodied-data`，ID `691f39a8-48d5-48ae-b961-f78bb6009498`。
查询 `banxia_collector_running{environment="production"}` 应出现两个实例；
查询 `ALERTS{alertname=~"BanxiaCollector.*",alertstate="firing"}` 查看已触发告警。

先查看具体告警的实例和最近错误，再检查依赖：

- 无主节点：检查 PostgreSQL 连通、采集器循环与候选计划。
- 停滞、低覆盖率或行情延迟：检查 rustdx 节点连通、源时间和行情样本。
- 发布失败或 WAL 积压：检查 Kafka broker、确认超时和 WAL 磁盘空间；
  恢复后等待重放完成，不删除未确认的 WAL。
- 日历不可用：检查 PostgreSQL 的交易日历数据及同步任务。
- 空名单：检查当前策略生成的监控计划，确认是正常筛选结果还是计划任务失败。

监控评估器为单副本；重启会丢失本地历史并重新计算 pending 持续时间。
VMP 抓取评估器自身指标可用于后续设置独立的监控失效告警。
两个采集器的主备切换与盘中全链路表现，需要在真实候选存在时持续观察。

## 本地验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_collector_observability.py
docker run --rm -v "$PWD/deploy/observability:/rules:ro" -w /rules \
  --entrypoint /bin/promtool prom/prometheus:v2.55.1 test rules collector-alerts.test.yaml
```

规则测试涵盖指标完全消失、空名单、午休/休市、故障触发、交易窗口结束后的恢复、
主节点缺失、重复主节点、日历故障、Kafka 失败和 WAL 清空。
这些是受控测试证据，不代表向生产注入故障或发送测试通知。
