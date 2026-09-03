"""Authoritative runtime clock and deterministic travel-date normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class RuntimeClock:
    timezone: str
    current_datetime: datetime

    @property
    def today(self) -> date:
        return self.current_datetime.date()

    def as_system_prompt(self) -> str:
        """Return a compact, authoritative temporal instruction for an Agent."""
        return (
            "权威运行时日期与时区（由服务器提供，不得依据模型训练时间猜测）：\n"
            f"- 时区：{self.timezone}\n"
            f"- 当前日期：{self.today.isoformat()}\n"
            f"- 当前时间：{self.current_datetime.isoformat(timespec='seconds')}\n"
            "日期规则：用户明确写出年份时保留该年份；用户省略年份时，将入住日期"
            "解析为不早于当前日期的最近一次该月日，离店日期必须晚于入住日期。"
            "如果仍有多种合理解释，先向用户澄清，不得编造年份。"
        )


@dataclass(frozen=True)
class ResolvedDateRange:
    check_in: date
    check_out: date
    inferred_year: bool


_DATE_RANGE_PATTERN = re.compile(
    r"(?:(?P<year_in>\d{4})\s*年\s*)?"
    r"(?P<month_in>1[0-2]|0?[1-9])\s*月\s*"
    r"(?P<day_in>3[01]|[12]\d|0?[1-9])\s*[日号]?\s*"
    r"(?:到|至|—|–|-|~|～)\s*"
    r"(?:(?P<year_out>\d{4})\s*年\s*)?"
    r"(?:(?P<month_out>1[0-2]|0?[1-9])\s*月\s*)?"
    r"(?P<day_out>3[01]|[12]\d|0?[1-9])\s*[日号]?"
)


def runtime_clock(timezone_name: str, *, now: datetime | None = None) -> RuntimeClock:
    """Read the current time once and convert it to the configured application timezone."""
    zone = ZoneInfo(timezone_name)
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return RuntimeClock(timezone_name, reference.astimezone(zone))


def current_date(timezone_name: str) -> date:
    """Return the domain's authoritative local date."""
    return runtime_clock(timezone_name).today


def _next_occurrence(month: int, day: int, today: date) -> date | None:
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


def resolve_date_range(text: str, *, today: date) -> ResolvedDateRange | None:
    """Normalize common Chinese date ranges without asking an LLM to invent the year."""
    match = _DATE_RANGE_PATTERN.search(text)
    if match is None:
        return None
    values = {key: int(value) if value else None for key, value in match.groupdict().items()}
    inferred_year = values["year_in"] is None
    if values["year_in"] is not None:
        try:
            check_in = date(values["year_in"], values["month_in"], values["day_in"])
        except ValueError:
            return None
    else:
        check_in = _next_occurrence(values["month_in"], values["day_in"], today)
        if check_in is None:
            return None

    checkout_month = values["month_out"] or values["month_in"]
    checkout_year = values["year_out"] or check_in.year
    try:
        check_out = date(checkout_year, checkout_month, values["day_out"])
    except ValueError:
        return None
    if values["year_out"] is None and values["month_out"] is not None and check_out <= check_in:
        try:
            check_out = date(checkout_year + 1, checkout_month, values["day_out"])
        except ValueError:
            return None
    if check_out <= check_in:
        return None
    return ResolvedDateRange(check_in, check_out, inferred_year)
