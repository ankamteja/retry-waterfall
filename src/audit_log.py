"""
Structured, append-only audit trail. One JSON line per decision -- not a
chat transcript. This is what "compliant escalation" and "stopping rules"
have to be checkable against after the fact.
"""

import json
from dataclasses import asdict, dataclass


@dataclass
class AuditRecord:
    payment_id: str
    policy: str
    decline_code: str
    attempt_number: int
    window_hours: int | None
    channel: str | None
    amount_inr: float
    expected_net_inr: float | None
    rationale: str
    recovered: bool | None          # None = attempt skipped, never contacted
    stopping_rule: str | None        # e.g. "npci_retry_cap_exhausted", "hard_decline_no_retry"


class AuditLog:
    def __init__(self, path: str, append: bool = False):
        """append=False by default: these files are regenerated build
        artifacts, and appending across runs silently stacks multiple runs
        into one trail (which once produced an impossible "12 permitted
        retries" against a 4-attempt regulatory cap). Real deployments
        would pass append=True and rotate."""
        self._path = path
        self._file = open(path, "a" if append else "w", encoding="utf-8")

    def write(self, record: AuditRecord) -> None:
        self._file.write(json.dumps(asdict(record)) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
