# Travel Agent Orchestrator

一个面向酒店搜索、沙箱预订和售后服务的可追踪 Multi-Agent 平台。系统不是把一条流水线机械地拆成多个 Agent，而是按工具权限、Prompt、上下文和业务责任划分 `Supervisor`、`Discovery`、`Booking` 与 `Customer Service` 四个推理角色，并将支付实现为确定性的补偿 Saga。


> [!IMPORTANT]
> 所有报价、预订、支付、退款和投诉都是可重复的沙箱业务，不会创建真实酒店订单，也不会收集银行卡或真实支付信息。高德 MCP 只提供地点数据，不代表酒店库存可真实预订。

![Travel Agent Orchestrator 工作区预览](docs/assets/web-preview.svg)

## 项目亮点

- 自定义 LangGraph `StateGraph`，显式实现路由、Specialist、handoff、HITL、工具执行和收尾节点。
- 每个 Specialist 使用独立 Prompt、模型配置、上下文投影和工具白名单，越权工具不会注册给模型。
- Agent 间通过 `HandoffEnvelope` 传递目标、约束、报价和订单引用，不靠自然语言反推业务状态。
- 只读工具自动执行；预订、改期、取消、退款和投诉必须通过图级风险策略与人工审批。
- 报价、库存、订单状态和金额由工具层校验，LLM 不能直接写库或修改支付金额。
- 支付成功但订单提交失败时自动退款补偿；幂等键阻止重复下单与重复扣款。
- 增量摘要、关键事实、任务计划、审批状态和原始消息持久化到 LangGraph checkpoint。
- 高德大型结果存为工具产物，模型只接收紧凑摘要并可安全分页，降低上下文消耗。
- PostgreSQL 保存脱敏执行轨迹；网页可查看路由、Agent、handoff、工具、审批和耗时。
- 36 个提交到仓库的离线场景，加上权限隔离、幂等、补偿、API 与 Web 自动化测试。

## 架构

```mermaid
flowchart LR
    U[用户] --> W[Gradio Web]
    W --> A[FastAPI :8002]
    A --> R[(Redis :6379<br/>DB 1 / DB 2)]
    R --> C[Celery Worker]
    C --> G[LangGraph Context Manager]
    G --> S[Supervisor]
    S --> D[Discovery Agent]
    S --> B[Booking Agent]
    S --> CS[Customer Service Agent]
    D -->|结构化 handoff| B
    B -->|订单售后 handoff| CS
    D --> DT[高德 MCP / 沙箱目录]
    B --> BT[报价 / 库存 / 预订]
    CS --> ST[改期 / 取消 / 退款 / 投诉]
    BT --> H[HITL 风险审批]
    ST --> H
    H --> P[Payment Service + Compensation Saga]
    G --> PG[(PostgreSQL :5432<br/>travel_agent)]
```

### 职责边界

| 角色 | 决策职责 | 可见上下文 | 工具权限 |
|---|---|---|---|
| Supervisor | 识别意图、选择角色、低置信度澄清、终止流程 | 摘要、当前输入、工作流和 handoff 结果 | 无业务工具 |
| Discovery Agent | 地点搜索、候选酒店、偏好推荐 | 检索约束、长期偏好、地点产物 | 高德 MCP、沙箱搜索、交接 Booking |
| Booking Agent | 房型、报价、库存、沙箱下单 | 选中酒店、日期、人数、quote/order 引用 | 报价、预订、查单、结构化交接 |
| Customer Service Agent | 查单、改期、取消、退款、投诉 | 当前用户所属订单与售后状态 | 本人订单、售后写工具、结构化交接 |
| Payment Service | 支付、幂等和失败补偿 | 已校验报价、订单、支付状态 | 确定性领域服务，不是自由决策 Agent |

完整设计见 [Multi-Agent 改造说明](docs/MULTI_AGENT_REFACTORING.md)，模块及时序图见 [架构文档](docs/architecture.md)。

## 酒店沙箱

数据库初始化会幂等写入杭州、南昌、上海、北京的演示酒店、房型、库存和取消规则。高德结果可以规范化为带 `external_ref` 的沙箱酒店，并生成明确标记的演示房型。订单受状态机约束：

```text
PAYMENT_PENDING → CONFIRMED → CHANGE_PENDING → CONFIRMED
                            └→ CANCEL_PENDING → CANCELLED / REFUNDED
PAYMENT_PENDING → PAYMENT_FAILED
支付已捕获 + 提交失败 → REFUNDED（自动补偿）
```

非法状态转换、过期报价、库存不足、跨用户订单访问和非 `demo_wallet` 支付都会在领域层被拒绝。

## 快速开始

默认按本地 Windows 服务运行。环境要求：Windows 10/11、Python 3.11、本地 PostgreSQL、本地 Redis，以及至少一个模型提供商的 API Key。高德 MCP 与 LangSmith 均为可选项。

### 1. 安装 Python 依赖

```powershell
Copy-Item .env.example .env
.\scripts\bootstrap.ps1 -PythonPath "D:\Pycharm\movefromC\envs\project3\python.exe" -Dev
```

### 2. 配置本地 PostgreSQL、Redis 与模型

先确认 Windows 中的 PostgreSQL 和 Redis 服务已经启动。在 PostgreSQL 中单独创建 `travel_agent` 数据库；初始化脚本会创建表和演示数据，但不会创建数据库本身：

```sql
CREATE DATABASE travel_agent;
```

可以通过 pgAdmin 执行，也可以使用 `psql`：

```powershell
psql -U postgres -c "CREATE DATABASE travel_agent;"
```

然后按本机账号和密码修改 `.env`。推荐让会话状态和 Celery Broker 使用不同的 Redis 逻辑库，避免与旧项目串任务：

```dotenv
PORT=8002
API_BASE_URL=http://localhost:8002

DB_URI=postgresql://postgres:你的PostgreSQL密码@localhost:5432/travel_agent?sslmode=disable

REDIS_URL=redis://localhost:6379/1
REDIS_KEY_PREFIX=travel-agent-orchestrator
CELERY_BROKER_URL=redis://localhost:6379/2

LLM_TYPE=qwen
DASHSCOPE_API_KEY=your-key
LLM_REQUEST_TIMEOUT_SECONDS=90
LLM_MAX_RETRIES=2
POLL_TIMEOUT_SECONDS=600
MAX_READ_ONLY_TOOL_CALLS_PER_AGENT=8
GRAPH_RECURSION_LIMIT=80
AMAP_MAPS_API_KEY=your-optional-amap-key
APP_TIMEZONE=Asia/Shanghai
WEB_STATE_SECRET=replace-with-a-long-random-local-value
```

`LLM_REQUEST_TIMEOUT_SECONDS` 是单次模型请求的读取超时。复合任务需要多次完成路由、
工具选择和 Agent handoff，建议本地演示保持 `90` 秒；`LLM_MAX_RETRIES=2` 表示临时网络
超时后最多自动重试两次。`POLL_TIMEOUT_SECONDS` 应明显大于单次模型超时；只读工具调用
预算和图递归上限共同防止检索循环。修改这些值后需要同时重启 Web/API 和 Celery Worker
才会生效。

Redis 配置了密码时，连接格式为 `redis://:你的Redis密码@localhost:6379/1`。`user_id`、数据库名、Redis 逻辑库和 key 前缀共同实现本地演示隔离。

### 3. 初始化数据库

```powershell
.\scripts\init-database.ps1 -PythonPath "D:\Pycharm\movefromC\envs\project3\python.exe"
```

该命令会幂等创建 LangGraph checkpoint/store、酒店领域表、执行轨迹表，并写入杭州、南昌、上海和北京的沙箱酒店数据。

### 4. 启动 Web/API 与 Worker

分别打开两个终端，并确保都位于项目根目录：

```powershell
.\scripts\start-web.ps1 -PythonPath "D:\Pycharm\movefromC\envs\project3\python.exe"
```

```powershell
.\scripts\start-worker.ps1 -PythonPath "D:\Pycharm\movefromC\envs\project3\python.exe"
```

访问：

- Web 工作区：<http://localhost:8002/>
- OpenAPI 文档：<http://localhost:8002/docs>

本地模式可以与旧项目共用 PostgreSQL/Redis 服务进程，但应使用独立的 `travel_agent` 数据库、Redis `/1` 与 `/2`、`travel-agent-orchestrator` key 前缀和版本化 checkpoint namespace。FastAPI 使用 `8002`，因此不会与旧项目的 `8001` 冲突。

### 可选：改用 Docker Compose 启动基础设施

如果另一台机器没有安装 PostgreSQL 或 Redis，可以使用仓库中的 Compose 文件。应用本身仍在 Windows Python 中运行；Docker 只负责两个基础设施服务：

```dotenv
DB_URI=postgresql://travel_agent:travel_agent@localhost:5433/travel_agent?sslmode=disable
REDIS_URL=redis://localhost:6380/1
CELERY_BROKER_URL=redis://localhost:6380/2
COMPOSE_POSTGRES_PORT=5433
COMPOSE_REDIS_PORT=6380
```

```powershell
docker compose up -d
.\scripts\init-database.ps1 -PythonPath "D:\Pycharm\movefromC\envs\project3\python.exe"
```

本地 Windows 数据库模式不要执行 `docker compose up -d`。

## 关键配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `PORT` | `8002` | Web 与 API 共用端口 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 日期解析、报价校验和取消规则使用的权威时区 |
| `DB_URI` | `...localhost:5432/travel_agent` | 本地独立 PostgreSQL 数据库 |
| `REDIS_URL` | `redis://localhost:6379/1` | 会话与任务状态 |
| `CELERY_BROKER_URL` | `redis://localhost:6379/2` | Celery 消息队列，与会话状态隔离 |
| `REDIS_KEY_PREFIX` | `travel-agent-orchestrator` | 项目级 Redis 隔离 |
| `DEFAULT_CHAT_MODEL` | `qwen3.8-max` | 四个角色的默认旗舰模型，支持 Function Calling |
| `SUPERVISOR_MODEL` | 空 | Supervisor 单独覆盖模型 |
| `DISCOVERY_MODEL` | 空 | Discovery 单独覆盖模型 |
| `BOOKING_MODEL` | 空 | Booking 单独覆盖模型 |
| `SERVICE_MODEL` | 空 | Customer Service 单独覆盖模型 |
| `SUPERVISOR_CONFIDENCE_THRESHOLD` | `0.65` | 低于阈值时澄清 |
| `MAX_HANDOFFS_PER_TASK` | `6` | Agent 交接安全上限 |
| `SANDBOX_PAYMENT_MODE` | `success` | `success` / `decline` / `fail_after_capture` |
| `CONTEXT_COMPRESSION_RATIO` | `0.70` | 触发增量摘要的上下文占比 |
| `CONTEXT_RECENT_TURNS` | `5` | 保留的最近完整对话轮次 |
| `LANGSMITH_TRACING` | `false` | 可选外部追踪；本地轨迹始终可用 |

每次 Supervisor 和 Specialist 推理都会收到服务器生成的当前日期、时间与时区。用户输入“9 月 10 日到 12 日”而省略年份时，后端会先将其规范化为不早于今天的最近日期，并把 ISO 日期写入可信工作流；模型不再根据训练数据猜测当前年份。查询新闻、网页或实时价格才需要搜索引擎，日期判断本身不依赖 Tavily。

完整变量位于 [.env.example](.env.example)。请勿提交 `.env`。

## 演示场景

1. Discovery → Booking：输入“推荐西湖附近适合亲子的酒店，并预订其中一家”，观察 Supervisor 路由、地点检索、结构化 handoff、报价和单张交易审批卡。
2. 售后写操作：先完成沙箱订单，再输入“把这个订单改到下周”，观察 Customer Service 先查单、再申请写操作审批并执行受控状态转换。
3. 补偿事务：设置 `SANDBOX_PAYMENT_MODE=fail_after_capture`，批准预订，观察支付捕获后自动退款、订单进入 `REFUNDED`，轨迹记录补偿结果。

更完整的逐步操作与演示见 [思路记录](https://blog.mirastar.top/2026/08/31/travel-agent-orchestrator-engineering-notes-with-demo/)。

## API

原有 `/agent/*` 与 `/system/info` 契约保持兼容，并增加只读能力：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/agent/trace/{user_id}/{session_id}/{task_id}` | 当前任务脱敏执行轨迹 |
| `GET` | `/travel/orders/{user_id}` | 当前用户沙箱订单 |
| `GET` | `/travel/orders/{user_id}/{order_id}` | 当前用户单个订单 |
| `GET` | `/system/metrics` | 本地路由、成功率、延迟、token 与补偿指标 |
| `GET` | `/evaluation/latest` | 最近一次离线评测摘要，不触发模型调用 |

完整契约见 [API 文档](docs/api.md)。`user_id` 只用于本地演示的数据隔离，并不等同生产身份认证。

## 离线评测与测试

默认评测完全使用提交到仓库的固定场景，不访问 Qwen、高德或 LangSmith：

```powershell
python -m travel_agent_orchestrator.evaluation
```

付费 Judge 默认关闭，只有显式传入 `--judge` 才使用已配置模型：

```powershell
python -m travel_agent_orchestrator.evaluation --judge
```

报告写入被忽略的 `var/evaluations/`。仓库附带一份注明数据来源的 [示例评测报告](docs/EVALUATION_SAMPLE.md)。运行全部质量检查：

```powershell
.\scripts\check.ps1 -PythonPath "D:\Pycharm\movefromC\envs\project3\python.exe"
```

## 项目结构

```text
travel-agent-orchestrator/
├─ src/travel_agent_orchestrator/
│  ├─ agent/             # LangGraph、路由、HITL、上下文和工具产物
│  ├─ travel/            # 酒店、报价、订单、支付、退款和轨迹领域
│  ├─ api/               # FastAPI 工厂与兼容/新增路由
│  ├─ application/       # 会话、任务、订单与指标用例
│  ├─ infrastructure/    # 配置、模型、Redis、PostgreSQL 和日志
│  ├─ workers/           # Celery 任务入口
│  ├─ web/               # Gradio 工作区、审批卡和执行检查器
│  └─ evaluation/        # 固定数据集与离线评测器
├─ tests/                # 单元、图、API、Web、领域与评测测试
├─ docs/                 # 架构、API、改造说明和示例报告
├─ scripts/              # 安装、初始化、启动、重置和质量检查
├─ compose.yaml          # 可选的 PostgreSQL/Redis 容器方案
└─ pyproject.toml
```


## 已有限制

- 仅覆盖酒店搜索、推荐、报价、沙箱预订与售后，不覆盖机票、火车票和真实支付。
- checkpoint 保留完整原始消息以便审计，摘要只压缩模型输入，数据库仍会随长会话增长。
- 高德地点与沙箱库存是两个数据域；页面会显示来源标签，不能把地点结果宣传为真实可订库存。

