# E06C Docker Compose、健康检查与持久化验收

## 1. 部署组成

单机部署由三个服务组成：

```text
Qdrant healthy
  → qdrant-init 校验或创建两套检索投影后正常退出
    → FastAPI 加载固定 BGE 模型并通过 /ready
```

- `qdrant`：固定 `qdrant/qdrant:v1.19.1`，数据写入命名卷；
- `qdrant-init`：幂等初始化任务。现有 collection 数量正确时只校验；全新卷上构建正式 29 条、实验 58 条并发布 alias；
- `app`：以非 root 用户运行 FastAPI，使用 CPU-only PyTorch，镜像内包含固定 revision 的 `BAAI/bge-small-zh-v1.5`。

应用镜像实测约 450 MB，目标平台为 `linux/amd64`。运行时设置 `TRANSFORMERS_OFFLINE=1`，因此服务启动不依赖 Hugging Face 可用性，也不会静默切换模型版本。

## 2. 健康与就绪

- `/api/v1/health` 仅表示 HTTP 进程存活；
- `/api/v1/ready` 同时检查 Qdrant 连通性、`corpus_current` 和 `m3_experiment_current`；
- Qdrant 或必要 alias 缺失时返回 503，不接收业务流量；
- readiness 错误不回传内部连接细节。

当前运行栈中 Qdrant 和应用容器均为 `healthy`，readiness 返回三个检查项全部 `ready`。

## 3. 热重启持久化验收

执行 `scripts/run_m6_compose_persistence_smoke.py`，在重启 Qdrant 和应用容器前后比较状态：

| 检查 | 重启前 | 重启后 |
| --- | --- | --- |
| `corpus_current` 目标 | `corpus_formal_v1` | `corpus_formal_v1` |
| 正式 point 数 | 29 | 29 |
| `m3_experiment_current` 目标 | `m3_experiment_release_v1` | `m3_experiment_release_v1` |
| 实验 point 数 | 58 | 58 |
| 应用 readiness | ready | ready |

重启后无模型调用的条件澄清请求正常返回，证明 HTTP 服务和查询核心恢复。

## 4. 隔离冷启动验收

执行 `scripts/run_m6_compose_cold_start.py`，使用独立项目名、18000/16333 端口和临时卷，不接触当前运行栈。全新空卷上的初始化任务实际创建并发布了两套投影：

- 冷启动至业务就绪：24.489 秒；
- 正式投影：29 points；
- 实验投影：58 points；
- 两个 alias 和应用 readiness 均通过；
- 验收后的临时容器、网络和命名卷已删除。

## 5. 运行与停止

首次准备 `.env` 后：

```powershell
docker build -t drainage-rag-app:local .
docker compose up -d --no-build
docker compose ps
```

普通停止使用 `docker compose down`，它保留 Qdrant 命名卷。`docker compose down --volumes` 会删除索引数据，只能用于明确的重置或隔离测试，不能作为日常停止命令。

## 6. 数据与安全边界

- `.env` 不进入镜像，也不提交 Git；
- `data/raw` 以只读方式挂载，公开仓库可以不包含受限 PDF；
- 查询 trace 写入宿主机 `data/observability` 并被 Git 忽略；
- SQLite、文件和语料 manifest 仍是事实源，Qdrant 命名卷仍是可重建投影；
- 本次证明的是单机冷启动和重启持久化，不代表多副本编排、滚动升级或云端高可用。

机器可读结果：

- `data/eval_results/m6-compose-persistence-v1.json`
- `data/eval_results/m6-compose-cold-start-v1.json`
