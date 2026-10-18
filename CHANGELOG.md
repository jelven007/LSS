# Changelog

项目采用语义化版本，生产发布使用带注释 Git Tag `vX.Y.Z`。镜像标签与 Git Tag
同版本，生产清单进一步固定镜像 digest。

## v0.5.1 - 2026-10-18

### Changed

- TSP 与 Policy 合并为单一 Web/API 入口，Policy 改为进程内 ASGI 调用。
- 实时行情、特征、策略和投影收敛为带 SQLite WAL 的 `market-worker`。
- TSP 研究、实时、通知和定时任务收敛为统一 `task-worker`。
- Policy 与 TSP 共享一个 PostgreSQL 集群，控制库和业务库使用独立运行角色。
- 对象归档切换到火山引擎 TOS，生产清单不再部署 MinIO 和 Policy 报告 NAS。
- 计算从特权 DinD 迁移到无密钥、禁止出网的独立 `tsp-compute` Pod。
- Policy 原生桥接统一使用 crates.io 固定版 `jelven-rustdx-complete 1.12.2`，镜像构建
  不再依赖 GitHub 读取凭据。

### Removed

- VKE 生产拓扑中的 Kafka、Flink、独立 Policy API、独立 TSP PostgreSQL、SSE Gateway
  以及四个独立 TSP Worker 容器。

### Compatibility

- Kafka/Flink 实现和本地 Compose 暂时保留，仅用于历史回归和应急回滚。
- `/api/policy/*` 前端契约保持不变。

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
