# TSP 产品整合说明

## 基线

- 上游仓库：`https://github.com/jelven007/TSP`
- 上游提交：`bb8ea7045b613f189159e7ebd84162567ffb09e5`
- 上游版本：`0.4.8`
- 整合产品版本：`0.5.0`
- 许可证：MIT，原许可证保留在 `product/LICENSE`

`product/` 保留 TSP 的 React/FastAPI 产品、完整业务模块、测试和服务端部署定义。
参考仓库中的截图、桌面打包产物、Git 元数据和 GitHub 配置未导入。

## 产品菜单

主导航固定为：

1. 首页
2. 自选
3. 策略
4. 次日计划
5. 当日实盘
6. 复盘
7. 回测
8. 个股分析
9. 设置

首页直接使用 TSP 市场看板。自选、策略、复盘、回测、个股分析、设置以及注册、登录、
找回密码使用 TSP 实现。TSP 其他业务页面和后端能力仍保留，但不进入主导航。

## Policy 专用业务域

次日计划和当日实盘继续由现有 Policy 服务负责计算和持久化：

```text
浏览器
  -> TSP 会话认证与租户权限
  -> /api/policy/*
  -> TSP Policy bridge
  -> Policy /api/v1/*
  -> PostgreSQL / Redis / ClickHouse
```

浏览器不会获得 Policy API Token。桥接只开放次日计划、报告任务和盘中监控所需的
固定路径；日期、股票代码和任务 ID 在转发前校验。读取需要有效 TSP 会话，报告刷新还
需要 `data:manage` 权限。

运行配置：

```dotenv
POLICY_SERVICE_URL=http://api.banxia.svc.cluster.local
POLICY_SERVICE_TOKEN=<server-side-token>
POLICY_SERVICE_TIMEOUT_SECONDS=30
```

生产环境应使用 Kubernetes Secret 或权限为 `0600` 的环境文件保存 Token。

## 数据归属

| 数据 | 权威系统 |
| --- | --- |
| 用户、租户、角色、会话、找回密码 | TSP |
| 自选、TSP 策略、复盘、回测、个股分析、用户设置 | TSP |
| Policy 次日计划、候选和策略版本 | Policy PostgreSQL |
| Policy 盘中行情和决策投影 | Policy ClickHouse / Redis / PostgreSQL |
| 行情采集与 rustdx 连接 | 两套系统按各自现有边界运行，部署时必须控制连接预算 |

## 生产发布边界

`v0.5.0` 统一部署到火山引擎 VKE：

1. Policy 工作负载使用当前主线镜像重建，原 `Retain` 数据卷继续挂载。
2. TSP 使用独立 PostgreSQL 和 NAS 状态卷，不覆盖 Policy 数据库。
3. `shanao.asia` 根路径切换到 TSP，Policy API 只保留集群内 Service。
4. `/api/events*` 由 Gateway 处理，其余请求由 API 处理。
5. SMTP 和 Policy Token 由 Kubernetes Secret 注入。

完整产品的 Compute Broker 在 `v0.5.0` 使用 Pod 内 DinD 过渡方案：不挂载宿主 Docker
socket，Compute 无网络、无凭据、只读公共行情，但 DinD 容器仍需 `privileged`。该例外
必须使用固定 digest、NetworkPolicy 和单 Pod 边界控制，并在 Kubernetes 原生 Runner
完成后删除。

清单、Secret 契约和验收见
[`deploy/kubernetes/README.md`](deploy/kubernetes/README.md)。
