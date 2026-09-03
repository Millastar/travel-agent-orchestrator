from pathlib import Path

CSS_PATH = (
    Path(__file__).parents[1] / "src" / "travel_agent_orchestrator" / "web" / "assets" / "theme.css"
)


def test_hitl_card_stays_visible_inside_the_fixed_workspace() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")

    assert "#chatbot {\n  flex: 1 1 0 !important;" in css
    assert "height: auto !important;" in css
    assert "#hitl-card {\n  display: flex !important;" in css
    assert "flex: 0 1 auto !important;" in css
    assert "max-height: min(34dvh, 380px);" in css
    assert "#hitl-content {\n  flex: 1 1 auto !important;" in css
    assert "overflow-y: auto;" in css
    assert "flex-wrap: nowrap !important;" in css
    assert "#hitl-actions {\n  flex: 0 0 auto !important;" in css


def test_history_and_chat_roles_use_visible_workspace_patterns() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")

    assert '#chatbot .message.user::before {\n  content: "YOU";' in css
    assert '#chatbot .message.bot::before {\n  content: "TRAVEL AGENT";' in css
    assert "#session-picker label:has(input:checked)" in css


def test_chat_controls_and_hitl_card_keep_safe_spacing_and_visual_hierarchy() -> None:
    css = CSS_PATH.read_text(encoding="utf-8")

    assert "min-width: min(420px, 70vw) !important;" in css
    assert "width: calc(100% - 24px) !important;" in css
    assert '.icon-button-wrapper.top-panel:has(button[aria-label*="清空"])' in css
    assert "#hitl-card > .styler" in css
    assert "#argument-table {" in css
    assert "#argument-table .header-row {" in css
    assert "#hitl-feedback-input {" in css
    for button_id in ("hitl-approve", "hitl-reject", "hitl-edit", "hitl-feedback"):
        assert f"#{button_id} {{" in css
