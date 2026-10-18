# rustdx 数据源迁移

日期：2026-10-15。仓库与线上新采集链路均已迁移至 rustdx 原生实现，
生产镜像为 `rustdx-20261015-r1`；镜像摘要与验收见
[生产发布记录](releases/2026-10-15-rustdx-r1.md)。
迁移前的报告、研究快照和数据库记录保留原始来源。

## 实现

`make_provider()` 是运行时构造入口，默认返回 `RustdxProvider`。
`BANXIA_DATA_PROVIDER=mootdx` 自动归一化为 `rustdx`；不导入旧客户端，也不回退旧数据源。
已删除 `MootdxProvider`，项目依赖不再包含 mootdx、tdxpy、pytdx 或 pandas。

`native/rustdx_native` 使用 PyO3 调用 rustdx 的 TCP 客户端和协议解析器。
行情请求直接在 Python 进程中执行，不依赖 rustdx HTTP 服务。每个采集工作线程拥有自己的
连接；请求期间释放 Python GIL，原有连接复用、批量请求、节点切换和并发回填继续工作。
协议解析发生 panic 时关闭连接并返回错误，避免继续使用损坏连接。

上游锁定 crates.io `jelven-rustdx-complete =1.12.2`，发布来源提交为
`9dfc024c9662fa5091099c1f17610569ee2eca8b`。`Cargo.lock` 保存发布包校验和，镜像构建
不依赖 GitHub 读取凭据；Python 桥接检查同一版本，版本不匹配时提示重建。

| 数据 | 原生实现 | 项目使用方式 |
| --- | --- | --- |
| 证券目录 | `Client::stocks` | 沪深目录、主板/创业板/科创板、参考快照 |
| 盘口 | `Client::quotes` | 实时监控、五档委托量、每日收盘快照 |
| 股票日/周/月/年及一分钟 K 线 | `Client::bars` | 报告、监控、历史回填、研究 |
| 上证指数日线 | `Client::index_bars` | 交易日序列；原生指数日线使用类别 4 |
| 历史分时 | `Client::history_minute` | 至多 240 个价格/成交量采样 |
| 历史分笔 | `Client::history_transaction` | 研究脚本分页核验，时间精度为分钟 |
| 财务摘要 | `Client::finance` | 股本、市值、财务指标 |
| 除权除息及股本变动 | `Client::xdxr` | 公司行为、历史股本估算 |
| F10 | `Client::f10` | 栏目目录、正文、行业分类 |
| 板块/行业原始文件 | `BlockInfoMeta/BlockInfoChunk` | 四类板块文件与 `tdxhy.cfg` |
| 板块文件解析 | `parse_block_content` | 板块名称、类型、成分证券 |
| 休市判断 | `TradingCalendar::try_is_trading_day` | 下一交易日和非交易日门控 |

本次迁移覆盖项目已经使用的数据接口。历史财务包及新的数据能力仍按原持久化设计推进。

## 安装与配置

本地需要 Python ≥3.9、Rust ≥1.89 和 C/C++ 构建工具。
原生轮子采用 `abi3-py39`，构建平台须与运行平台兼容。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install ./native/rustdx_native
.venv/bin/python -m pip install -e '.[production,test]'
.venv/bin/banxia-strategy doctor
```

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `BANXIA_DATA_PROVIDER` | `rustdx` | 唯一启用的后端；旧值 `mootdx` 自动归一化 |
| `BANXIA_RUSTDX_SERVERS` | 内置节点池 | 逗号分隔的 IPv4:端口 |
| `BANXIA_RUSTDX_TIMEOUT_SECONDS` | `5` | 单连接超时秒数；原生层限制为 1–60 |

Dockerfile 使用独立 Rust 构建阶段，以 `--locked --release` 生成并安装原生轮子。
最终 Python 镜像无需 Rust 编译器和退役客户端。Compose 与 Kubernetes ConfigMap 均已
加入上述变量，macOS launchd 安装脚本也会安装桥接。

发布时构建新镜像，更新应用镜像标签并滚动重启服务。旧镜像标签仍代表迁移前代码，
仅修改环境变量不足以完成线上迁移。

## 数据口径与兼容

- 价格以元计，成交量及五档委托量以手计，成交额以元计；保持原策略换算逻辑。
- 原生 `bid1_vol` / `ask1_vol` 等字段映射为项目既有 `bid_vol1` / `ask_vol1`。
- 历史分时依次标记 09:31–11:30、13:01–15:00。历史分时是价格采样，
  OHLC 均取该采样价；成交额保持缺失，不伪造 OHLC 或成交额。
- 空分时、超过 240 点、非正价格和无效成交量明确失败，触发节点重试；
  部分有效分时保留实际点数，执行评估继续使用既有完整性和 T+1 校验。
- 当前锁定版本的休市日历覆盖 2024–2026 年；超出范围明确失败，禁止仅凭工作日推测。
- 新行情原始记录保存 `source=rustdx`。历史接口按存储记录来源返回；
  同页可返回 `mootdx,rustdx`，来源未记录的行返回 `unknown`。
- 冻结研究数据、旧报告及 `config/monitor_watchlist.json` 中已核验的旧来源不改写。
  复用缓存时须依据输入来源标记结果；切换客户端不会把旧采集事实变成新事实。

## 验证

2026-10-15 在同一 `117.34.114.14:7709` 节点，以 2026-10-14 完整行情进行对照。
证券为 `600000`、`000001`、`300750`：

- 每只 20 根日线的 OHLC、成交量、成交额一致。
- 每只 240 点历史分时价格和成交量一致。
- 盘口价格、昨收、OHLC、成交额及选取的一档/五档委托量一致。
- 财务摘要全部 37 个字段一致；除权记录日期、类别和数量一致。
- 每只 20 条历史分笔的时间、价格、成交量及买卖方向一致。
- 四份板块文件与行业配置逐字节一致。

原始统计见 [实测对照结果](assets/rustdx-migration/comparison.json)。
这些检查证明所选样本的兼容性，不构成上游所有日期和字段的无限覆盖承诺。

最终验证：独立 Python 环境未安装 mootdx、tdxpy、pytdx 或 pandas，363 项测试中
343 项通过、20 项按环境跳过。Docker 镜像成功构建，Linux 原生桥接加载与行情检查通过；
macOS 和 Linux 的 `doctor` 均返回 2026-10-14 行情及 40 条主板涨停记录。
真实参考快照解析得到 5,227 只 A 股、92,620 条板块归属及 5 份原始文件。
Compose 配置检查和 VKE Kustomize 渲染均通过。

自动化验证包含 Provider 契约、原生响应适配、节点失败重试、分钟时间边界、
历史来源保留及既有策略/T+1 回归测试：

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
docker compose -f deploy/compose/docker-compose.yml config --quiet
kubectl kustomize --load-restrictor=LoadRestrictionsNone deploy/kubernetes/overlays/volcengine
```

迁移前能力矩阵见 [mootdx 能力归档](26-mootdx-capability-matrix.md)；
持久化模型见 [非实时数据持久化设计](11-mootdx-persistence.md)。
