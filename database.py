"""
Database layer — SQLite via aiosqlite
All tables: time_entries, leave_requests, standup_schedules, overtime_config
"""

import aiosqlite
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import os

log = logging.getLogger("Database")
DB_PATH = "data/hrbot.db"

WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def parse_blocked_weekdays(value) -> List[int]:
    if value is None:
        return [5]

    if isinstance(value, (list, tuple, set)):
        tokens = [str(v) for v in value]
    else:
        tokens = re.split(r"[,\s]+", str(value).strip())

    days: List[int] = []
    for token in tokens:
        if not token:
            continue
        raw = token.strip().lower()
        if raw.isdigit():
            idx = int(raw)
        else:
            idx = WEEKDAY_ALIASES.get(raw)
        if idx is None or idx < 0 or idx > 6:
            continue
        if idx not in days:
            days.append(idx)

    return days or [5]


def normalize_blocked_weekdays(value) -> str:
    return ",".join(str(day) for day in parse_blocked_weekdays(value))


class Database:
    def __init__(self):
        self.path = DB_PATH

    async def init(self):
        os.makedirs("data", exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS time_entries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    TEXT NOT NULL,
                    user_id     TEXT NOT NULL,
                    username    TEXT NOT NULL,
                    clock_in    TEXT NOT NULL,
                    clock_out   TEXT,
                    duration_minutes INTEGER,
                    auto_out    INTEGER DEFAULT 0,
                    note        TEXT
                );

                CREATE TABLE IF NOT EXISTS leave_requests (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    TEXT NOT NULL,
                    user_id     TEXT NOT NULL,
                    username    TEXT NOT NULL,
                    leave_type  TEXT NOT NULL,
                    start_date  TEXT NOT NULL,
                    end_date    TEXT NOT NULL,
                    reason      TEXT,
                    status      TEXT DEFAULT 'pending',
                    approver_id TEXT,
                    approver_name TEXT,
                    created_at  TEXT NOT NULL,
                    message_id  TEXT
                );

                CREATE TABLE IF NOT EXISTS standup_schedules (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    TEXT NOT NULL,
                    channel_id  TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    cron_time   TEXT NOT NULL,
                    message     TEXT NOT NULL,
                    meeting_url TEXT,
                    voice_duration_minutes INTEGER DEFAULT 20,
                    active      INTEGER DEFAULT 1,
                    ping_role   TEXT,
                    last_sent   TEXT
                );

                CREATE TABLE IF NOT EXISTS overtime_config (
                    guild_id    TEXT PRIMARY KEY,
                    daily_hours REAL DEFAULT 8.0,
                    weekly_hours REAL DEFAULT 40.0,
                    auto_out_hours REAL DEFAULT 12.0
                );

                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id    TEXT PRIMARY KEY,
                    leave_channel_id TEXT,
                    activity_log_channel_id TEXT,
                    admin_role_id TEXT,
                    present_role_id TEXT,
                    on_break_role_id TEXT,
                    timezone    TEXT DEFAULT 'UTC'
                );

                CREATE TABLE IF NOT EXISTS work_rules (
                    guild_id    TEXT PRIMARY KEY,
                    default_break_minutes REAL DEFAULT 60.0,
                    blocked_weekdays TEXT DEFAULT '5'
                );

                CREATE TABLE IF NOT EXISTS break_entries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    TEXT NOT NULL,
                    user_id     TEXT NOT NULL,
                    username    TEXT NOT NULL,
                    time_entry_id INTEGER,
                    break_start TEXT NOT NULL,
                    break_end   TEXT,
                    duration_minutes INTEGER,
                    break_type  TEXT DEFAULT 'break',
                    reason      TEXT
                );

                CREATE TABLE IF NOT EXISTS scheduled_breaks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    start_time  TEXT NOT NULL,
                    end_time    TEXT NOT NULL,
                    active      INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS work_update_config (
                    guild_id        TEXT PRIMARY KEY,
                    enabled         INTEGER DEFAULT 0,
                    interval_hours  REAL DEFAULT 2.0,
                    question_text   TEXT DEFAULT 'What did you work on?',
                    archive_channel_id TEXT
                );

                CREATE TABLE IF NOT EXISTS work_updates (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id        TEXT NOT NULL,
                    user_id         TEXT NOT NULL,
                    username        TEXT NOT NULL,
                    time_entry_id   INTEGER NOT NULL,
                    prompt_slot     INTEGER NOT NULL,
                    prompted_at     TEXT NOT NULL,
                    question_text   TEXT,
                    submitted_at    TEXT,
                    content         TEXT,
                    UNIQUE(time_entry_id, prompt_slot)
                );
            """)
            columns = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(guild_config)")).fetchall()
            }
            if "present_role_id" not in columns:
                await db.execute("ALTER TABLE guild_config ADD COLUMN present_role_id TEXT")
            if "on_break_role_id" not in columns:
                await db.execute("ALTER TABLE guild_config ADD COLUMN on_break_role_id TEXT")
            if "activity_log_channel_id" not in columns:
                await db.execute("ALTER TABLE guild_config ADD COLUMN activity_log_channel_id TEXT")
            standup_columns = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(standup_schedules)")).fetchall()
            }
            if "meeting_url" not in standup_columns:
                await db.execute("ALTER TABLE standup_schedules ADD COLUMN meeting_url TEXT")
            if "voice_duration_minutes" not in standup_columns:
                await db.execute("ALTER TABLE standup_schedules ADD COLUMN voice_duration_minutes INTEGER DEFAULT 20")
            update_config_columns = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(work_update_config)")).fetchall()
            }
            if "question_text" not in update_config_columns:
                await db.execute(
                    "ALTER TABLE work_update_config ADD COLUMN question_text TEXT DEFAULT 'What did you work on?'"
                )
            if "archive_channel_id" not in update_config_columns:
                await db.execute("ALTER TABLE work_update_config ADD COLUMN archive_channel_id TEXT")
            work_updates_columns = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(work_updates)")).fetchall()
            }
            if "question_text" not in work_updates_columns:
                await db.execute("ALTER TABLE work_updates ADD COLUMN question_text TEXT")
            break_entries_columns = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(break_entries)")).fetchall()
            }
            if "reason" not in break_entries_columns:
                await db.execute("ALTER TABLE break_entries ADD COLUMN reason TEXT")
            await db.commit()
        log.info("Database initialized")

    # ─── CONNECT / DISCONNECT ────────────────────────────────────────────────

    async def clock_in(self, guild_id: str, user_id: str, username: str, note: str = None) -> Dict:
        """Clock a user in. Returns error if already clocked in."""
        active = await self.get_active_entry(guild_id, user_id)
        if active:
            return {"success": False, "error": "already_clocked_in", "entry": active}
        
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO time_entries (guild_id, user_id, username, clock_in, note) VALUES (?,?,?,?,?)",
                (guild_id, user_id, username, now, note)
            )
            await db.commit()
            entry_id = cursor.lastrowid
        return {"success": True, "entry_id": entry_id, "clock_in": now}

    async def clock_out(self, guild_id: str, user_id: str, auto: bool = False) -> Dict:
        """Clock a user out. Returns duration."""
        active = await self.get_active_entry(guild_id, user_id)
        if not active:
            return {"success": False, "error": "not_clocked_in"}
        
        now = datetime.utcnow()
        clock_in = datetime.fromisoformat(active["clock_in"])
        duration = int((now - clock_in).total_seconds() / 60)  # minutes
        
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE time_entries SET clock_out=?, duration_minutes=?, auto_out=? WHERE id=?",
                (now.isoformat(), duration, 1 if auto else 0, active["id"])
            )
            await db.commit()
        
        return {
            "success": True,
            "entry_id": active["id"],
            "duration_minutes": duration,
            "clock_in": active["clock_in"],
            "clock_out": now.isoformat(),
            "auto": auto
        }

    async def get_active_entry(self, guild_id: str, user_id: str) -> Optional[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM time_entries WHERE guild_id=? AND user_id=? AND clock_out IS NULL",
                (guild_id, user_id)
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_active_entries_for_guild(self, guild_id: str) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM time_entries WHERE guild_id=? AND clock_out IS NULL ORDER BY clock_in ASC",
                (guild_id,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def auto_checkout_overdue(self, bot):
        """Auto clock-out anyone clocked in beyond the configured limit."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT te.*, oc.auto_out_hours FROM time_entries te "
                "LEFT JOIN overtime_config oc ON te.guild_id = oc.guild_id "
                "WHERE te.clock_out IS NULL"
            ) as cur:
                rows = await cur.fetchall()
        
        for row in rows:
            row = dict(row)
            limit_hours = row.get("auto_out_hours") or 12.0
            clock_in = datetime.fromisoformat(row["clock_in"])
            if (datetime.utcnow() - clock_in).total_seconds() / 3600 >= limit_hours:
                result = await self.clock_out(row["guild_id"], row["user_id"], auto=True)
                log.info(f"Auto clock-out: {row['username']} after {limit_hours}h")
                # Attempt to notify user
                try:
                    user = await bot.fetch_user(int(row["user_id"]))
                    await user.send(
                        f"⏰ **Auto Clock-Out Notice**\n"
                        f"You were automatically clocked out after **{limit_hours:.0f} hours**.\n"
                        f"Duration: **{result['duration_minutes']//60}h {result['duration_minutes']%60}m**\n"
                        f"Use `/clockin` to clock back in."
                    )
                except Exception:
                    pass

                try:
                    from cogs.activity_log import post_activity_log

                    await post_activity_log(
                        bot,
                        row["guild_id"],
                        title="⏰ Auto Clocked Out",
                        color=0xED4245,
                        fields=[
                            ("Employee", row["username"], True),
                            ("Clock In", f"`{row['clock_in'].replace('T', ' ')[:16]} UTC`", True),
                            ("Clock Out", f"`{result['clock_out'].replace('T', ' ')[:16]} UTC`", True),
                            ("Duration", f"`{result['duration_minutes']//60}h {result['duration_minutes']%60:02d}m`", True),
                        ],
                    )
                except Exception:
                    pass

                try:
                    config = await self.get_guild_config(row["guild_id"])
                    present_role_id = config.get("present_role_id")
                    role_id = config.get("on_break_role_id")
                    guild = bot.get_guild(int(row["guild_id"]))
                    if guild and present_role_id:
                        member = guild.get_member(int(row["user_id"]))
                        if member is None:
                            member = await guild.fetch_member(int(row["user_id"]))
                        role = guild.get_role(int(present_role_id))
                        if member and role and role in member.roles:
                            await member.remove_roles(role, reason="Auto clock-out removed present role")
                    if role_id:
                        if guild:
                            member = guild.get_member(int(row["user_id"]))
                            if member is None:
                                member = await guild.fetch_member(int(row["user_id"]))
                            role = guild.get_role(int(role_id))
                            if member and role and role in member.roles:
                                await member.remove_roles(role, reason="Auto clock-out removed on-break role")
                except Exception:
                    pass

    # ─── QUERIES ─────────────────────────────────────────────────────────────

    async def get_entries_range(self, guild_id: str, user_id: str, start: datetime, end: datetime) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM time_entries WHERE guild_id=? AND user_id=? "
                "AND clock_in >= ? AND clock_in < ? ORDER BY clock_in ASC",
                (guild_id, user_id, start.isoformat(), end.isoformat())
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_all_entries_range(self, guild_id: str, start: datetime, end: datetime) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM time_entries WHERE guild_id=? "
                "AND clock_in >= ? AND clock_in < ? ORDER BY username, clock_in ASC",
                (guild_id, start.isoformat(), end.isoformat())
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_user_summary(self, guild_id: str, user_id: str, start: datetime, end: datetime) -> Dict:
        """Total minutes worked, overtime, entry count for range."""
        entries = await self.get_entries_range(guild_id, user_id, start, end)
        config = await self.get_overtime_config(guild_id)
        rules = await self.get_work_rules(guild_id)
        
        gross_minutes = sum(e["duration_minutes"] or 0 for e in entries if e["clock_out"])
        days_worked = len(set(e["clock_in"][:10] for e in entries))
        break_minutes = await self.get_break_minutes_for_range(guild_id, user_id, start, end)
        default_break_minutes = float(rules.get("default_break_minutes") or 0)
        expected_break_minutes = int(default_break_minutes * days_worked)
        applied_break_minutes = max(break_minutes, expected_break_minutes)
        total_minutes = max(0, gross_minutes - applied_break_minutes)
        expected_minutes = config["daily_hours"] * 60 * days_worked
        overtime_minutes = max(0, total_minutes - expected_minutes)
        
        return {
            "gross_minutes": gross_minutes,
            "break_minutes": applied_break_minutes,
            "total_minutes": total_minutes,
            "overtime_minutes": overtime_minutes,
            "days_worked": days_worked,
            "entry_count": len(entries),
            "entries": entries
        }

    # ─── LEAVE REQUESTS ──────────────────────────────────────────────────────

    async def create_leave_request(self, guild_id, user_id, username, leave_type, 
                                    start_date, end_date, reason) -> int:
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO leave_requests (guild_id, user_id, username, leave_type, "
                "start_date, end_date, reason, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (guild_id, user_id, username, leave_type, start_date, end_date, reason, now)
            )
            await db.commit()
            return cursor.lastrowid

    async def update_leave_status(self, request_id: int, status: str, approver_id: str, approver_name: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE leave_requests SET status=?, approver_id=?, approver_name=? WHERE id=?",
                (status, approver_id, approver_name, request_id)
            )
            await db.commit()

    async def set_leave_message_id(self, request_id: int, message_id: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE leave_requests SET message_id=? WHERE id=?", (message_id, request_id))
            await db.commit()

    async def get_leave_requests(self, guild_id: str, status: str = None, user_id: str = None) -> List[Dict]:
        query = "SELECT * FROM leave_requests WHERE guild_id=?"
        params = [guild_id]
        if status:
            query += " AND status=?"
            params.append(status)
        if user_id:
            query += " AND user_id=?"
            params.append(user_id)
        query += " ORDER BY created_at DESC"
        
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_pending_leave_requests_with_messages(self) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM leave_requests WHERE status='pending' AND message_id IS NOT NULL"
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ─── STANDUP ─────────────────────────────────────────────────────────────

    async def add_standup(
        self,
        guild_id,
        channel_id,
        name,
        cron_time,
        message,
        ping_role=None,
        meeting_url=None,
        voice_duration_minutes: int = 20,
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO standup_schedules (guild_id, channel_id, name, cron_time, message, ping_role, meeting_url, voice_duration_minutes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (guild_id, channel_id, name, cron_time, message, ping_role, meeting_url, voice_duration_minutes)
            )
            await db.commit()
            return cursor.lastrowid

    async def get_standups(self, guild_id: str, active_only=True) -> List[Dict]:
        query = "SELECT * FROM standup_schedules WHERE guild_id=?"
        if active_only:
            query += " AND active=1"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (guild_id,)) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_all_active_standups(self) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM standup_schedules WHERE active=1") as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_standup_last_sent(self, standup_id: int, when: str):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE standup_schedules SET last_sent=? WHERE id=?", (when, standup_id))
            await db.commit()

    async def toggle_standup(self, standup_id: int, active: bool):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE standup_schedules SET active=? WHERE id=?", (1 if active else 0, standup_id))
            await db.commit()

    async def delete_standup(self, standup_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM standup_schedules WHERE id=?", (standup_id,))
            await db.commit()

    # ─── OVERTIME CONFIG ─────────────────────────────────────────────────────

    async def get_overtime_config(self, guild_id: str) -> Dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM overtime_config WHERE guild_id=?", (guild_id,)) as cur:
                row = await cur.fetchone()
        if row:
            return dict(row)
        return {"guild_id": guild_id, "daily_hours": 8.0, "weekly_hours": 40.0, "auto_out_hours": 12.0}

    async def set_overtime_config(self, guild_id: str, daily_hours: float, weekly_hours: float, auto_out_hours: float):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO overtime_config (guild_id, daily_hours, weekly_hours, auto_out_hours) "
                "VALUES (?,?,?,?)",
                (guild_id, daily_hours, weekly_hours, auto_out_hours)
            )
            await db.commit()

    async def get_break_minutes_for_entry(self, entry_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(duration_minutes), 0) FROM break_entries WHERE time_entry_id=? AND break_end IS NOT NULL",
                (entry_id,)
            ) as cur:
                row = await cur.fetchone()
        return int(row[0] or 0)

    async def get_break_minutes_for_range(self, guild_id: str, user_id: str, start: datetime, end: datetime) -> int:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT te.clock_in, COALESCE(be.duration_minutes, 0) AS duration_minutes "
                "FROM break_entries be "
                "JOIN time_entries te ON te.id = be.time_entry_id "
                "WHERE te.guild_id=? AND te.user_id=? AND te.clock_in >= ? AND te.clock_in < ? "
                "AND be.break_end IS NOT NULL",
                (guild_id, user_id, start.isoformat(), end.isoformat())
            ) as cur:
                rows = await cur.fetchall()

        return sum(int(row[1] or 0) for row in rows)

    async def get_work_rules(self, guild_id: str) -> Dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM work_rules WHERE guild_id=?", (guild_id,)) as cur:
                row = await cur.fetchone()
        if row:
            data = dict(row)
            data["blocked_weekdays"] = normalize_blocked_weekdays(data.get("blocked_weekdays"))
            return data
        return {
            "guild_id": guild_id,
            "default_break_minutes": 60.0,
            "blocked_weekdays": "5",
        }

    async def set_work_rules(self, guild_id: str, default_break_minutes: float = 60.0, blocked_weekdays: str = "5"):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO work_rules (guild_id, default_break_minutes, blocked_weekdays) "
                "VALUES (?,?,?)",
                (guild_id, default_break_minutes, normalize_blocked_weekdays(blocked_weekdays))
            )
            await db.commit()

    async def get_scheduled_breaks(self, guild_id: str, active_only: bool = True) -> List[Dict]:
        query = "SELECT * FROM scheduled_breaks WHERE guild_id=?"
        params = [guild_id]
        if active_only:
            query += " AND active=1"
        query += " ORDER BY start_time ASC, id ASC"
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def replace_scheduled_breaks(self, guild_id: str, schedules: List[Dict]):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM scheduled_breaks WHERE guild_id=?", (guild_id,))
            for schedule in schedules:
                await db.execute(
                    """
                    INSERT INTO scheduled_breaks (guild_id, name, start_time, end_time, active)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        guild_id,
                        schedule["name"],
                        schedule["start_time"],
                        schedule["end_time"],
                        1 if schedule.get("active", True) else 0,
                    ),
                )
            await db.commit()

    # ─── GUILD CONFIG ────────────────────────────────────────────────────────

    async def get_guild_config(self, guild_id: str) -> Dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)) as cur:
                row = await cur.fetchone()
        if row:
            return dict(row)
        return {
            "guild_id": guild_id,
            "leave_channel_id": None,
            "activity_log_channel_id": None,
            "admin_role_id": None,
            "present_role_id": None,
            "on_break_role_id": None,
            "timezone": "UTC",
        }

    async def set_guild_config(self, guild_id: str, **kwargs):
        existing = await self.get_guild_config(guild_id)
        existing.update(kwargs)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO guild_config (guild_id, leave_channel_id, activity_log_channel_id, admin_role_id, present_role_id, on_break_role_id, timezone) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    guild_id,
                    existing.get("leave_channel_id"),
                    existing.get("activity_log_channel_id"),
                    existing.get("admin_role_id"),
                    existing.get("present_role_id"),
                    existing.get("on_break_role_id"),
                    existing.get("timezone", "UTC"),
                )
            )
            await db.commit()

    async def get_work_update_config(self, guild_id: str) -> Dict:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM work_update_config WHERE guild_id=?", (guild_id,)) as cur:
                row = await cur.fetchone()
        if row:
            return dict(row)
        return {
            "guild_id": guild_id,
            "enabled": 0,
            "interval_hours": 2.0,
            "question_text": "What did you work on?",
            "archive_channel_id": None,
        }

    async def set_work_update_config(
        self,
        guild_id: str,
        enabled: bool,
        interval_hours: float,
        question_text: str,
        archive_channel_id: str = None,
    ):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO work_update_config
                (guild_id, enabled, interval_hours, question_text, archive_channel_id)
                VALUES (?,?,?,?,?)
                """,
                (guild_id, 1 if enabled else 0, interval_hours, question_text, archive_channel_id)
            )
            await db.commit()

    async def get_enabled_work_update_configs(self) -> List[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM work_update_config WHERE enabled=1 AND interval_hours > 0"
            ) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def ensure_work_update_prompt(
        self,
        guild_id: str,
        user_id: str,
        username: str,
        time_entry_id: int,
        prompt_slot: int,
        question_text: str,
    ) -> bool:
        prompted_at = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO work_updates
                (guild_id, user_id, username, time_entry_id, prompt_slot, prompted_at, question_text, submitted_at, content)
                VALUES (?,?,?,?,?,?,?,NULL,NULL)
                """,
                (guild_id, user_id, username, time_entry_id, prompt_slot, prompted_at, question_text)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def submit_work_update(
        self,
        guild_id: str,
        user_id: str,
        username: str,
        time_entry_id: int,
        prompt_slot: int,
        question_text: str,
        content: str,
    ):
        submitted_at = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO work_updates
                (guild_id, user_id, username, time_entry_id, prompt_slot, prompted_at, question_text, submitted_at, content)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(time_entry_id, prompt_slot)
                DO UPDATE SET
                    username=excluded.username,
                    question_text=excluded.question_text,
                    submitted_at=excluded.submitted_at,
                    content=excluded.content
                """,
                (
                    guild_id,
                    user_id,
                    username,
                    time_entry_id,
                    prompt_slot,
                    submitted_at,
                    question_text,
                    submitted_at,
                    content,
                )
            )
            await db.commit()

    async def get_work_updates(
        self,
        guild_id: str,
        start: datetime,
        end: datetime,
        user_id: str = None,
        submitted_only: bool = True,
    ) -> List[Dict]:
        query = (
            "SELECT * FROM work_updates WHERE guild_id=? AND prompted_at >= ? AND prompted_at < ?"
        )
        params = [guild_id, start.isoformat(), end.isoformat()]
        if user_id:
            query += " AND user_id=?"
            params.append(user_id)
        if submitted_only:
            query += " AND submitted_at IS NOT NULL AND content IS NOT NULL"
        query += " ORDER BY prompted_at ASC"

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def get_pending_work_update(self, guild_id: str, user_id: str, time_entry_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM work_updates
                WHERE guild_id=? AND user_id=? AND time_entry_id=? AND submitted_at IS NULL
                ORDER BY prompt_slot DESC
                LIMIT 1
                """,
                (guild_id, user_id, time_entry_id)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None
