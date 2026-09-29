"""Per-conversation state: LLM history, preferences, last offered slots."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

# Preferences outlive a single session (single-user demo; a DB would go here in production).
GLOBAL_PREFERENCES: dict[str, str] = {"usual_meeting_minutes": "30"}


@dataclass
class Session:
    tz: ZoneInfo
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    history: list[Any] = field(default_factory=list)  # provider-native messages
    preferences: dict[str, str] = field(default_factory=lambda: dict(GLOBAL_PREFERENCES))
    last_offered_slots: list[dict] = field(default_factory=list)
    booked: list[dict] = field(default_factory=list)
    snapshot: str = ""  # calendar snapshot text injected into the prompt (see prompts.build_calendar_snapshot)
    snapshot_at: float = 0.0  # time.time() when it was fetched; 0 = stale

    def copy(self) -> Session:
        return Session(
            tz=self.tz,
            id=self.id,
            history=list(self.history),
            preferences=dict(self.preferences),
            last_offered_slots=list(self.last_offered_slots),
            booked=list(self.booked),
            snapshot=self.snapshot,
            snapshot_at=self.snapshot_at,
        )

    def remember(self, key: str, value: str) -> None:
        self.preferences[key] = value
        GLOBAL_PREFERENCES[key] = value
