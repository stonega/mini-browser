"""Bounded, session-only agent activity without request or response contents."""

from collections import deque
from dataclasses import dataclass
from time import time

from .agent_tools import TOOLS

METHODS = frozenset(tool["name"] for tool in TOOLS)


@dataclass
class AgentAction:
    method: str
    started: float
    page_id: int | None
    status: str = "Running"


class AgentActivity:
    def __init__(self, changed):
        self.records = deque(maxlen=100)
        self.changed = changed

    def start(self, method, page_id=None):
        record = AgentAction(
            method if method in METHODS else "unknown_command",
            time(),
            page_id if type(page_id) is int and 0 < page_id <= 2**31 - 1 else None,
        )
        self.records.appendleft(record)
        self.changed()
        return record

    def finish(self, record, status):
        if record.status == "Running":
            record.status = status
            self.changed()
