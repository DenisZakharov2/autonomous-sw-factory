"""Per-step usage accounting and mode budget enforcement.

The journal carries no token or price fields, so usage lives beside the run in
`runtime/usage.jsonl`.  Unknown numbers stay `null`: a runtime that cannot read
tokens from its agent must not invent them, and the budget check then skips the
dimension it cannot measure.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..journal import utc_now


@dataclass(frozen=True)
class UsageTotals:
    steps: int
    seconds: float
    tokens: int | None
    usd: float | None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read: int | None = None

    @property
    def billable_tokens(self) -> int | None:
        """Input and output only, without cache reads.

        `total_tokens` from a CLI counts a cached prefix again on every API
        call, so it is the conservative number the budget uses.  `billable`
        is the provider-invoice number and is reported separately; replacing
        one with the other silently would make the budget look bigger.
        """
        if self.tokens_in is None or self.tokens_out is None:
            return None
        return self.tokens_in + self.tokens_out

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "seconds": round(self.seconds, 3),
            "minutes": round(self.seconds / 60.0, 4),
            "tokens": self.tokens,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read": self.cache_read,
            "tokens_billable": self.billable_tokens,
            "usd": self.usd,
        }


class UsageLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        entry = {"timestamp_utc": utc_now(), **record}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return entry

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def next_step(self) -> int:
        """A number that is unique for the whole run, not for one loop call.

        `factory run` is invoked again after every human gate; a per-call
        counter used to restart at 1, so the ledger could not say which step
        of the run a 74k-token call belonged to.
        """
        return len(self.records()) + 1

    def totals(self) -> UsageTotals:
        rows = self.records()
        seconds = sum(float(row.get("seconds") or 0.0) for row in rows)
        tokens: int | None = 0
        usd: float | None = 0.0
        tokens_in: int | None = 0
        tokens_out: int | None = 0
        cache_read: int | None = 0
        for row in rows:
            value = row.get("tokens_total")
            if value is None:
                tokens = None
            elif tokens is not None:
                tokens += int(value)
            cost = row.get("usd")
            if cost is None:
                usd = None
            elif usd is not None:
                usd += float(cost)
            for name, target in (
                ("tokens_in", "in"),
                ("tokens_out", "out"),
                ("cache_read", "cache"),
            ):
                raw = row.get(name)
                if raw is None:
                    if target == "in":
                        tokens_in = None
                    elif target == "out":
                        tokens_out = None
                    else:
                        cache_read = None
                else:
                    if target == "in" and tokens_in is not None:
                        tokens_in += int(raw)
                    elif target == "out" and tokens_out is not None:
                        tokens_out += int(raw)
                    elif target == "cache" and cache_read is not None:
                        cache_read += int(raw)
        return UsageTotals(
            steps=len(rows),
            seconds=seconds,
            tokens=tokens,
            usd=usd,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read=cache_read,
        )

    def budget_violation(
        self, budget: dict[str, Any] | None, enforce: dict[str, Any] | None = None
    ) -> str | None:
        if not budget:
            return None
        enforce = enforce or {}
        totals = self.totals()
        minutes_limit = budget.get("minutes")
        if enforce.get("minutes", True) and minutes_limit is not None:
            used = totals.seconds / 60.0
            if used > float(minutes_limit):
                return (
                    f"minute budget exceeded: {used:.2f} min > {float(minutes_limit):.2f} min"
                )
        token_limit = budget.get("tokens")
        if enforce.get("tokens", True) and token_limit is not None and totals.tokens is not None:
            if totals.tokens > int(token_limit):
                return f"token budget exceeded: {totals.tokens} > {int(token_limit)}"
        usd_limit = budget.get("usd")
        if enforce.get("usd", True) and usd_limit is not None and totals.usd is not None:
            if totals.usd > float(usd_limit):
                return f"usd budget exceeded: {totals.usd:.2f} > {float(usd_limit):.2f}"
        return None
