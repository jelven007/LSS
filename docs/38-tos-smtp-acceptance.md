# TOS、SMTP 配置与归档验收

日期：2026-10-19，基线：v0.5.1，入口：<https://shanao.asia>。

## 当前事实

- 集群 `banxia` 的 `BANXIA_OBJECT_ACCESS_KEY/SECRET_KEY` 均为空。
- 系统 SMTP 主机、用户名、密码与发件地址均为空。
- 没有已发布的 Policy 主数据快照；五类 ClickHouse 行情表均为空。
- rustdx 线上只读采集成功：51,879 条原始证券目录、5,225 只股票、
  92,021 条板块关系和五个原始文件，耗时 5.78 秒。这证明源可用，不证明已归档。
- ClickHouse 五表 `FINAL` 与 `JSONEachRow` 流式读取通过。
- 因缺少有效服务凭据，TOS 写入读回、真实 SMTP 投递、注册与找回密码尚未完成。
  不恢复全新安装前的 SMTP 凭据，不以模拟 SMTP 测试替代外部投递。

## 安全配置

准备火山 TOS S3 兼容 AK/SK、两个私有 Bucket，以及 SMTP 服务授权密码。
两个 Bucket 必须已经存在，运行凭据至少可检查 Bucket、写对象、读对象；
对象删除权限按策略删除功能的实际需要授予。不要将 AK/SK、SMTP 密码写入聊天、
源码、命令参数或 ConfigMap。

在本机终端使用已授权的 kubeconfig，进入仓库后运行：

```bash
python3 scripts/configure_external_services.py --tos --smtp
```

可选 `--context`、`--server`、`--namespace` 指定集群；默认命名空间 `banxia`。
脚本要求交互式终端，密钥输入不回显。先验证所有输入，然后通过 stdin 对
`banxia-secrets`、`banxia-runtime`、`tsp-product-secrets` 做 merge patch，
保留数据库、Redis、Token 等其他键；不获取旧 Secret。
更新后重启 API、参考同步、报告、Market Worker 和 Task Worker。

SMTP `465` 使用 `ssl`，`587` 使用 `starttls`。系统配置统一用于注册、
找回密码和通知，不另外配置用户级 SMTP 密码。

仅核对是否配置，不写对象、不发邮件：

```bash
python3 scripts/verify_external_services.py
```

## TOS 与主数据验收

先等待 `market-reference-sync` 发布首次快照，再运行：

```bash
python3 scripts/verify_external_services.py --tos
```

工具在两个 Bucket 各写一个 `acceptance/tos/` 探针对象并读回，随后读取
PostgreSQL 已发布快照所引用的 Manifest，校验每个原始资产的 SHA-256 和长度。
探针对象保留为验收记录，不删除业务对象。验收输出只含配置状态、快照 ID、
对象数量和报价覆盖数。

主数据归档覆盖证券原始目录、板块文件、行业配置、交易日日报价与 Manifest。
它不等于全量日线、分钟或盘口历史回填。

## 行情日终归档

新增 `banxia_strategy.application.market_archive`，读取五类已采集数据：

| ClickHouse 表 | 内容 |
| --- | --- |
| `market_quote_snapshot` | 盘口快照 |
| `market_bar_1m` | 一分钟线 |
| `market_feature_realtime` | 实时特征 |
| `market_history_bar` | 历史 K 线 |
| `market_board_capital_history` | 板块资金与市值 |

固定白名单表，参数化交易日；通过 `FINAL` 去重和排序后流式导出
`JSONEachRow+gzip`，保留原始价格精度与空值。临时文件位于任务临时磁盘，
不将整日行情装入内存。对象内容寻址，写完逐个读回核对 SHA-256 和字节数。
导出行数必须等于源表行数，源表行数与行内容指纹在导出前后必须一致。
所有对象核验后才写 Manifest；失败不产生可签收的清单。

对象路径：

```text
market-raw/clickhouse/dataset=<table>/trade_date=<date>/<sha256>.json.gz
market-raw/manifests/dataset=clickhouse_market/trade_date=<date>/<sha256>/manifest.json
```

任务不会修改源表或清理 WAL。Manifest 的 `scope=collected_data` 明确仅覆盖
已经采集的数据；空表记录 `empty`，整日五表皆空直接失败，不能据此宣称全市场覆盖。
同一稳定数据重复执行使用相同对象路径；新增或修订数据产生新清单。

在具备生产依赖和有效凭据的容器中执行：

```bash
python -m banxia_strategy.application.market_archive --date 2026-10-16
```

默认日期为上海时区前一自然日。显式归档当天仅允许 20:00 后执行；
完整度以源表稳定性与数据覆盖验收为准，不能仅凭时间判断。
部署清单 `market-archive` CronJob 在周二至周六 00:10 归档前一天数据，
默认 `suspend: true`。归档镜像与停用任务已部署，手动完成有数据的交易日
验收后再启用；不要让旧镜像运行新入口。

归档候选制品使用独立镜像别名，既有实时/报告镜像 Digest 不变：

| 项目 | 值 |
| --- | --- |
| 源码提交 | `9db47bcd30041e5dc023db086f8cbf15da9e8f3d` |
| 镜像标签 | `policy-v0.5.1-archive-9db47bc` |
| 镜像 Digest | `sha256:873d58fac341555873d5928bd49032f5d977b88a8bfdd40d14906de93302d57a` |
| 平台 | `linux/amd64` |
| 模块 SHA-256 | `953dd5174ff2416fd0113741067576ab845df2ebe0687fb36693720537625a38` |

以已发布 Policy 基础 Digest 构建增量候选，包含归档模块及源码副本。
`market-archive-smoke-9db47bc` 已 Complete，真实 Pod 读取模块核对 SHA-256，
确认五类数据集入口可加载；这不等于真实对象归档完成。

修改源清单的 `suspend` 为 `false` 并重新发布，保证后续 apply 不会重新暂停任务。
临时开启可使用：

```bash
kubectl -n banxia patch cronjob market-archive --type=merge \
  --patch '{"spec":{"suspend":false}}'
```

归档文件可用于后续隔离恢复测试；本轮没有执行数据库恢复，不能算恢复验收通过。

## 邮件验收

由用户指定允许接收测试邮件的地址后运行：

```bash
python3 scripts/verify_external_services.py --email-to <指定收件地址>
```

仅发送一封测试邮件，单次尝试；不读取默认多用户收件列表。
`smtp_accepted=true` 只表示 SMTP 服务接受请求，仍需收件箱确认到达。
随后在测试账户完成注册验证码、错误验证码拒绝、正确验证码注册、找回验证码、
密码重置和新旧密码验证；业务管理员密码不用于这项测试。

## 验证与限制

- 新增归档测试 8 项、配置安全测试 5 项通过。
- 主数据同步回归 4 项、存储适配器回归 34 项通过。
- 邮件默认配置、通知适配器和发送测试接口回归 37 项通过（模拟传输）。
- 受影响文件 Ruff 通过。
- Kustomize 渲染及集群整包 server dry-run 通过；停用 CronJob 已应用，
  归档镜像烟测 Job Complete。
- 线上只读查询已完成；实际写入、日终有数据归档和邮件流程需有效新凭据。
- <https://shanao.asia/health/ready> 仍返回 200/ready。
- GitHub 主线和候选标签已同步到 <https://github.com/jelven007/LSS>：
  `main=132db778c03b5e4d724afd510c63d269c7764211`，`v0.5.1-rc.1` 指向
  `8b3a82db8fc5fdb7dc4cf9fb7204702b866b8b94`。
- 不创建正式稳定 Tag，不将源码、模拟测试或健康检查当作业务签收。
