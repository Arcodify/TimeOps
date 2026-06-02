#!/usr/bin/env python3
"""
Recover attendance data from a Discord activity log channel.

The bot posts structured embeds for:
- Clocked In
- Clocked Out
- Break Started
- Break Ended

This script reads those embeds back out of Discord history and writes CSV
exports that can be shared even if the SQLite database is gone.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import discord


CLOCK_IN_TITLES = {"🟢 Clocked In"}
CLOCK_OUT_TITLES = {"🔴 Clocked Out", "⏰ Auto Clocked Out"}
BREAK_START_TITLES = {
    "☕ Break Started",
    "☕ Automatic Break Started",
    "☕ Off-Schedule Break Started",
}
BREAK_END_TITLES = {"✅ Break Ended"}


def _parse_utc_timestamp(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().strip("`")
    text = text.removesuffix(" UTC").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _fmt_utc(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _minutes_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[int]:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() // 60))


def _field_map(embed: discord.Embed) -> dict[str, str]:
    return {field.name: field.value for field in embed.fields}


def _extract_user_id(value: str | None) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"<@!?(\d+)>", value)
    if match:
        return match.group(1)
    return None


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip()
    return "" if text == "—" else text


def _safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip())
    return label.strip("_") or "report"


def _parse_duration_to_minutes(value: str | None) -> Optional[int]:
    if not value:
        return None
    text = value.strip().strip("`")
    match = re.fullmatch(r"(?:(\d+)h\s*)?(\d+)?m?", text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return hours * 60 + minutes


@dataclass
class BreakRecord:
    break_type: str
    reason: str = ""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    source_title: str = ""
    source_message_id: str = ""

    @property
    def duration_minutes(self) -> Optional[int]:
        if self.start and self.end:
            return _minutes_between(self.start, self.end)
        return None


@dataclass
class SessionRecord:
    guild_id: str
    user_id: str
    username: str
    clock_in: datetime
    note: str = ""
    clock_out: Optional[datetime] = None
    auto_out: bool = False
    early_clock_out: bool = False
    early_clock_out_reason: str = ""
    scheduled_clock_out_time: str = ""
    source_message_id: str = ""
    breaks: list[BreakRecord] = field(default_factory=list)

    @property
    def gross_minutes(self) -> Optional[int]:
        return _minutes_between(self.clock_in, self.clock_out)

    @property
    def break_minutes(self) -> int:
        total = 0
        for item in self.breaks:
            if item.duration_minutes is not None:
                total += item.duration_minutes
        return total

    @property
    def net_minutes(self) -> Optional[int]:
        gross = self.gross_minutes
        if gross is None:
            return None
        return max(0, gross - self.break_minutes)

    @property
    def status(self) -> str:
        if self.clock_out is None:
            return "In Progress"
        if self.auto_out:
            return "Auto Clock-Out"
        return "Completed"


class RecoveryState:
    def __init__(self, guild_id: str):
        self.guild_id = guild_id
        self.sessions_by_key: dict[tuple[str, str], SessionRecord] = {}
        self.current_session_by_user: dict[str, SessionRecord] = {}
        self.active_break_by_user: dict[str, BreakRecord] = {}
        self.sessions: dict[tuple[str, str], SessionRecord] = {}
        self.orphan_breaks: list[BreakRecord] = []

    def _session_key(self, user_id: str, clock_in: datetime) -> tuple[str, str]:
        return user_id, clock_in.isoformat()

    def get_or_create_session(
        self,
        user_id: str,
        username: str,
        clock_in: datetime,
        source_message_id: str = "",
    ) -> SessionRecord:
        key = self._session_key(user_id, clock_in)
        session = self.sessions.get(key)
        if session is None:
            session = SessionRecord(
                guild_id=self.guild_id,
                user_id=user_id,
                username=username,
                clock_in=clock_in,
                source_message_id=source_message_id,
            )
            self.sessions[key] = session
        else:
            session.username = username or session.username
            if source_message_id:
                session.source_message_id = source_message_id
        return session

    def start_session(
        self,
        user_id: str,
        username: str,
        clock_in: datetime,
        note: str = "",
        source_message_id: str = "",
    ) -> None:
        session = self.get_or_create_session(user_id, username, clock_in, source_message_id)
        session.note = note or session.note
        self.current_session_by_user[user_id] = session

    def end_session(
        self,
        user_id: str,
        username: str,
        clock_in: datetime,
        clock_out: datetime,
        auto_out: bool = False,
        early_clock_out: bool = False,
        early_clock_out_reason: str = "",
        scheduled_clock_out_time: str = "",
        source_message_id: str = "",
    ) -> SessionRecord:
        session = self.get_or_create_session(user_id, username, clock_in, source_message_id)
        session.clock_out = clock_out
        session.auto_out = auto_out
        session.early_clock_out = early_clock_out
        session.early_clock_out_reason = early_clock_out_reason or session.early_clock_out_reason
        session.scheduled_clock_out_time = scheduled_clock_out_time or session.scheduled_clock_out_time
        if source_message_id:
            session.source_message_id = source_message_id

        active_break = self.active_break_by_user.get(user_id)
        if active_break and active_break.start and active_break.end is None:
            active_break.end = clock_out

        self.current_session_by_user.pop(user_id, None)
        self.active_break_by_user.pop(user_id, None)
        return session

    def start_break(
        self,
        user_id: str,
        break_type: str,
        start: datetime,
        reason: str = "",
        source_title: str = "",
        source_message_id: str = "",
    ) -> BreakRecord:
        record = BreakRecord(
            break_type=break_type,
            reason=reason,
            start=start,
            source_title=source_title,
            source_message_id=source_message_id,
        )
        session = self.current_session_by_user.get(user_id)
        if session is not None:
            session.breaks.append(record)
        else:
            self.orphan_breaks.append(record)
        self.active_break_by_user[user_id] = record
        return record

    def end_break(
        self,
        user_id: str,
        end: datetime,
        duration_minutes: Optional[int] = None,
    ) -> Optional[BreakRecord]:
        record = self.active_break_by_user.get(user_id)
        if record is None:
            return None
        if record.start is None and duration_minutes is not None:
            record.start = end - timedelta(minutes=duration_minutes)
        record.end = end
        self.active_break_by_user.pop(user_id, None)
        return record

    def finalize(self) -> list[SessionRecord]:
        sessions = list(self.sessions.values())
        sessions.sort(key=lambda item: (item.clock_in, item.username.lower(), item.user_id))
        return sessions


async def _resolve_username(guild: discord.Guild, user_id: str, fallback: str = "") -> str:
    member = guild.get_member(int(user_id))
    if member is not None:
        return member.display_name
    try:
        member = await guild.fetch_member(int(user_id))
        return member.display_name
    except Exception:
        return fallback or f"User {user_id}"


async def _parse_clock_in_embed(
    embed: discord.Embed,
    message: discord.Message,
    state: RecoveryState,
    guild: discord.Guild,
) -> None:
    fields = _field_map(embed)
    user_id = _extract_user_id(fields.get("Employee"))
    started = _parse_utc_timestamp(fields.get("Started"))
    if user_id is None or started is None:
        return
    username = await _resolve_username(guild, user_id, _clean_text(fields.get("Employee")))
    note = _clean_text(fields.get("Note"))
    state.start_session(
        user_id=user_id,
        username=username,
        clock_in=started,
        note=note,
        source_message_id=str(message.id),
    )


async def _parse_clock_out_embed(
    embed: discord.Embed,
    message: discord.Message,
    state: RecoveryState,
    guild: discord.Guild,
) -> None:
    fields = _field_map(embed)
    user_id = _extract_user_id(fields.get("Employee"))
    clock_in = _parse_utc_timestamp(fields.get("Clock In"))
    clock_out = _parse_utc_timestamp(fields.get("Clock Out")) or message.created_at.astimezone(timezone.utc)
    if user_id is None or clock_in is None or clock_out is None:
        return
    username = await _resolve_username(guild, user_id, _clean_text(fields.get("Employee")))
    auto_out = embed.title == "⏰ Auto Clocked Out"
    early_reason = _clean_text(fields.get("Early Clock-Out Reason"))
    scheduled_clock_out_time = _clean_text(fields.get("Scheduled Clock-Out"))
    session = state.end_session(
        user_id=user_id,
        username=username,
        clock_in=clock_in,
        clock_out=clock_out,
        auto_out=auto_out,
        early_clock_out=bool(early_reason),
        early_clock_out_reason=early_reason,
        scheduled_clock_out_time=scheduled_clock_out_time,
        source_message_id=str(message.id),
    )

    # If the clock-out log includes a duration but no break-end log was seen,
    # we still leave the break record closed at the session end.
    if session.clock_out and user_id in state.active_break_by_user:
        state.active_break_by_user[user_id].end = session.clock_out


async def _parse_break_start_embed(
    embed: discord.Embed,
    message: discord.Message,
    state: RecoveryState,
    guild: discord.Guild,
) -> None:
    fields = _field_map(embed)
    user_id = _extract_user_id(fields.get("Employee"))
    started = _parse_utc_timestamp(fields.get("Started")) or message.created_at.astimezone(timezone.utc)
    if user_id is None or started is None:
        return

    if embed.title == "☕ Automatic Break Started":
        break_type = "scheduled"
        reason = _clean_text(fields.get("Break"))
    elif embed.title == "☕ Off-Schedule Break Started":
        break_type = "break"
        reason = _clean_text(fields.get("Reason"))
    else:
        break_type = _clean_text(fields.get("Break")).lower() or "break"
        reason = ""

    username = await _resolve_username(guild, user_id, _clean_text(fields.get("Employee")))
    session = state.current_session_by_user.get(user_id)
    if session is None:
        # Try to attach the break to a session that is already open in the
        # same chronological window.
        session = state.sessions.get((user_id, started.isoformat()))
    if session is not None:
        state.current_session_by_user[user_id] = session

    state.start_break(
        user_id=user_id,
        break_type=break_type,
        start=started,
        reason=reason,
        source_title=embed.title,
        source_message_id=str(message.id),
    )


def _parse_break_end_embed(embed: discord.Embed, message: discord.Message, state: RecoveryState) -> None:
    fields = _field_map(embed)
    user_id = _extract_user_id(fields.get("Employee"))
    if user_id is None:
        return
    ended = message.created_at.astimezone(timezone.utc)
    duration = _parse_duration_to_minutes(fields.get("Duration"))
    record = state.end_break(user_id=user_id, end=ended, duration_minutes=duration)
    if record is None and duration is not None:
        # Orphaned end event. Keep a minimal record so the CSV can still show it.
        state.orphan_breaks.append(
            BreakRecord(
                break_type=_clean_text(fields.get("Break")) or "break",
                reason="",
                start=ended - timedelta(minutes=duration),
                end=ended,
                source_title=embed.title,
                source_message_id=str(message.id),
            )
        )


async def _resolve_channel(guild: discord.Guild, channel_id: Optional[int], channel_name: Optional[str]):
    if channel_id is not None:
        channel = guild.get_channel_or_thread(channel_id) if hasattr(guild, "get_channel_or_thread") else guild.get_channel(channel_id)
        if channel is None:
            channel = await guild.fetch_channel(channel_id)
        return channel

    if channel_name:
        for channel in guild.text_channels:
            if channel.name == channel_name:
                return channel
        for thread in getattr(guild, "threads", []):
            if thread.name == channel_name:
                return thread
    return None


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _session_rows(sessions: list[SessionRecord]) -> list[dict]:
    rows = []
    for session in sessions:
        rows.append(
            {
                "Date": session.clock_in.strftime("%Y-%m-%d"),
                "Employee": session.username,
                "User ID": session.user_id,
                "Clock In": _fmt_utc(session.clock_in),
                "Clock Out": _fmt_utc(session.clock_out),
                "Status": session.status,
                "Gross Minutes": session.gross_minutes if session.gross_minutes is not None else "",
                "Break Minutes": session.break_minutes,
                "Net Minutes": session.net_minutes if session.net_minutes is not None else "",
                "Auto Clock-Out": "Yes" if session.auto_out else "No",
                "Early Clock-Out": "Yes" if session.early_clock_out else "No",
                "Early Clock-Out Reason": session.early_clock_out_reason,
                "Scheduled Clock-Out Time": session.scheduled_clock_out_time,
                "Note": session.note,
                "Source Message ID": session.source_message_id,
            }
        )
    return rows


def _break_rows(sessions: list[SessionRecord], orphan_breaks: list[BreakRecord]) -> list[dict]:
    rows = []
    for session in sessions:
        for br in session.breaks:
            rows.append(
                {
                    "Employee": session.username,
                    "User ID": session.user_id,
                    "Session Clock In": _fmt_utc(session.clock_in),
                    "Break Type": br.break_type,
                    "Reason": br.reason,
                    "Break Start": _fmt_utc(br.start),
                    "Break End": _fmt_utc(br.end),
                    "Duration Minutes": br.duration_minutes if br.duration_minutes is not None else "",
                    "Source Message ID": br.source_message_id,
                }
            )
    for br in orphan_breaks:
        rows.append(
            {
                "Employee": "",
                "User ID": "",
                "Session Clock In": "",
                "Break Type": br.break_type,
                "Reason": br.reason,
                "Break Start": _fmt_utc(br.start),
                "Break End": _fmt_utc(br.end),
                "Duration Minutes": br.duration_minutes if br.duration_minutes is not None else "",
                "Source Message ID": br.source_message_id,
            }
        )
    rows.sort(key=lambda row: (row["Break Start"], row["Employee"], row["User ID"]))
    return rows


async def recover_logs(args) -> int:
    token = args.token or os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is not set and --token was not provided.")

    guild_id = args.guild_id or os.getenv("DISCORD_GUILD_ID")
    if not guild_id:
        raise SystemExit("Guild ID is missing. Set DISCORD_GUILD_ID or pass --guild-id.")

    channel_id = args.channel_id or os.getenv("DISCORD_ACTIVITY_LOG_CHANNEL_ID") or os.getenv("DISCORD_LEAVE_CHANNEL_ID")
    channel_name = args.channel_name or os.getenv("DISCORD_ACTIVITY_LOG_CHANNEL_NAME")
    if channel_id is None and not channel_name:
        raise SystemExit(
            "Activity log channel is missing. Set DISCORD_ACTIVITY_LOG_CHANNEL_ID "
            "(or DISCORD_LEAVE_CHANNEL_ID) or pass --channel-id/--channel-name."
        )
    if channel_id is not None and channel_name:
        raise SystemExit("Use either a channel ID or channel name, not both.")

    intents = discord.Intents.default()
    intents.guilds = True
    client = discord.Client(intents=intents)

    ready = asyncio.Event()
    result = {"ok": False}

    @client.event
    async def on_ready():
        try:
            print("Connected to Discord. Resolving guild and channel...", flush=True)
            guild = client.get_guild(int(guild_id)) or await client.fetch_guild(int(guild_id))
            print(f"Resolved guild: {guild.name} ({guild.id})", flush=True)
            channel = await _resolve_channel(guild, int(channel_id) if channel_id is not None else None, channel_name)
            if channel is None:
                raise RuntimeError("Could not resolve the activity log channel.")
            print(f"Resolved channel: {channel.name} ({channel.id})", flush=True)

            state = RecoveryState(str(guild.id))
            since = args.since or datetime.min.replace(tzinfo=timezone.utc)
            until = args.until or datetime.now(timezone.utc)
            print(
                "Scanning history from "
                f"{(since.strftime('%Y-%m-%d %H:%M UTC') if args.since else 'beginning')} "
                f"to {until.strftime('%Y-%m-%d %H:%M UTC')}...",
                flush=True,
            )

            scanned = 0
            matched = 0
            history_kwargs = {"limit": None, "oldest_first": True}
            if args.since is not None:
                history_kwargs["after"] = since
            if args.until is not None:
                history_kwargs["before"] = until
            async for message in channel.history(**history_kwargs):
                scanned += 1
                if not message.embeds:
                    continue
                embed = message.embeds[0]
                title = embed.title or ""
                if title in CLOCK_IN_TITLES:
                    matched += 1
                    await _parse_clock_in_embed(embed, message, state, guild)
                elif title in CLOCK_OUT_TITLES:
                    matched += 1
                    await _parse_clock_out_embed(embed, message, state, guild)
                elif title in BREAK_START_TITLES:
                    matched += 1
                    await _parse_break_start_embed(embed, message, state, guild)
                elif title in BREAK_END_TITLES:
                    matched += 1
                    _parse_break_end_embed(embed, message, state)

            sessions = state.finalize()
            session_rows = _session_rows(sessions)
            break_rows = _break_rows(sessions, state.orphan_breaks)

            out_dir = Path(args.output_dir)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            base = f"{_safe_label(guild.name)}_{guild.id}_{_safe_label(channel.name)}_{ts}"
            sessions_path = out_dir / f"recovered_timesheet_{base}.csv"
            breaks_path = out_dir / f"recovered_breaks_{base}.csv"

            _write_csv(
                sessions_path,
                session_rows,
                [
                    "Date",
                    "Employee",
                    "User ID",
                    "Clock In",
                    "Clock Out",
                    "Status",
                    "Gross Minutes",
                    "Break Minutes",
                    "Net Minutes",
                    "Auto Clock-Out",
                    "Early Clock-Out",
                    "Early Clock-Out Reason",
                    "Scheduled Clock-Out Time",
                    "Note",
                    "Source Message ID",
                ],
            )
            _write_csv(
                breaks_path,
                break_rows,
                [
                    "Employee",
                    "User ID",
                    "Session Clock In",
                    "Break Type",
                    "Reason",
                    "Break Start",
                    "Break End",
                    "Duration Minutes",
                    "Source Message ID",
                ],
            )

            print(f"Scanned {scanned} messages, matched {matched} activity log entries.")
            print(f"Wrote {len(session_rows)} session rows to: {sessions_path}")
            print(f"Wrote {len(break_rows)} break rows to: {breaks_path}")
            result["ok"] = True
        except Exception:
            traceback.print_exc()
        finally:
            ready.set()
            await client.close()

    await client.start(token)
    await ready.wait()
    return 0 if result["ok"] else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover HR attendance data from a Discord activity log channel."
    )
    parser.add_argument("--guild-id", type=int, help="Discord guild/server ID.")
    parser.add_argument("--channel-id", type=int, help="Activity log channel ID.")
    parser.add_argument("--channel-name", help="Activity log channel name if you do not know the ID.")
    parser.add_argument("--since", help="Only scan messages after this UTC date/time (YYYY-MM-DD or ISO-8601).")
    parser.add_argument("--until", help="Only scan messages before this UTC date/time (YYYY-MM-DD or ISO-8601).")
    parser.add_argument("--output-dir", default="exports", help="Directory for recovered CSV files.")
    parser.add_argument("--token", help="Discord bot token. Defaults to DISCORD_TOKEN.")
    args = parser.parse_args()

    def parse_boundary(value: str | None) -> Optional[datetime]:
        if value is None:
            return None
        raw = value.strip()
        if len(raw) == 10:
            raw = raw + "T00:00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise SystemExit(f"Invalid datetime: {value!r}. Use YYYY-MM-DD or ISO-8601.") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    args.since = parse_boundary(args.since)
    args.until = parse_boundary(args.until)
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(recover_logs(_parse_args())))
    except KeyboardInterrupt:
        raise SystemExit(130)
