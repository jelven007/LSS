# v0.5.1 全新安装发布记录

日期：2026-10-19。集群：`cdaus2p98m08ce3b12nn0`。入口：<https://shanao.asia>。

状态：发布执行中；源码、镜像与公网验收分别记录，未完成项不算通过。

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
| Web Runtime | `web-v0.5.1` | 初版 `4e3ebc7243d3` 已推送；资源完整性修复中，最终摘要待回填 |
| Compute | `compute-v0.5.1` | 初版 `9e3b5a035936` 已 Ready；随源码修复重新构建 |

正式 Git Tag 尚未创建；验收后指向包含最终清单和证据的发布提交。

## 已核验状态

- 本轮开始时：功能分支为 `844f4ef`，远程 `main` 为 `d9fb82f`。
- 本轮开始时线上是旧 v0.5.0 拓扑，TSP 数据库 CrashLoop，公网返回 503。
- `fe1d4ae9122b54e11a096149e334c55a7b076125` 已推送并核对远程 main。
- 旧应用工作负载、应用 Secret 和 10 个 PVC 已删除；EBS PV 改为 Delete 回收。
  新 PostgreSQL、ClickHouse、Redis 及三个 EBS 卷已创建并 Ready。
- 新应用凭据已生成，Policy Schema 与 TSP 双库角色初始化 Job 均 Complete。
- 新 `tsp-product-state` PVC 已绑定；清空 Job Complete，
  `Fresh NAS state verified empty` 检查通过。
- 三个镜像已按 amd64 构建和推送；Compute Ready，Web 初版快照阻塞与静态资源问题
  已定位，修复后重新验收。
- TSP schema v6、管理员与 22 项内置策略初始化成功；初始化 Job 的 HOME 和 Numba
  缓存统一使用可写 `/tmp`，避免非 root 加载策略模块失败。
- 集群没有 `BANXIA_OBJECT_ACCESS_KEY/SECRET_KEY`，TOS 授权待配置。
- SMTP 未配置；邮件注册验证码和找回密码未验收。

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
- 公网健康、登录、Policy 页面与 SSE 尚未通过，不能宣称部署完成。
- 冷快照运行时复现确认股票 `refresh=False` 仍进入同步重计算并等待首次同步的
  独占计算槽。修复后同条件下立即返回；五项定向回归通过。板块使用同次股票快照，
  冷缓存跳过板块/异动计算。较广 amd64 模拟回归 26 通过、两项耗时失败待原生复核。
- 首页资源实际请求发现缺失 JavaScript/CSS，完整 dist 已重新打包；
  新增 `check_static.py` 构建检查和资源引用回归，Manifest 不能替代引用完整性。

## 恢复边界

本次不保留旧业务恢复点。应用回滚仅使用与新数据库兼容的制品，不引用已删除旧卷。
正式运行后的备份与恢复规则仍按运维手册执行。
