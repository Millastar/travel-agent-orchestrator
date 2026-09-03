from travel_agent_orchestrator.observability import redact


def test_trace_redaction_masks_secret_fields_and_inline_credentials() -> None:
    result = redact(
        {
            "api_key": "never-store-this",
            "nested": {
                "message": (
                    "authorization=Bearer-secret token:abc123 "
                    "sk-example1234567890 card 6222021234567890"
                )
            },
        }
    )

    assert result["api_key"] == "[redacted]"
    message = result["nested"]["message"]
    assert "Bearer-secret" not in message
    assert "abc123" not in message
    assert "sk-example" not in message
    assert "6222021234567890" not in message
