"""Single provider-neutral budget authority for native Executor invocations."""

from __future__ import annotations

from dataclasses import dataclass


NATIVE_RESPONSE_BUDGET_MINUTES = 180
NATIVE_PROCESS_WATCHDOG_SECONDS = 185 * 60


@dataclass(frozen=True)
class NativeExecutionPolicy:
    """Distinct response and process budgets with an independent mutation grant."""

    authorizes_mutation: bool
    response_budget_minutes: int = NATIVE_RESPONSE_BUDGET_MINUTES
    process_watchdog_seconds: int = NATIVE_PROCESS_WATCHDOG_SECONDS


def resolve_native_execution_policy(
    *, authorizes_mutation: bool
) -> NativeExecutionPolicy:
    """Resolve shared budgets without deriving or expanding execution authority."""

    return NativeExecutionPolicy(authorizes_mutation=authorizes_mutation)
