# 科学实验溯源工作台（Experiment Provenance Workbench）

CQRS + Event Sourcing 全栈示例：命令追加 `event_store`，查询走投影表；Vue 前端查看 Run、事件时间线与血缘。支持按指标名配置上下限阈值，越界指标自动生成告警并可跳转对应 Run 详情。

## How to Run

```bash
cd projects/03-experiment-provenance
docker compose up --build
```

> 镜像默认走 `docker.m.daocloud.io`（便于国内拉取）；前端 npm 使用 `npmmirror`。若你可直连 Docker Hub，可将 Dockerfile / compose 中的镜像前缀改回官方名。

首次启动会：

1. 拉起 PostgreSQL
2. 启动 FastAPI 后端并建表
3. `seed` 写入 2 条已完成 Run + 1 条进行中 Run
4. 构建并启动前端（nginx）

停止：

```bash
docker compose down
```

本地后端测试（可选，需 Python 3.11+）：

```bash
cd backend
pip install -r requirements.txt
pytest -q
```

## Services / 端口

| 服务 | 地址 |
|------|------|
| Frontend | http://localhost:3173 |
| Backend API | http://localhost:8173 |
| PostgreSQL | localhost:54373 |

容器内：

- `db`：Postgres `provenance/provenance`，库名 `provenance`
- `backend`：Uvicorn `:8000`
- `seed`：一次性灌数后退出
- `frontend`：nginx `:80`，`/api` 反代到 backend

## 账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| researcher | lab123456 | 可发命令（Start/Metric/Artifact/Complete/Abort）、可增删改指标阈值 |
| auditor | audit123456 | 只读事件、投影、阈值与告警 |

## 指标阈值与告警

- **阈值**：导航「阈值」页按指标名配置上下限（至少一项；下限 ≤ 上限）。研究员可增改删，审计员只读。
- **告警**：记录指标时若越界即生成告警；**保存/收紧阈值时会重新扫描历史已记录指标**，越界点立即补生成告警。导航「告警」页列出 Run、指标、值、阈值、时间，点击「Run 详情」跳转对应 Run。
- 告警按 `(run_id, metric_name, step, bound)` 去重，重复扫描不会产生重复行；删除阈值保留历史告警。
- Seed 预置演示阈值：`tm_score [0.5, 0.9]`、`hit_rate ≥ 0.2`、`loss ≤ 1.5`。其中 `loss=1.84`、`hit_rate=0.12` 越界，首次启动即可在告警页看到 2 条记录。

## Verification

1. 打开 http://localhost:3173 ，使用 `researcher` / `lab123456` 登录
2. 在 Run 列表看到 seed 数据（含进行中与已完成）
3. 点击「新建 Run」，填写 project/name、dataset sha、code commit，启动
4. 在详情页记录指标、挂载产物，再 Complete（或 Abort）
5. 打开「事件时间线」确认 version 递增的原始事件
6. 打开「血缘」确认 code_commit、dataset 指纹、artifacts、metrics
7. 打开「告警」：seed 的 `loss=1.84`、`hit_rate=0.12` 越界已在列表中，点击「Run 详情」可跳转
8. 打开「阈值」：将 `tm_score` 上限从 0.9 调低为 0.75 并保存，回到「告警」页可见 `tm_score=0.81` 的越界新行
9. 健康检查：`GET http://localhost:8173/api/health`
10. 用 `auditor` 登录：可看列表/事件/血缘/阈值/告警，命令与阈值编辑不可用

终态或 `expected_version` 不匹配时，API 返回 **409**。

## 架构要点

- **命令**：`StartRun` / `RecordMetric` / `AttachArtifact` / `CompleteRun` / `AbortRun`
- **事件**：`RunStarted` / `MetricRecorded` / `ArtifactAttached` / `RunCompleted` / `RunAborted`
- **event_store**：`(aggregate_id, version)` 唯一；冲突 → 409
- **run_projections**：查询侧投影（状态、指标、产物等）
- **metric_thresholds**：指标名 → 上下限（`upsert` 时回扫历史指标生成告警）
- **metric_alerts**：越界记录（`(run_id, metric_name, step, bound)` 唯一去重）
