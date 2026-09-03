# API Contract

Web 工作区位于 `http://localhost:8002/`，OpenAPI 文档位于 `http://localhost:8002/docs`。原项目路径、必需字段和任务状态保持兼容；新增字段均为可选扩展。

## 兼容接口

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/agent/invoke` | 创建或复用会话并将任务入队 |
| POST | `/agent/resume` | 恢复等待人工审批的任务 |
| GET | `/system/info` | 会话与活跃用户统计 |
| GET | `/agent/active/sessionid/{user_id}` | 最近更新会话 |
| GET | `/agent/sessionids/{user_id}` | 用户会话列表 |
| GET | `/agent/tasks/{user_id}/{session_id}` | 会话任务及状态 |
| GET | `/agent/status/{user_id}/{session_id}/{task_id}` | 任务状态与结果 |
| POST | `/agent/write/longterm` | 保存用户长期偏好 |
| DELETE | `/agent/session/{user_id}/{session_id}` | 删除会话、checkpoint 与产物 |
| DELETE | `/agent/task/{user_id}/{session_id}/{task_id}` | 删除单个任务状态 |

## 新增只读接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/agent/trace/{user_id}/{session_id}/{task_id}` | 脱敏路由、Agent、工具、审批和耗时事件 |
| GET | `/travel/orders/{user_id}` | 当前用户沙箱订单列表 |
| GET | `/travel/orders/{user_id}/{order_id}` | 当前用户沙箱订单详情 |
| GET | `/system/metrics` | 本地任务、失败、延迟、token 和路由分布 |
| GET | `/evaluation/latest` | 最近离线报告；不会触发付费评测 |

## 请求示例

### 创建任务

```json
{
  "user_id": "demo-user",
  "session_id": "6cb36c4e-...",
  "task_id": "7eaef44f-...",
  "query": "推荐西湖附近的酒店并帮我预订",
  "system_message": "你会使用工具帮助用户，并遵守沙箱交易与审批规则。"
}
```

### 恢复审批

`response_type` 保持 `accept`、`reject`、`edit` 或 `response`。

```json
{
  "user_id": "demo-user",
  "session_id": "6cb36c4e-...",
  "task_id": "7eaef44f-...",
  "response_type": "edit",
  "args": {
    "args": {
      "quote_id": "quo-...",
      "payment_method": "demo_wallet"
    }
  }
}
```

等待审批时，`interrupt_data` 保留 `action_request` 与 `description`，并可增加：

```json
{
  "agent_role": "booking",
  "risk_level": "transaction",
  "workflow_state": "quoted",
  "trace_id": "trace-...",
  "task_state": {
    "objective": "预订西湖云栖酒店",
    "plan": [
      {"step": "理解请求", "status": "completed"},
      {"step": "执行工具 confirm_hotel_booking", "status": "waiting_approval"}
    ],
    "constraints": {
      "quote_id": "quo-...",
      "payment_method": "demo_wallet"
    }
  },
  "artifact_ref": null
}
```

## 状态值

| 状态 | 含义 |
|---|---|
| `not_found` | 任务不存在或已过期 |
| `idle` | 会话存在且当前空闲 |
| `pending` | 等待 Worker |
| `running` | Worker 正在执行 |
| `interrupted` | 等待人工审批 |
| `completed` | 任务完成，包括用户明确拒绝后的安全终止 |
| `error` | 执行失败 |

## 安全说明

`user_id` 是本地演示隔离键，而不是生产身份认证。订单仓储的所有读写都必须同时带当前 `user_id`；即使猜到其他订单 ID，也会返回“不存在或不属于当前用户”。生产部署必须由认证网关注入可信用户身份，不能接受客户端自由填写的 `user_id`。
