# Changelog

项目采用语义化版本，生产发布使用带注释 Git Tag `vX.Y.Z`。镜像标签与 Git Tag
同版本，生产清单进一步固定镜像 digest。

## v0.5.1 - 2026-10-18

### Changed

- TSP 与 Policy 合并为单一 Web/API 入口，Policy 改为进程内 ASGI 调用。
- 实时行情、特征、策略和投影收敛为带 SQLite WAL 的 `market-worker`。
- TSP 研究、实时、通知和定时任务收敛为统一 `task-worker`。
- `tsp-task-worker` 独立部署，API 的 HPA 不再复制后台任务进程。
- Policy `report-scheduler` 常驻消费页面刷新和历史任务，取代重复的报告 CronJob。
- Market Worker 直接加载数据库监控计划，不依赖本地报告文件。
- Policy 与 TSP 共享一个 PostgreSQL 集群，控制库和业务库使用独立运行角色。
- 对象归档切换到火山引擎 TOS，生产清单不再部署 MinIO 和 Policy 报告 NAS。
- 计算从特权 DinD 迁移到无密钥、禁止出网的独立 `tsp-compute` Pod。
- Policy 原生桥接统一使用 crates.io 固定版 `jelven-rustdx-complete 1.12.2`，镜像构建
  不再依赖 GitHub 读取凭据。

### Fixed

- 冷启动股票快照不再等待首次同步的独占计算槽，板块复用同次快照。
- 镜像构建校验首页引用资源，避免缺失 JavaScript/CSS 导致公网白屏。
- 多租户向导完成标记按账户写入 PostgreSQL，刷新与新 API 副本使用同一状态。
- 全新安装通过幂等离线命令创建管理员工作空间，业务请求和 SSE 有真实租户上下文；
  重复初始化保留密码和后续租户选择。
- Compute 执行器使用服务配置的只读公共行情路径，兼容进程型 Pod 的挂载目录。

### Removed

- VKE 生产拓扑中的 Kafka、Flink、独立 Policy API、独立 TSP PostgreSQL、SSE Gateway
  以及四个独立 TSP Worker 容器。
- Kafka/Flink 源码、依赖、本地服务、旧 Worker 入口与无效部署补丁。
- 品牌风格预览、开发演示页、演示数据与强制预警接口、第三方开源发布监控。

### Compatibility

- 本地 Compose 统一使用 Market Worker；回滚依赖 Git 历史和对应旧镜像。
- `/api/policy/*` 前端契约保持不变。
- 2026-10-19 生产按全新环境安装，不恢复旧业务数据；应用凭据重新生成，
  空库初始化、镜像摘要、公网验收与未配置项见本次发布记录。

## v0.5.0 - 2026-10-18

### Added

- 导入 TSP `0.4.8` Web 产品主干，形成完整 React/FastAPI 多租户产品。
- 新增首页、自选、策略、次日计划、当日实盘、复盘、回测、个股分析、设置九项菜单。
- 新增服务端 Policy Bridge，浏览器不接触 Policy Token。
- 新增注册、登录、找回密码、RBAC、租户隔离和预置管理员初始化。
- 新增火山引擎 VKE 的 TSP PostgreSQL、平台、API、Gateway、Worker 和隔离计算部署。

### Changed

- 桌面侧栏默认展开并支持图标栏收缩；移动端抽屉不遮挡页面标题。
- 次日计划和当日实盘先展示默认功能，依赖失败降级为页面内状态，不阻断路由。
- `shanao.asia` 入口切换到 TSP Web，Policy API 转为集群内桥接服务。
- Policy 全部业务工作负载使用同一 `v0.5.0` 主线镜像重建。

### Removed

- Android/APK 工程、构建产物和发布流程。
- 旧 Web 单页作为公网入口的部署方式。

### Verification

- Policy Python 测试、基础设施渲染测试。
- TSP 前端 200 项测试、TypeScript、ESLint 和生产构建。
- Policy Bridge 11 项后端测试。
- VKE Kustomize 渲染、服务端 dry-run、滚动部署和公网健康验收。
