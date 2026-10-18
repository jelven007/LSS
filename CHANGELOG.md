# Changelog

项目采用语义化版本，生产发布使用带注释 Git Tag `vX.Y.Z`。镜像标签与 Git Tag
同版本，生产清单进一步固定镜像 digest。

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
