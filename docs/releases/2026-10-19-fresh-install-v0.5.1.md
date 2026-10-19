# v0.5.1 全新安装发布记录

日期：2026-10-19。集群：`cdaus2p98m08ce3b12nn0`。入口：<https://shanao.asia>。

状态：v0.5.1 新环境已上线，HTTPS、登录、九页浏览器和 SSE 验收通过。
完整业务签收仍待 TOS/SMTP 配置、行情数据同步和真实有信号回测；
GitHub 最新推送受本机凭据阻塞，未完成项不算通过。

## 范围

- 整合 `844f4ef` 的精简改造：统一 Web/API，独立 Task Worker 和报告调度，
  数据库计划启动的 Market Worker，隔离 Compute。
- Kafka/Flink、旧服务入口、品牌演示与第三方版本轮询退役。
- 同步需求、方案、开发、测试、运维与版本规则。
- 线上作为新环境安装，删除旧业务数据和应用凭据，不恢复旧用户、策略或报告。
  VKE 系统、镜像拉取认证和域名入口基础设施继续作为安装通道。
- TSP 从共享 PostgreSQL 创建三库、双运行角色和 schema v6，首次创建管理员。

## 制品

| 组件 | 标签 | Digest |
| --- | --- | --- |
| Policy / Market Worker | `v0.5.1` | `sha256:3f569013ce24412457b1d9c118921cd0b45157b09984b3e39775681d7e0bc789` |
| Web Runtime | `web-v0.5.1` | `sha256:cf1a2864e39678b4a5ee5cda7735494bf85771cff7a22a49d6ea4993d3a8d995` |
| Compute | `compute-v0.5.1` | `sha256:9bf4fce66903f48286812aa84dfdbcef3afcb9a7f68cc3e964e41ec5e47070f0` |

Web/Compute 制品源码为 `bf9ed85247e7fc03c32c8c8c087b0654b60e3679`；
二者 Manifest 均为 831 个文件，
`files_sha256=53e58cc890b65d2d47f6a1d3880dc7b4a50f809e7fd95ac3f3e19ef3a5225ef4`。
Policy 制品复用本轮已构建且未变更的源码。部署使用 Digest，版本镜像标签已指向最终
制品。源码以 annotated 候选 Tag `v0.5.1-rc.1` 归档最终部署清单和证据；
正式 `v0.5.1` 稳定标签待完整业务签收，不覆盖候选标签。

## 已核验状态

- 本轮开始时：功能分支为 `844f4ef`，远程 `main` 为 `d9fb82f`。
- 本轮开始时线上是旧 v0.5.0 拓扑，TSP 数据库 CrashLoop，公网返回 503。
- `fe1d4ae9122b54e11a096149e334c55a7b076125` 已推送并核对远程 main。
- 旧应用工作负载、应用 Secret 和 10 个 PVC 已删除；EBS PV 改为 Delete 回收。
  新 PostgreSQL、ClickHouse、Redis 及三个 EBS 卷已创建并 Ready。
- 新应用凭据已生成，Policy Schema 与 TSP 双库角色初始化 Job 均 Complete。
- 新 `tsp-product-state` PVC 已绑定；清空 Job Complete，
  `Fresh NAS state verified empty` 检查通过。
- 三个镜像已按 amd64 构建和推送；最终 API 两副本、Platform、Task Worker 和
  Compute 均完成滚动并 Ready。Web 初版快照阻塞、静态资源缺失已修复。
- TSP schema v6、管理员与 22 项内置策略初始化成功；初始化 Job 的 HOME 和 Numba
  缓存统一使用可写 `/tmp`，避免非 root 加载策略模块失败。
- 集群没有 `BANXIA_OBJECT_ACCESS_KEY/SECRET_KEY`，TOS 授权待配置。
- SMTP 未配置；邮件注册验证码和找回密码未验收。
- 初始管理员工作空间通过离线幂等 Job 初始化 Complete，存在真实业务库绑定和
  默认偏好；不会重置密码，重复运行不覆盖后续租户选择。
- 多租户向导完成状态写入 PostgreSQL 账户偏好，刷新与新 API 实例保持一致。
- Compute 原先固定 `/market` 与进程型 Pod 挂载不一致；修复为读取 `DATA_DIR`，
  实际 Pod 已执行 `n_day_low_reversal` 策略描述成功。

## 验证

- Policy：394 项，369 通过、25 项按环境跳过。
- TSP：Policy Facade、Task Worker、Compute、扩展与健康定向 46 项通过。
- 版本检查：`valid application version: 0.5.1`。
- 前端生产构建通过，2,870 个模块；TypeScript `tsc -b` 通过。
  本机 Node 使用锁定版本的 Darwin 原生依赖完成构建。此前助手状态测试超时，
  本机复核 5 项通过；完整前端 41 个文件、178 项回归通过。
- 首次 Schema Job 增加有上限的 PostgreSQL/ClickHouse 连接检查，避免数据库首次
  启动时耗尽 Job 重试次数。重跑已完成。
- Web 构建遇到 npm 网络超时，改为复用已验证的依赖与同版本 Codex 二进制。
  Dockerfile 增加独立 `runtime-deps` 阶段及 pnpm 下载缓存，Codex 0.144.3
  已核验；原生桥接与应用源码按最新版本重新构建，最终 Manifest 和摘要待回填。
- 公网首页、所有引用 JavaScript/CSS、登录和九项路由均 HTTP 200。
  `/health/ready` 为 200/ready，数据库、四类 Worker、Platform、公共文件及 Compute
  检查正常。登录 Cookie 具有 Secure/HttpOnly，匿名 Policy 请求 401。
- 独立 Chrome 新会话真实登录、完成向导、点击九项菜单并刷新，桌面 1440x1000 和
  窄屏 390x844 均显示对应业务标题，无向导回跳、无 JavaScript 页面错误。
  该验收证明路由和初装页面可用，不表示全量行情、AI 和回测结果已经可用。
- SSE 换票 200、事件流 200/text-event-stream，已收到首帧。
- Policy 报告列表 200/空列表；空安装尚无激活计划，监控返回明确
  `503 active plan is unavailable`，次日计划/当日实盘页面显示空态。
- 冷快照运行时复现确认股票 `refresh=False` 仍进入同步重计算并等待首次同步的
  独占计算槽。修复后同条件下立即返回；五项定向回归通过。板块使用同次股票快照，
  冷缓存跳过板块/异动计算。较广 amd64 模拟回归 26 通过、两项耗时失败；
  arm64 测试亦 26 通过、两项服务导入超出 40 秒。未放宽测试规避失败，不算全量通过。
- 首页资源实际请求发现缺失 JavaScript/CSS，完整 dist 已重新打包；
  新增 `check_static.py` 构建检查和资源引用回归，Manifest 不能替代引用完整性。
- 最新身份与工作空间真实 PostgreSQL 回归 23 项通过；Compute 回归 9 项通过，
  账户相关前端定向 9 项通过；最终前端全量 42 个文件、180 项通过，
  TypeScript 和 Vite 构建通过；本轮受影响 Python 文件 Ruff 通过。

## 未完成项与交接

- TOS AK/SK 为空，基础资料同步 `market-reference-sync` 因 AccessDenied 重试；
  对象归档、报告落 TOS 与完整行情链路尚未签收。没有创建替代存储或恢复旧凭据。
- 后续已补交互式安全配置、TOS/SMTP 验证工具和五表行情日终归档源码。
  线上 rustdx 主数据只读采集通过、ClickHouse 五表空态已核验；新归档 CronJob
  默认停用，尚未发布含新模块的镜像。实际归档与邮件仍需有效新凭据，
  详见 [TOS/SMTP 验收记录](../38-tos-smtp-acceptance.md)。
- SMTP/AI 未配置；验证码、邮件找回密码、AI 分析未验收。
- 尚无 Policy 激活计划和报告；实际有信号回测、交易时段覆盖、容量与恢复演练
  不在此次公网入口烟测结论内。
- 远程 `main` 最后确认是 `fe1d4ae9122b54e11a096149e334c55a7b076125`；
  后续修复与本发布清单已本地提交，但 macOS Git 凭据访问失败：
  `git-credential-osxkeychain` 阻塞，无交互模式报 `unable to get password from user`。
  必须在本机完成 GitHub 认证后执行 `git push origin main --follow-tags`，
  再核对远程 SHA 和候选 Tag；不能把镜像推送当作 Git 主线已同步。
- 诊断日志、临时浏览器文件及凭据不纳入源码发布。

## 恢复边界

本次不保留旧业务恢复点。应用回滚仅使用与新数据库兼容的制品，不引用已删除旧卷。
正式运行后的备份与恢复规则仍按运维手册执行。
