"""Google Calendar API v3 client (OAuth2 user credentials)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .base import BusyPeriod, Event

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendar:
    def __init__(self, creds: Credentials, calendar_id: str = "primary"):
        self.creds = creds
        self.calendar_id = calendar_id
        # cache_discovery=False avoids a noisy warning about the file cache in server envs.
        self.service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    @classmethod
    def from_token(
        cls, token_json: str = "", token_file: str = "", calendar_id: str = "primary"
    ) -> GoogleCalendar | None:
        """Load authorized-user credentials from a JSON string or file; None if unavailable."""
        raw = token_json.strip()
        if not raw and token_file and Path(token_file).exists():
            raw = Path(token_file).read_text()
        if not raw:
            return None
        return cls.from_json(raw, calendar_id)

    @classmethod
    def from_json(cls, raw: str, calendar_id: str = "primary") -> GoogleCalendar:
        """Build a client from authorized-user JSON (the token.json written by scripts/authorize_google.py).

        Raises ValueError on anything that is not a usable token, so an upload can be rejected cleanly."""
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("not valid JSON") from exc
        missing = [
            k for k in ("client_id", "client_secret", "refresh_token") if not isinstance(info, dict) or not info.get(k)
        ]
        if missing:
            raise ValueError(
                f"token is missing {', '.join(missing)}; upload the token.json from scripts/authorize_google.py"
            )
        creds = Credentials.from_authorized_user_info(info, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return cls(creds, calendar_id)

    def probe(self) -> dict:
        """Cheap call for validating the token; returns the calendar's identity for the UI."""
        cal = self.service.calendars().get(calendarId=self.calendar_id).execute()
        return {
            "id": cal.get("id", self.calendar_id),
            "summary": cal.get("summary", ""),
            "timezone": cal.get("timeZone", ""),
        }

    def busy_periods(self, start: datetime, end: datetime) -> list[BusyPeriod]:
        # events.list (rather than freebusy) so conflicts can be explained by title.
        return [
            BusyPeriod(e.start, e.end, e.title)
            for e in self._list(start, end)
            if not e.description.startswith("__all_day__")
        ]

    def search_events(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        return self._list(start, end, query)

    def create_event(self, title: str, start: datetime, end: datetime, description: str = "") -> Event:
        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
            "conferenceData": {
                "createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}
            },
        }
        try:
            created = (
                self.service.events().insert(calendarId=self.calendar_id, body=body, conferenceDataVersion=1).execute()
            )
        except Exception as exc:  # e.g. Meet not allowed on this calendar -> retry without it
            log.warning("insert with Meet failed (%s); retrying without conference", exc)
            body.pop("conferenceData")
            created = self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
        return self._to_event(created)

    def _list(self, start: datetime, end: datetime, query: str | None = None) -> list[Event]:
        items: list[dict] = []
        page_token = None
        while True:
            resp = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=start.astimezone(UTC).isoformat(),
                    timeMax=end.astimezone(UTC).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    q=query or None,
                    maxResults=250,
                    pageToken=page_token,
                )
                .execute()
            )
            items.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        events = []
        for item in items:
            if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
                continue  # declined/free-time events don't block
            ev = self._to_event(item)
            if ev is not None:
                events.append(ev)
        return events

    @staticmethod
    def _to_event(item: dict) -> Event | None:
        start, end = item.get("start", {}), item.get("end", {})
        if "dateTime" not in start:
            # All-day event: keep it searchable but mark so it doesn't block slots.
            try:
                s = datetime.fromisoformat(start["date"]).replace(tzinfo=UTC)
                e = datetime.fromisoformat(end["date"]).replace(tzinfo=UTC)
            except (KeyError, ValueError):
                return None
            return Event(
                item["id"],
                item.get("summary", "(no title)"),
                s,
                e,
                "__all_day__ " + item.get("description", ""),
                item.get("htmlLink", ""),
            )
        return Event(
            id=item["id"],
            title=item.get("summary", "(no title)"),
            start=datetime.fromisoformat(start["dateTime"]),
            end=datetime.fromisoformat(end["dateTime"]),
            description=item.get("description", "") or "",
            link=item.get("hangoutLink") or item.get("htmlLink", ""),
        )
