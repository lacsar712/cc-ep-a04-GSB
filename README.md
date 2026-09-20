# 科学实验溯源工作台（Experiment Provenance Workbench）

CQRS + Event Sourcing 全栈示例：命令追加 `event_store`，查询走投影表；Vue 前端查看 Run、事件时间线与血缘。支持按指标名配置阈值，越界自动生成告警。

## How to Run

```bash
cd projects/03-experiment-provenance
docker compose up --build
```

> 镜像默认走 `docker.m.daocloud.io`（便于国内拉取）；前端 npm 使用 `npmmirror`。若你可直连 Docker Hub，可将 Dockerfile / compose 中的镜像前缀改回官方名。

首次启动会：

1. 拉起 PostgreSQL
2. 启动 FastAPI 后端并建表
3. `seed` 写入 2 条已完成 Run + 1 条进行中 Run + 3 条演示阈值（`loss` 上限 1.5，`tm_score` 上限 0.9，`hit_rate` 下限 0.1；seed 的 `loss=1.84` 会立即触发一条告警）
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
| researcher | lab123456 | 可发命令（Start/Metric/Artifact/Complete/Abort）、可改阈值 |
| auditor | audit123456 | 只读事件、投影、阈值与告警 |

## 指标阈值与告警

- **阈值**：按指标名配置上下限（`PUT /api/thresholds/{metric_name}`，仅研究员；`GET /api/thresholds` 双方可读）。上下限至少填一项，保存后对全部已记录指标重新评估。
- **告警**：`RecordMetric` 越界时即时生成；调低/调高阈值后历史越界行也会补齐或清除（告警是阈值 × 指标的派生投影）。`GET /api/alerts` 可按 `run_id`、`metric_name` 过滤，返回 Run、指标、值、阈值快照与越界方向。
- 前端导航有「阈值」「告警」入口：阈值页研究员可增改删、审计员只读；告警页每行可点进对应 Run 详情。

## Verification

1. 打开 http://localhost:3173 ，使用 `researcher` / `lab123456` 登录
2. 在 Run 列表看到 seed 数据（含进行中与已完成）
3. 点击「新建 Run」，填写 project/name、dataset sha、code commit，启动
4. 在详情页记录指标、挂载产物，再 Complete（或 Abort）
5. 打开「事件时间线」确认 version 递增的原始事件
6. 打开「血缘」确认 code_commit、dataset 指纹、artifacts、metrics
7. 健康检查：`GET http://localhost:8173/api/health`
8. 用 `auditor` 登录：可看列表/事件/血缘/阈值/告警，命令与阈值写操作不可用
9. 打开「告警」：seed 的 `loss=1.84`（上限 1.5）已在列表中，点「Run 详情」可跳到对应 Run
10. 打开「阈值」，把 `tm_score` 上限从 0.9 调低到 0.75 并保存 → 回到「告警」可看到 `tm_score=0.81` 的越界行（历史指标被重新评估）

终态或 `expected_version` 不匹配时，API 返回 **409**。

## 架构要点

- **命令**：`StartRun` / `RecordMetric` / `AttachArtifact` / `CompleteRun` / `AbortRun`
- **事件**：`RunStarted` / `MetricRecorded` / `ArtifactAttached` / `RunCompleted` / `RunAborted`
- **event_store**：`(aggregate_id, version)` 唯一；冲突 → 409
- **run_projections**：查询侧投影（状态、指标、产物等）
- **metric_thresholds / metric_alerts**：阈值配置与告警派生投影（阈值变更即全量重估该指标告警）
