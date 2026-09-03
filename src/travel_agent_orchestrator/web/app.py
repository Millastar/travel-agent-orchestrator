"""Gradio Blocks application mounted by the FastAPI service."""

from __future__ import annotations

import html
import json
import time
import uuid
from pathlib import Path
from typing import Any

import gradio as gr

from travel_agent_orchestrator.domain.models import DEFAULT_SYSTEM_MESSAGE
from travel_agent_orchestrator.infrastructure.config import Settings
from travel_agent_orchestrator.web.client import AgentApiClient, ApiClientError
from travel_agent_orchestrator.web.presentation import (
    INTERRUPT_PROMPT,
    SessionView,
    arguments_to_rows,
    extract_agent_label,
    extract_answer,
    extract_error,
    extract_interrupt,
    format_interrupt_task_state,
    parse_argument_rows,
    parse_json_arguments,
    polling_timed_out,
    status_label,
)
from travel_agent_orchestrator.web.service import (
    WorkspaceService,
    new_workspace_state,
    state_from_session,
)

ASSET_DIR = Path(__file__).resolve().parent / "assets"
ACTIVE_STATUSES = {"idle", "pending", "running"}

THEME_BOOTSTRAP_JS = """
() => {
  const saved = localStorage.getItem("travel-agent-theme") || "dark";
  document.body.dataset.raTheme = saved;
  document.body.classList.toggle("dark", saved === "dark");
  document.documentElement.classList.toggle("dark", saved === "dark");
  document.documentElement.style.colorScheme = saved;
}
"""

THEME_TOGGLE_JS = """
() => {
  const current = document.body.dataset.raTheme || "dark";
  const next = current === "dark" ? "light" : "dark";
  document.body.dataset.raTheme = next;
  document.body.classList.toggle("dark", next === "dark");
  document.documentElement.classList.toggle("dark", next === "dark");
  document.documentElement.style.colorScheme = next;
  localStorage.setItem("travel-agent-theme", next);
}
"""


def _client(settings: Settings) -> AgentApiClient:
    return AgentApiClient(settings.api_base_url, settings.http_timeout_seconds)


def _session_choices(sessions: list[SessionView]) -> list[tuple[str, str]]:
    choices: list[tuple[str, str]] = []
    for session in sessions:
        label = session.choice_label if session.tasks else "新会话"
        choices.append((label, session.session_id))
    return choices


def _identity_html(user_id: str, session_id: str) -> str:
    return (
        '<div class="ra-section-label">当前工作区</div>'
        f'<div style="font-weight:680;color:var(--ra-text)">{html.escape(user_id)}</div>'
        f'<div class="ra-muted" style="font-size:11px;margin-top:4px">'
        f"{html.escape(session_id[:8])}…</div>"
    )


def _capabilities_html(settings: Settings, *, online: bool) -> str:
    model_label = (
        settings.ollama_chat_model if settings.llm_type == "ollama" else settings.default_chat_model
    )
    api_label = "API 与存储在线" if online else "API 状态待确认"
    api_class = "online" if online else ""
    amap_label = "高德 MCP 已配置" if settings.amap_maps_api_key else "高德 MCP 未配置"
    return f"""
    <div class="ra-capabilities">
      <span class="ra-chip"><span class="ra-dot {api_class}"></span>{api_label}</span>
      <span class="ra-chip">{html.escape(model_label)}</span>
      <span class="ra-chip">{amap_label}</span>
      <span class="ra-chip">沙箱交易</span>
    </div>
    """


def _activity_html(status: str, detail: str | None = None) -> str:
    normalized = (
        status
        if status
        in {
            "pending",
            "running",
            "interrupted",
            "completed",
            "error",
            "failed",
            "not_found",
        }
        else "idle"
    )
    suffix = f'<span class="ra-muted"> · {html.escape(detail)}</span>' if detail else ""
    return (
        f'<span class="ra-status-chip {normalized}"><span class="ra-dot"></span>'
        f"{status_label(normalized)}</span>{suffix}"
    )


def _system_summary(info: dict[str, Any], user_id: str) -> str:
    count = int(info.get("sessions_count") or 0)
    return (
        f"**系统概览**\n\n- 当前用户：`{user_id}`\n"
        f"- 活跃会话：**{count}**\n- API 文档：[`/docs`](/docs)"
    )


def _trace_html(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<div class="ta-empty">执行任务后，这里会显示路由、handoff、工具与审批轨迹。</div>'
    rows = []
    for event in events[-30:]:
        event_type = html.escape(str(event.get("event_type") or "event"))
        actor = html.escape(str(event.get("actor") or "system").replace("_", " ").upper())
        status = html.escape(str(event.get("status") or "success"))
        duration = event.get("duration_ms")
        token_usage = event.get("token_usage")
        details = []
        if isinstance(duration, int | float):
            details.append(f"{duration:.0f} ms")
        if isinstance(token_usage, int):
            details.append(f"{token_usage} tokens")
        rows.append(
            '<div class="ta-trace-event">'
            f'<span class="ta-trace-dot {status}"></span>'
            '<div class="ta-trace-body">'
            f'<div class="ta-trace-type">{event_type}</div>'
            f'<div class="ta-trace-meta">{actor}'
            + (f" · {' · '.join(details)}" if details else "")
            + "</div></div></div>"
        )
    return '<div class="ta-trace-list">' + "".join(rows) + "</div>"


def _orders_markdown(orders: list[dict[str, Any]]) -> str:
    if not orders:
        return "尚无沙箱订单。完成一次预订后可在这里查看状态。"
    sections = ["所有订单均为 **沙箱交易**，不会产生真实预订或扣款。"]
    for order in orders[:12]:
        sections.append(
            f"### {order.get('hotel_name', '酒店')}\n"
            f"`{order.get('order_id', '-')}` · **{order.get('status', '-')}**\n\n"
            f"{order.get('check_in', '-')} → {order.get('check_out', '-')} · "
            f"{order.get('room_type', '-')} · ¥{order.get('total_amount', 0)}"
        )
    return "\n\n---\n\n".join(sections)


def _metrics_markdown(metrics: dict[str, Any]) -> str:
    compensation = metrics.get("compensation_results") or {}
    routes = metrics.get("route_distribution") or {}
    route_text = "、".join(f"{key}: {value}" for key, value in routes.items()) or "暂无"
    return (
        f"- 任务数：**{metrics.get('tasks', 0)}**\n"
        f"- 成功率：**{float(metrics.get('success_rate') or 0):.2%}**\n"
        f"- 错误事件：**{metrics.get('errors', 0)}**\n"
        f"- 累计 token：**{metrics.get('total_tokens', 0)}**\n"
        f"- 平均事件耗时：**{float(metrics.get('avg_duration_ms') or 0):.0f} ms**\n"
        f"- 路由分布：{route_text}\n"
        f"- 自动补偿：**{compensation.get('succeeded', 0)} / "
        f"{compensation.get('attempted', 0)}**"
    )


def _evaluation_markdown(report: dict[str, Any]) -> str:
    if not report.get("available"):
        return "尚未运行评测。执行 `python -m travel_agent_orchestrator.evaluation` 后刷新。"
    deterministic = report.get("deterministic_metrics") or {}
    return (
        f"- 场景数：**{report.get('scenario_count', 0)}**\n"
        f"- 路由准确率：**{float(report.get('route_accuracy') or 0):.2%}**\n"
        f"- Handoff 第一跳：**{float(report.get('handoff_first_hop_accuracy') or 0):.2%}**\n"
        f"- 工具选择：**{float(deterministic.get('tool_selection_accuracy') or 0):.2%}**\n"
        f"- 参数一致性：**{float(deterministic.get('parameter_consistency') or 0):.2%}**\n"
        f"- 写操作审批覆盖：**{float(deterministic.get('write_approval_coverage') or 0):.2%}**\n"
        f"- 数据来源：`{report.get('dataset', 'unknown')}`\n\n"
        "> 该结果来自提交到仓库的离线测试样例，不代表生产流量。"
    )


def _interrupt_values(payload: dict[str, Any]) -> tuple[str, str, list[list[str]], str]:
    name, description, arguments = extract_interrupt(payload)
    response = payload.get("last_response") or payload.get("result") or payload
    interrupt_data = response.get("interrupt_data") if isinstance(response, dict) else {}
    interrupt_data = interrupt_data if isinstance(interrupt_data, dict) else {}
    role = str(interrupt_data.get("agent_role") or "specialist").replace("_", " ").upper()
    risk = str(interrupt_data.get("risk_level") or "transaction").replace("_", " ").upper()
    heading = f"### {role} · 工具审批 · `{name}`"
    code = json.dumps(arguments, ensure_ascii=False, indent=2)
    task_state = format_interrupt_task_state(payload)
    risk_notice = f"**风险级别：{risk}** · 所有交易均为沙箱模拟。"
    base = f"{risk_notice}\n\n{description}"
    detail = f"{base}\n\n---\n\n{task_state}" if task_state else base
    return heading, detail, arguments_to_rows(arguments), code


def _append_failure(state: dict[str, Any], message: str) -> None:
    state["messages"] = [
        *state.get("messages", []),
        {
            "role": "assistant",
            "content": f"⚠️ {message}",
            "metadata": {"title": "请求未完成"},
        },
    ]


def _task_is_active(status: str, task_id: Any) -> bool:
    return bool(task_id and status in ACTIVE_STATUSES)


def _validate_user_id(value: str | None) -> str:
    user_id = (value or "").strip()
    if not user_id:
        raise ValueError("请输入用户名称后继续。")
    if len(user_id) > 64:
        raise ValueError("用户名称不能超过 64 个字符。")
    if any(ord(character) < 32 for character in user_id):
        raise ValueError("用户名称不能包含控制字符。")
    return user_id


def build_web_demo(settings: Settings) -> gr.Blocks:
    """Build the browser workspace without starting a separate web server."""

    theme = gr.themes.Soft(
        primary_hue="indigo",
        neutral_hue="slate",
        radius_size="md",
        font=["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
    )

    with gr.Blocks(
        title="Travel Agent Orchestrator",
        theme=theme,
        css_paths=ASSET_DIR / "theme.css",
        js=THEME_BOOTSTRAP_JS,
        analytics_enabled=False,
        fill_width=True,
        fill_height=True,
    ) as demo:
        browser_user = gr.BrowserState(
            "",
            storage_key="travel-agent-orchestrator-user",
            secret=settings.web_state_secret,
        )
        workspace_state = gr.State(new_workspace_state(""))
        settings_open = gr.State(False)
        poll_timer = gr.Timer(value=settings.poll_interval_seconds, active=False)
        inspector_timer = gr.Timer(value=2.0, active=False)

        with gr.Column(elem_id="ra-app"):
            with gr.Column(elem_id="identity-gate") as identity_gate:
                gr.HTML(
                    '<div class="ra-brand-mark">TA</div>'
                    '<h1 class="ra-gate-title">欢迎来到旅行智能体工作区</h1>'
                    '<p class="ra-gate-copy">输入演示用户名，继续使用多会话、'
                    "工具调用和人工审批能力。</p>"
                )
                login_user = gr.Textbox(
                    label="演示用户名",
                    placeholder="例如：mirastar",
                    max_length=64,
                    autofocus=True,
                )
                login_button = gr.Button("进入工作区", variant="primary")
                login_notice = gr.Markdown()

            with gr.Column(visible=False) as workspace:
                with gr.Row(elem_id="workspace-header"):
                    gr.HTML(
                        '<div class="ra-header-brand">'
                        '<span class="ra-brand-mark">TA</span>'
                        '<div><p class="ra-header-title">Travel Agent Orchestrator</p>'
                        '<p class="ra-header-subtitle">Supervisor · Specialists · HITL · Saga</p>'
                        "</div></div>",
                        elem_id="header-brand",
                    )
                    capabilities = gr.HTML(
                        _capabilities_html(settings, online=False),
                        elem_id="header-capabilities",
                    )
                    theme_button = gr.Button(
                        "◐",
                        size="sm",
                        min_width=36,
                        scale=0,
                        elem_id="theme-button",
                    )
                    settings_button = gr.Button(
                        "检查器",
                        size="sm",
                        min_width=64,
                        scale=0,
                        elem_id="settings-button",
                    )

                with gr.Row(elem_id="workspace-shell", equal_height=True):
                    with gr.Column(scale=0, min_width=260, elem_id="session-sidebar"):
                        identity_card = gr.HTML(elem_id="identity-card")
                        new_session_button = gr.Button(
                            "＋ 新建会话",
                            variant="primary",
                            size="sm",
                            elem_id="new-session-button",
                        )
                        gr.HTML(
                            '<div class="ra-section-label">历史会话</div>',
                            elem_id="history-label",
                        )
                        session_picker = gr.Radio(
                            choices=[],
                            label="历史会话",
                            show_label=False,
                            interactive=True,
                            container=False,
                            elem_id="session-picker",
                        )
                        with gr.Row(elem_id="session-actions"):
                            refresh_button = gr.Button("刷新状态", size="sm")
                            delete_button = gr.Button(
                                "删除会话", size="sm", variant="stop", interactive=False
                            )
                        with gr.Group(visible=False, elem_id="delete-confirmation") as delete_group:
                            delete_notice = gr.Markdown("确定删除当前会话吗？此操作无法撤销。")
                            with gr.Row():
                                cancel_delete = gr.Button("取消", size="sm")
                                confirm_delete = gr.Button("确认删除", size="sm", variant="stop")

                    with gr.Column(scale=4, min_width=430, elem_id="chat-surface"):
                        chatbot = gr.Chatbot(
                            type="messages",
                            label="对话",
                            show_label=False,
                            elem_id="chatbot",
                            height="calc(100vh - 285px)",
                            min_height=360,
                            layout="bubble",
                            show_copy_button=True,
                            feedback_options=None,
                            allow_tags=False,
                            placeholder=(
                                "<div style='text-align:center;padding:72px 18px'>"
                                "<div style='font-size:28px;margin-bottom:10px'>✦</div>"
                                "<strong>今天想让智能体帮你做什么？</strong><br>"
                                "<span style='opacity:.62'>可以查询地点、规划任务，"
                                "或体验工具审批流程。</span>"
                                "</div>"
                            ),
                        )
                        activity = gr.HTML(_activity_html("idle"), elem_id="activity-line")

                        with gr.Group(visible=False, elem_id="hitl-card") as hitl_card:
                            with gr.Column(elem_id="hitl-content"):
                                tool_heading = gr.Markdown("### 工具审批")
                                tool_description = gr.Markdown()
                                with gr.Tabs():
                                    with gr.Tab("字段修改"):
                                        argument_table = gr.Dataframe(
                                            headers=["参数", "值"],
                                            datatype=["str", "str"],
                                            type="array",
                                            interactive=True,
                                            row_count=(1, "dynamic"),
                                            col_count=(2, "fixed"),
                                            show_row_numbers=False,
                                            show_fullscreen_button=False,
                                            label="调用参数",
                                            elem_id="argument-table",
                                        )
                                    with gr.Tab("高级 JSON"):
                                        json_editor = gr.Code(
                                            language="json",
                                            label="完整工具参数",
                                            interactive=True,
                                            lines=8,
                                        )
                                        apply_json_button = gr.Button(
                                            "应用 JSON 修改", variant="primary"
                                        )
                                feedback_box = gr.Textbox(
                                    label="给智能体的反馈",
                                    placeholder="例如：请换一种不需要预订的方案",
                                    lines=2,
                                    elem_id="hitl-feedback-input",
                                )
                            with gr.Row(elem_id="hitl-actions"):
                                approve_button = gr.Button(
                                    "批准执行",
                                    variant="primary",
                                    elem_id="hitl-approve",
                                )
                                reject_button = gr.Button(
                                    "拒绝调用",
                                    variant="stop",
                                    elem_id="hitl-reject",
                                )
                                apply_fields_button = gr.Button(
                                    "应用字段修改",
                                    elem_id="hitl-edit",
                                )
                                feedback_button = gr.Button(
                                    "发送反馈",
                                    elem_id="hitl-feedback",
                                )

                        with gr.Row(elem_id="composer-row"):
                            composer = gr.Textbox(
                                label="消息",
                                show_label=False,
                                placeholder="输入问题，Enter 发送，Shift + Enter 换行",
                                lines=1,
                                max_lines=7,
                                container=False,
                                scale=1,
                                elem_id="composer",
                            )
                            send_button = gr.Button(
                                "↑",
                                variant="primary",
                                size="sm",
                                min_width=40,
                                scale=0,
                                elem_id="send-button",
                            )

                    with (
                        gr.Column(
                            scale=1,
                            min_width=290,
                            visible=False,
                            elem_id="settings-panel",
                        ) as settings_panel,
                        gr.Tabs(elem_id="inspector-tabs"),
                    ):
                        with gr.Tab("执行轨迹"):
                            trace_timeline = gr.HTML(_trace_html([]), elem_id="trace-timeline")
                        with gr.Tab("沙箱订单"):
                            orders_view = gr.Markdown("尚无沙箱订单。")
                        with gr.Tab("指标与评测"):
                            gr.Markdown("### 本地指标")
                            metrics_view = gr.Markdown("等待连接服务…")
                            gr.Markdown("---\n### 最近评测")
                            evaluation_view = gr.Markdown("尚未运行评测。")
                        with gr.Tab("设置"):
                            switch_user = gr.Textbox(
                                label="切换用户", placeholder="输入另一个 user_id"
                            )
                            switch_user_button = gr.Button("切换并加载")
                            gr.Markdown("---\n### 长期记忆")
                            memory_input = gr.Textbox(
                                label="偏好内容",
                                placeholder="例如：优先推荐靠近地铁、支持免费取消的酒店",
                                lines=4,
                            )
                            save_memory_button = gr.Button("保存偏好", variant="primary")
                            settings_notice = gr.Markdown()
                            gr.Markdown("---")
                            system_summary = gr.Markdown("**系统概览**\n\n等待连接服务…")

        identity_outputs = [
            browser_user,
            workspace_state,
            identity_gate,
            workspace,
            login_notice,
            session_picker,
            chatbot,
            identity_card,
            activity,
            composer,
            send_button,
            poll_timer,
            hitl_card,
            tool_heading,
            tool_description,
            argument_table,
            json_editor,
            feedback_box,
            delete_button,
            capabilities,
            system_summary,
        ]

        poll_outputs = [
            workspace_state,
            chatbot,
            activity,
            hitl_card,
            tool_heading,
            tool_description,
            argument_table,
            json_editor,
            feedback_box,
            composer,
            send_button,
            poll_timer,
            session_picker,
            delete_button,
        ]

        session_outputs = [
            workspace_state,
            chatbot,
            activity,
            hitl_card,
            tool_heading,
            tool_description,
            argument_table,
            json_editor,
            feedback_box,
            composer,
            send_button,
            poll_timer,
            delete_button,
            identity_card,
        ]

        def initialize_user(raw_user_id: str | None):
            try:
                user_id = _validate_user_id(raw_user_id)
                with _client(settings) as client:
                    info = client.get_system_info()
                    service = WorkspaceService(client)
                    current = service.initialize(user_id)
                    sessions = service.list_sessions(user_id, include_session_id=current.session_id)
                state = state_from_session(user_id, current)
                polling = _task_is_active(state["status"], state["task_id"])
                if polling:
                    state["poll_started"] = time.time()
                latest_payload = current.tasks[-1].payload if current.tasks else None
                interrupted = state["status"] == "interrupted" and latest_payload is not None
                if interrupted:
                    heading, description, rows, code = _interrupt_values(latest_payload)
                else:
                    heading, description, rows, code = "### 工具审批", "", [], "{}"
                is_active = polling or interrupted
                return (
                    user_id,
                    state,
                    gr.Column(visible=False),
                    gr.Column(visible=True),
                    "",
                    gr.Radio(choices=_session_choices(sessions), value=current.session_id),
                    state["messages"],
                    _identity_html(user_id, current.session_id),
                    _activity_html(state["status"]),
                    gr.Textbox(interactive=not is_active),
                    gr.Button(interactive=not is_active),
                    gr.Timer(active=polling),
                    gr.Group(visible=interrupted),
                    heading,
                    description,
                    rows,
                    code,
                    "",
                    gr.Button(interactive=state["persisted"]),
                    _capabilities_html(settings, online=True),
                    _system_summary(info, user_id),
                )
            except (ValueError, ApiClientError) as exc:
                return (
                    raw_user_id or "",
                    new_workspace_state(""),
                    gr.Column(visible=True),
                    gr.Column(visible=False),
                    f"⚠️ {exc}",
                    gr.Radio(choices=[], value=None),
                    [],
                    "",
                    _activity_html("error"),
                    gr.Textbox(interactive=False),
                    gr.Button(interactive=False),
                    gr.Timer(active=False),
                    gr.Group(visible=False),
                    "### 工具审批",
                    "",
                    [],
                    "{}",
                    "",
                    gr.Button(interactive=False),
                    _capabilities_html(settings, online=False),
                    "**系统概览**\n\n服务暂不可用。",
                )

        def load_session(selected_session: str | None, state: dict[str, Any]):
            if not selected_session:
                return tuple(gr.skip() for _ in session_outputs)
            user_id = str(state.get("user_id") or "")
            try:
                with _client(settings) as client:
                    session = WorkspaceService(client).load_session(user_id, selected_session)
                next_state = state_from_session(user_id, session)
                latest = session.tasks[-1] if session.tasks else None
                interrupted = bool(latest and latest.status == "interrupted")
                if interrupted and latest:
                    heading, description, rows, code = _interrupt_values(latest.payload)
                else:
                    heading, description, rows, code = "### 工具审批", "", [], "{}"
                if latest and latest.status in ACTIVE_STATUSES:
                    next_state["poll_started"] = time.time()
                locked = bool(latest and (latest.status in ACTIVE_STATUSES or interrupted))
                return (
                    next_state,
                    next_state["messages"],
                    _activity_html(next_state["status"]),
                    gr.Group(visible=interrupted),
                    heading,
                    description,
                    rows,
                    code,
                    "",
                    gr.Textbox(interactive=not locked),
                    gr.Button(interactive=not locked),
                    gr.Timer(active=bool(latest and latest.status in ACTIVE_STATUSES)),
                    gr.Button(interactive=next_state["persisted"]),
                    _identity_html(user_id, selected_session),
                )
            except ApiClientError as exc:
                _append_failure(state, str(exc))
                return (
                    state,
                    state["messages"],
                    _activity_html("error", str(exc)),
                    gr.Group(visible=False),
                    "### 工具审批",
                    "",
                    [],
                    "{}",
                    "",
                    gr.Textbox(interactive=True),
                    gr.Button(interactive=True),
                    gr.Timer(active=False),
                    gr.Button(interactive=bool(state.get("persisted"))),
                    _identity_html(user_id, str(state.get("session_id") or "")),
                )

        def submit_query(query: str | None, state: dict[str, Any]):
            value = (query or "").strip()
            if not value:
                raise gr.Error("请输入问题后再发送。")
            if (
                _task_is_active(str(state.get("status")), state.get("task_id"))
                or state.get("status") == "interrupted"
            ):
                raise gr.Error("当前任务仍在处理中，请等待完成或先处理工具审批。")

            task_id = str(uuid.uuid4())
            state = dict(state)
            state["task_id"] = task_id
            state["status"] = "pending"
            state["poll_started"] = time.time()
            state["persisted"] = True
            state["messages"] = [*state.get("messages", []), {"role": "user", "content": value}]
            try:
                with _client(settings) as client:
                    client.invoke(
                        str(state["user_id"]),
                        str(state["session_id"]),
                        task_id,
                        value,
                        DEFAULT_SYSTEM_MESSAGE,
                    )
            except ApiClientError as exc:
                state["status"] = "error"
                _append_failure(state, str(exc))
                return (
                    state,
                    state["messages"],
                    _activity_html("error", str(exc)),
                    gr.Group(visible=False),
                    "### 工具审批",
                    "",
                    [],
                    "{}",
                    "",
                    gr.Textbox(value="", interactive=True),
                    gr.Button(interactive=True),
                    gr.Timer(active=False),
                    gr.Radio(value=state["session_id"]),
                    gr.Button(interactive=False),
                )
            return (
                state,
                state["messages"],
                _activity_html("pending", "请求已提交，正在等待 Worker"),
                gr.Group(visible=False),
                "### 工具审批",
                "",
                [],
                "{}",
                "",
                gr.Textbox(value="", interactive=False),
                gr.Button(interactive=False),
                gr.Timer(active=True),
                gr.Radio(value=state["session_id"]),
                gr.Button(interactive=True),
            )

        def poll_status(state: dict[str, Any]):
            task_id = state.get("task_id")
            if not task_id:
                return tuple(gr.skip() for _ in poll_outputs)
            try:
                with _client(settings) as client:
                    payload = client.get_status(
                        str(state["user_id"]), str(state["session_id"]), str(task_id)
                    )
                status = str(payload.get("status") or "error")
                state = dict(state)
                state["status"] = status

                if status in ACTIVE_STATUSES:
                    started = float(state.get("poll_started") or time.time())
                    if polling_timed_out(started, settings.poll_timeout_seconds):
                        return (
                            state,
                            state["messages"],
                            _activity_html(
                                "pending", "等待超时，任务可能仍在后台运行，可点击“刷新状态”"
                            ),
                            gr.Group(visible=False),
                            gr.skip(),
                            gr.skip(),
                            gr.skip(),
                            gr.skip(),
                            gr.skip(),
                            gr.Textbox(interactive=True),
                            gr.Button(interactive=True),
                            gr.Timer(active=False),
                            gr.skip(),
                            gr.Button(interactive=True),
                        )
                    return (
                        state,
                        state["messages"],
                        _activity_html(status),
                        gr.Group(visible=False),
                        gr.skip(),
                        gr.skip(),
                        gr.skip(),
                        gr.skip(),
                        gr.skip(),
                        gr.Textbox(interactive=False),
                        gr.Button(interactive=False),
                        gr.Timer(active=True),
                        gr.skip(),
                        gr.Button(interactive=True),
                    )

                if status == "interrupted":
                    heading, description, rows, code = _interrupt_values(payload)
                    messages = list(state.get("messages") or [])
                    if not messages or messages[-1].get("content") != INTERRUPT_PROMPT:
                        messages.append({"role": "assistant", "content": INTERRUPT_PROMPT})
                    state["messages"] = messages
                    return (
                        state,
                        messages,
                        _activity_html("interrupted", "请确认工具及参数后继续"),
                        gr.Group(visible=True),
                        heading,
                        description,
                        rows,
                        code,
                        "",
                        gr.Textbox(interactive=False),
                        gr.Button(interactive=False),
                        gr.Timer(active=False),
                        gr.skip(),
                        gr.Button(interactive=True),
                    )

                displayed = set(state.get("displayed_task_ids") or [])
                if status == "completed" and task_id not in displayed:
                    answer = extract_answer(payload) or "任务已完成，但没有可显示的文本结果。"
                    state["messages"] = [
                        *state.get("messages", []),
                        {
                            "role": "assistant",
                            "content": answer,
                            "metadata": {"title": extract_agent_label(payload)},
                        },
                    ]
                    state["displayed_task_ids"] = [*displayed, task_id]
                elif status in {"error", "failed", "not_found"} and task_id not in displayed:
                    _append_failure(state, extract_error(payload))
                    state["displayed_task_ids"] = [*displayed, task_id]

                try:
                    with _client(settings) as client:
                        sessions = WorkspaceService(client).list_sessions(
                            str(state["user_id"]), include_session_id=str(state["session_id"])
                        )
                    picker_update = gr.Radio(
                        choices=_session_choices(sessions), value=state["session_id"]
                    )
                except ApiClientError:
                    picker_update = gr.skip()
                detail = extract_error(payload) if status in {"error", "failed"} else None
                return (
                    state,
                    state["messages"],
                    _activity_html(status, detail),
                    gr.Group(visible=False),
                    "### 工具审批",
                    "",
                    [],
                    "{}",
                    "",
                    gr.Textbox(interactive=True),
                    gr.Button(interactive=True),
                    gr.Timer(active=False),
                    picker_update,
                    gr.Button(interactive=True),
                )
            except ApiClientError as exc:
                state = dict(state)
                state["status"] = "error"
                _append_failure(state, str(exc))
                return (
                    state,
                    state["messages"],
                    _activity_html("error", str(exc)),
                    gr.Group(visible=False),
                    gr.skip(),
                    gr.skip(),
                    gr.skip(),
                    gr.skip(),
                    gr.skip(),
                    gr.Textbox(interactive=True),
                    gr.Button(interactive=True),
                    gr.Timer(active=False),
                    gr.skip(),
                    gr.Button(interactive=bool(state.get("persisted"))),
                )

        def resume_task(
            decision: str,
            state: dict[str, Any],
            decision_args: dict[str, Any] | None = None,
        ):
            if state.get("status") != "interrupted" or not state.get("task_id"):
                raise gr.Error("当前没有等待审批的任务。")
            try:
                with _client(settings) as client:
                    client.resume(
                        str(state["user_id"]),
                        str(state["session_id"]),
                        str(state["task_id"]),
                        decision,
                        decision_args,
                    )
            except ApiClientError as exc:
                raise gr.Error(str(exc)) from exc
            state = dict(state)
            state["status"] = "pending"
            state["poll_started"] = time.time()
            return (
                state,
                _activity_html("pending", "审批结果已提交"),
                gr.Group(visible=False),
                gr.Textbox(interactive=False),
                gr.Button(interactive=False),
                gr.Timer(active=True),
            )

        def approve(state: dict[str, Any]):
            return resume_task("accept", state)

        def reject(state: dict[str, Any]):
            return resume_task("reject", state)

        def apply_fields(rows: Any, state: dict[str, Any]):
            try:
                arguments = parse_argument_rows(rows)
            except ValueError as exc:
                raise gr.Error(str(exc)) from exc
            return resume_task("edit", state, {"args": arguments})

        def apply_json(value: str | None, state: dict[str, Any]):
            try:
                arguments = parse_json_arguments(value)
            except ValueError as exc:
                raise gr.Error(str(exc)) from exc
            return resume_task("edit", state, {"args": arguments})

        def send_feedback(value: str | None, state: dict[str, Any]):
            feedback = (value or "").strip()
            if not feedback:
                raise gr.Error("请输入反馈内容。")
            return resume_task("response", state, {"args": feedback})

        def create_session(state: dict[str, Any]):
            next_state = new_workspace_state(str(state.get("user_id") or ""))
            try:
                with _client(settings) as client:
                    sessions = WorkspaceService(client).list_sessions(
                        next_state["user_id"], include_session_id=next_state["session_id"]
                    )
                picker = gr.Radio(
                    choices=_session_choices(sessions), value=next_state["session_id"]
                )
            except ApiClientError:
                picker = gr.Radio(
                    choices=[("新会话", next_state["session_id"])],
                    value=next_state["session_id"],
                )
            return (
                next_state,
                [],
                picker,
                _activity_html("idle"),
                gr.Group(visible=False),
                gr.Group(visible=False),
                gr.Textbox(value="", interactive=True),
                gr.Button(interactive=True),
                gr.Timer(active=False),
                gr.Button(interactive=False),
                _identity_html(next_state["user_id"], next_state["session_id"]),
            )

        def ask_delete(state: dict[str, Any]):
            session_id = str(state.get("session_id") or "")
            return (
                gr.Group(visible=True),
                f"确定删除会话 `{session_id[:8]}…` 吗？此操作无法撤销。",
            )

        def delete_current_session(state: dict[str, Any]):
            user_id = str(state.get("user_id") or "")
            session_id = str(state.get("session_id") or "")
            if state.get("persisted"):
                try:
                    with _client(settings) as client:
                        client.delete_session(user_id, session_id)
                except ApiClientError as exc:
                    raise gr.Error(str(exc)) from exc
            next_state = new_workspace_state(user_id)
            try:
                with _client(settings) as client:
                    sessions = WorkspaceService(client).list_sessions(
                        user_id, include_session_id=next_state["session_id"]
                    )
                choices = _session_choices(sessions)
            except ApiClientError:
                choices = [("新会话", next_state["session_id"])]
            return (
                next_state,
                [],
                gr.Radio(choices=choices, value=next_state["session_id"]),
                _activity_html("idle", "会话已删除"),
                gr.Group(visible=False),
                gr.Button(interactive=False),
                gr.Timer(active=False),
                gr.Textbox(value="", interactive=True),
                gr.Button(interactive=True),
                _identity_html(user_id, next_state["session_id"]),
            )

        def save_memory(value: str | None, state: dict[str, Any]):
            memory = (value or "").strip()
            if not memory:
                raise gr.Error("请输入需要长期保存的偏好。")
            try:
                with _client(settings) as client:
                    client.write_memory(str(state.get("user_id") or ""), memory)
            except ApiClientError as exc:
                raise gr.Error(str(exc)) from exc
            return "", "✅ 偏好已保存到长期记忆。"

        def refresh_system(state: dict[str, Any]):
            try:
                with _client(settings) as client:
                    info = client.get_system_info()
                return (
                    _capabilities_html(settings, online=True),
                    _system_summary(info, str(state.get("user_id") or "")),
                )
            except ApiClientError as exc:
                return _capabilities_html(settings, online=False), f"⚠️ {exc}"

        def refresh_inspector(state: dict[str, Any]):
            user_id = str(state.get("user_id") or "")
            if not user_id:
                return _trace_html([]), "尚无沙箱订单。", "等待登录…", "尚未运行评测。"
            try:
                with _client(settings) as client:
                    trace = (
                        client.get_trace(
                            user_id,
                            str(state.get("session_id") or ""),
                            str(state.get("task_id") or ""),
                        )
                        if state.get("task_id")
                        else {"events": []}
                    )
                    orders = client.get_orders(user_id)
                    metrics = client.get_metrics()
                    evaluation = client.get_latest_evaluation()
                return (
                    _trace_html(list(trace.get("events") or [])),
                    _orders_markdown(list(orders.get("orders") or [])),
                    _metrics_markdown(metrics),
                    _evaluation_markdown(evaluation),
                )
            except ApiClientError as exc:
                message = f"⚠️ {exc}"
                return _trace_html([]), message, message, message

        def toggle_settings(opened: bool):
            next_value = not bool(opened)
            return next_value, gr.Column(visible=next_value), gr.Timer(active=next_value)

        demo.load(
            initialize_user,
            inputs=[browser_user],
            outputs=identity_outputs,
            show_progress="hidden",
        )
        login_button.click(
            initialize_user,
            inputs=[login_user],
            outputs=identity_outputs,
            show_progress="minimal",
        )
        login_user.submit(
            initialize_user,
            inputs=[login_user],
            outputs=identity_outputs,
            show_progress="minimal",
        )
        switch_user_button.click(
            initialize_user,
            inputs=[switch_user],
            outputs=identity_outputs,
            show_progress="minimal",
        )
        theme_button.click(fn=None, js=THEME_TOGGLE_JS, queue=False)
        settings_button.click(
            toggle_settings,
            inputs=[settings_open],
            outputs=[settings_open, settings_panel, inspector_timer],
            show_progress="hidden",
        )
        inspector_timer.tick(
            refresh_inspector,
            inputs=[workspace_state],
            outputs=[trace_timeline, orders_view, metrics_view, evaluation_view],
            queue=False,
            show_progress="hidden",
        )

        session_picker.change(
            load_session,
            inputs=[session_picker, workspace_state],
            outputs=session_outputs,
            show_progress="minimal",
        )
        new_session_button.click(
            create_session,
            inputs=[workspace_state],
            outputs=[
                workspace_state,
                chatbot,
                session_picker,
                activity,
                hitl_card,
                delete_group,
                composer,
                send_button,
                poll_timer,
                delete_button,
                identity_card,
            ],
            show_progress="hidden",
        )
        delete_button.click(
            ask_delete,
            inputs=[workspace_state],
            outputs=[delete_group, delete_notice],
            show_progress="hidden",
        )
        cancel_delete.click(
            lambda: gr.Group(visible=False),
            outputs=[delete_group],
            show_progress="hidden",
        )
        confirm_delete.click(
            delete_current_session,
            inputs=[workspace_state],
            outputs=[
                workspace_state,
                chatbot,
                session_picker,
                activity,
                delete_group,
                delete_button,
                poll_timer,
                composer,
                send_button,
                identity_card,
            ],
            show_progress="minimal",
        )

        send_button.click(
            submit_query,
            inputs=[composer, workspace_state],
            outputs=poll_outputs,
            show_progress="hidden",
        )
        composer.submit(
            submit_query,
            inputs=[composer, workspace_state],
            outputs=poll_outputs,
            show_progress="hidden",
        )
        poll_timer.tick(
            poll_status,
            inputs=[workspace_state],
            outputs=poll_outputs,
            queue=False,
            show_progress="hidden",
        )
        refresh_button.click(
            poll_status,
            inputs=[workspace_state],
            outputs=poll_outputs,
            show_progress="hidden",
        )

        resume_outputs = [
            workspace_state,
            activity,
            hitl_card,
            composer,
            send_button,
            poll_timer,
        ]
        approve_button.click(approve, inputs=[workspace_state], outputs=resume_outputs)
        reject_button.click(reject, inputs=[workspace_state], outputs=resume_outputs)
        apply_fields_button.click(
            apply_fields,
            inputs=[argument_table, workspace_state],
            outputs=resume_outputs,
        )
        apply_json_button.click(
            apply_json,
            inputs=[json_editor, workspace_state],
            outputs=resume_outputs,
        )
        feedback_button.click(
            send_feedback,
            inputs=[feedback_box, workspace_state],
            outputs=resume_outputs,
        )

        save_memory_button.click(
            save_memory,
            inputs=[memory_input, workspace_state],
            outputs=[memory_input, settings_notice],
            show_progress="minimal",
        )
        settings_button.click(
            refresh_system,
            inputs=[workspace_state],
            outputs=[capabilities, system_summary],
            show_progress="hidden",
        )

    return demo
