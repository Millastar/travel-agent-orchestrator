from datetime import UTC, date, datetime

from travel_agent_orchestrator.infrastructure.clock import resolve_date_range, runtime_clock


def test_runtime_clock_uses_configured_timezone_and_exposes_authoritative_date() -> None:
    clock = runtime_clock("Asia/Shanghai", now=datetime(2026, 8, 30, 16, 30, tzinfo=UTC))

    assert clock.today == date(2026, 8, 31)
    assert "当前日期：2026-08-31" in clock.as_system_prompt()
    assert "Asia/Shanghai" in clock.as_system_prompt()


def test_yearless_date_range_resolves_to_nearest_future_occurrence() -> None:
    resolved = resolve_date_range("9 月 10 日到 12 日的双人房", today=date(2026, 8, 31))

    assert resolved is not None
    assert resolved.check_in == date(2026, 9, 10)
    assert resolved.check_out == date(2026, 9, 12)
    assert resolved.inferred_year is True


def test_yearless_date_range_rolls_into_next_year() -> None:
    resolved = resolve_date_range("预订 1月2号至1月4号", today=date(2026, 12, 31))

    assert resolved is not None
    assert resolved.check_in == date(2027, 1, 2)
    assert resolved.check_out == date(2027, 1, 4)


def test_explicit_past_year_is_preserved_for_domain_validation() -> None:
    resolved = resolve_date_range("2025年9月10日到12日", today=date(2026, 8, 31))

    assert resolved is not None
    assert resolved.check_in == date(2025, 9, 10)
    assert resolved.inferred_year is False
