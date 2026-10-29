"""Fail-closed guard for broker CLI paths superseded by Plan B."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import date
from typing import NoReturn


LEGACY_BROKER_WRITE_EXIT = 20
BLOCKED_LEGACY_EXECUTE = "BLOCKED_LEGACY_EXECUTE"
_ALLOWED_PERIODS = frozenset({"1m", "5m", "15m", "30m", "1h", "day", "week", "month", "year"})
_EXACT_READ_COMMANDS = frozenset({("auth", "status"), ("assets",), ("positions",), ("portfolio",), ("market-status",), ("order",), ("order", "--history")})
_SYMBOL_RE = re.compile(r"(?:\.[A-Z0-9]+|[A-Z0-9]+(?:[.-][A-Z0-9]+)*)\.(?:US|HK|SG|SH|SZ|HAS)\Z", re.ASCII)
_ORDER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)


class LegacyBrokerWriteBlocked(RuntimeError):
    """Raised before any external side effect on a superseded path."""

    exit_code = LEGACY_BROKER_WRITE_EXIT


def block_legacy_broker_write(operation: str) -> NoReturn:
    raise LegacyBrokerWriteBlocked(f"{BLOCKED_LEGACY_EXECUTE}: {operation}")


def _reject_read_command(operation: str) -> NoReturn:
    block_legacy_broker_write(f"{operation}: command is not an approved read-only shape")


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def _validated_tokens(tokens: Sequence[str], operation: str) -> tuple[str, ...]:
    if isinstance(tokens, (str, bytes, bytearray)) or not isinstance(tokens, Sequence) or not tokens or len(tokens) > 21:
        _reject_read_command(operation)
    result: list[str] = []
    for token in tokens:
        if not isinstance(token, str) or not token or len(token) > 256 or _contains_control(token):
            _reject_read_command(operation)
        result.append(token)
    return tuple(result)


def _valid_symbol(value: str) -> bool:
    return len(value) <= 40 and _SYMBOL_RE.fullmatch(value) is not None


def _valid_order_id(value: str) -> bool:
    return _ORDER_ID_RE.fullmatch(value) is not None


def _canonical_date(value: str) -> date | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def assert_legacy_cli_readonly(tokens: Sequence[str], operation: str) -> None:
    command = _validated_tokens(tokens, operation)
    if command in _EXACT_READ_COMMANDS:
        return
    if len(command) == 3 and command[:2] == ("order", "detail") and _valid_order_id(command[2]):
        return
    if 2 <= len(command) <= 21 and command[0] == "quote" and all(_valid_symbol(symbol) for symbol in command[1:]):
        return
    if (
        len(command) == 9
        and command[:2] == ("kline", "history")
        and _valid_symbol(command[2])
        and command[3] == "--start"
        and command[5] == "--end"
        and command[7] == "--period"
        and command[8] in _ALLOWED_PERIODS
    ):
        start = _canonical_date(command[4])
        end = _canonical_date(command[6])
        if start is not None and end is not None and start <= end:
            return
    _reject_read_command(operation)


def _is_execute_token(token: object) -> bool:
    if not isinstance(token, str):
        return False
    return token.partition("=")[0] in {"--e", "--ex", "--exec", "--execute", "--execut"}


def reject_execute_argv(argv: Sequence[str]) -> int | None:
    """Old entry-point UX only; this is not the low-level security boundary."""
    if any(_is_execute_token(token) for token in argv):
        print(BLOCKED_LEGACY_EXECUTE)
        return LEGACY_BROKER_WRITE_EXIT
    return None


def block_execute_tokens(tokens: Sequence[str], operation: str) -> None:
    """Compatibility import whose security semantics are the strict allowlist."""
    assert_legacy_cli_readonly(tokens, operation)
