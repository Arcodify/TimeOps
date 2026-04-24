"""
Breaks Cog
/break start, /break end, /break status
Tracks paid/unpaid break time within a session.
Break time is subtracted from total worked hours.
"""

import discord
from discord.ext import commands
from discord.ext.tasks import loop
from discord import app_commands
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from cogs.activity_log import post_activity_log

log = logging.getLogger("Breaks")


def fmt_duration(minutes: int) -> str:
    if not minutes:
        return "0h 00m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


break_group = app_commands.Group(name="break", description="Break time tracking")


def _label_break(entry: dict) -> str:
    if entry.get("break_type") == "scheduled":
        return entry.get("reason") or "Scheduled Break"
    mapping = {"break": "Regular Break", "lunch": "Lunch", "personal": "Personal"}
    return mapping.get(entry.get("break_type"), entry.get("break_type", "Break").title())


def _parse_hhmm(value: str):
    return datetime.strptime(value, "%H:%M").time()


async def _get_guild_timezone(bot, guild_id: str) -> tuple[ZoneInfo, str]:
    config = await bot.db.get_guild_config(guild_id)
    timezone_name = config.get("timezone") or "UTC"
    try:
        return ZoneInfo(timezone_name), timezone_name
    except Exception:
        return ZoneInfo("UTC"), "UTC"


async def _get_current_scheduled_break(bot, guild: discord.Guild):
    schedules = await bot.db.get_scheduled_breaks(str(guild.id))
    if not schedules:
        return None

    tz, _ = await _get_guild_timezone(bot, str(guild.id))
    local_time = datetime.now(tz).time()
    for schedule in schedules:
        start = _parse_hhmm(schedule["start_time"])
        end = _parse_hhmm(schedule["end_time"])
        if start <= local_time < end:
            return schedule
    return None


async def _get_named_scheduled_break(bot, guild: discord.Guild, schedule_name: str):
    schedules = await bot.db.get_scheduled_breaks(str(guild.id))
    for schedule in schedules:
        if schedule["name"] == schedule_name:
            return schedule
    return None


def _schedule_window_for_now(schedule: dict, local_now: datetime) -> tuple[datetime, datetime]:
    start = _parse_hhmm(schedule["start_time"])
    end = _parse_hhmm(schedule["end_time"])
    start_local = local_now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    end_local = local_now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    return start_local, end_local


async def _notify_break_message(
    bot,
    guild: discord.Guild,
    user_id: str,
    *,
    title: str,
    description: str,
    color: int,
):
    try:
        member = guild.get_member(int(user_id))
        if member is None:
            member = await guild.fetch_member(int(user_id))
        if member is None:
            return
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow(),
        )
        await member.send(embed=embed)
    except Exception:
        pass


async def _mark_break_reminder_sent(db_path: str, break_id: int):
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE break_entries SET reminder_sent_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), break_id),
        )
        await db.commit()


class Breaks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Ensure breaks table exists
        import aiosqlite
        async with aiosqlite.connect(self.bot.db.path) as db:
            await db.execute("""
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
                )
            """)
            columns = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(break_entries)")).fetchall()
            }
            if "reason" not in columns:
                await db.execute("ALTER TABLE break_entries ADD COLUMN reason TEXT")
            await db.commit()
        self.scheduled_break_loop.start()

    async def cog_unload(self):
        self.scheduled_break_loop.cancel()

    @loop(minutes=1)
    async def scheduled_break_loop(self):
        for guild in self.bot.guilds:
            current_schedule = await _get_current_scheduled_break(self.bot, guild)
            active_entries = await self.bot.db.get_active_entries_for_guild(str(guild.id))
            tz, _ = await _get_guild_timezone(self.bot, str(guild.id))
            local_now = datetime.now(tz)

            for entry in active_entries:
                member = guild.get_member(int(entry["user_id"]))
                if member is None:
                    try:
                        member = await guild.fetch_member(int(entry["user_id"]))
                    except Exception:
                        member = None

                active_break = await _get_active_break(self.bot.db.path, str(guild.id), entry["user_id"])

                if active_break and active_break.get("break_type") == "scheduled":
                    active_name = active_break.get("reason") or ""
                    schedule = await _get_named_scheduled_break(self.bot, guild, active_name)
                    if schedule is not None and not active_break.get("reminder_sent_at"):
                        _, end_local = _schedule_window_for_now(schedule, local_now)
                        reminder_local = end_local - timedelta(minutes=1)
                        if reminder_local <= local_now < end_local:
                            await _notify_break_message(
                                self.bot,
                                guild,
                                entry["user_id"],
                                title="⏰ Break ends in 1 minute",
                                description=(
                                    f"Your scheduled break **{schedule['name']}** ends at "
                                    f"`{schedule['end_time']}`.\n"
                                    "Please end break manually when you're back."
                                ),
                                color=0x5865F2,
                            )
                            await _mark_break_reminder_sent(self.bot.db.path, active_break["id"])
                    continue

                if active_break:
                    continue

                if current_schedule is None:
                    continue

                session_breaks = await _get_session_breaks(
                    self.bot.db.path,
                    str(guild.id),
                    entry["user_id"],
                    entry["id"],
                )
                already_started = any(
                    item.get("break_type") == "scheduled" and (item.get("reason") or "") == current_schedule["name"]
                    for item in session_breaks
                )
                if already_started:
                    continue

                await _start_break(
                    self.bot.db.path,
                    str(guild.id),
                    entry["user_id"],
                    entry["username"],
                    "scheduled",
                    entry["id"],
                    reason=current_schedule["name"],
                )
                if member is not None:
                    try:
                        await _apply_on_break_role(self.bot, guild, member)
                    except Exception:
                        pass
                await post_activity_log(
                    self.bot,
                    str(guild.id),
                    title="☕ Automatic Break Started",
                    color=0xFEE75C,
                    fields=[
                        ("Employee", entry["username"], True),
                        ("Break", current_schedule["name"], True),
                        ("Window", f"`{current_schedule['start_time']}` → `{current_schedule['end_time']}`", True),
                    ],
                )
                await _notify_break_message(
                    self.bot,
                    guild,
                    entry["user_id"],
                    title="☕ This break started",
                    description=(
                        f"Your scheduled break **{current_schedule['name']}** has started.\n"
                        "You have been marked as on break."
                    ),
                    color=0xFEE75C,
                )


async def _get_active_break(db_path: str, guild_id: str, user_id: str):
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM break_entries WHERE guild_id=? AND user_id=? AND break_end IS NULL",
            (guild_id, user_id)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def _get_on_break_role(bot, guild: discord.Guild):
    config = await bot.db.get_guild_config(str(guild.id))
    role_id = config.get("on_break_role_id")
    if not role_id:
        return None
    return guild.get_role(int(role_id))


async def _apply_on_break_role(bot, guild: discord.Guild, member: discord.Member):
    role = await _get_on_break_role(bot, guild)
    if role is None or role in member.roles:
        return
    await member.add_roles(role, reason="Started break")


async def _clear_on_break_role(bot, guild: discord.Guild, member: discord.Member):
    role = await _get_on_break_role(bot, guild)
    if role is None or role not in member.roles:
        return
    await member.remove_roles(role, reason="Ended break")


async def _start_break(
    db_path: str,
    guild_id: str,
    user_id: str,
    username: str,
    break_type: str,
    entry_id: int,
    reason: str = None,
):
    import aiosqlite
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO break_entries (guild_id, user_id, username, time_entry_id, break_start, break_type, reason) "
            "VALUES (?,?,?,?,?,?,?)",
            (guild_id, user_id, username, entry_id, now, break_type, reason)
        )
        await db.commit()
        return cursor.lastrowid, now


async def _end_break(db_path: str, guild_id: str, user_id: str):
    import aiosqlite
    active = await _get_active_break(db_path, guild_id, user_id)
    if not active:
        return None
    now = datetime.utcnow()
    start = datetime.fromisoformat(active["break_start"])
    duration = int((now - start).total_seconds() / 60)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE break_entries SET break_end=?, duration_minutes=? WHERE id=?",
            (now.isoformat(), duration, active["id"])
        )
        await db.commit()
    return {**active, "break_end": now.isoformat(), "duration_minutes": duration}


async def _get_session_breaks(db_path: str, guild_id: str, user_id: str, entry_id: int):
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM break_entries WHERE guild_id=? AND user_id=? AND time_entry_id=?",
            (guild_id, user_id, entry_id)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _get_active_auto_break_message(bot, guild: discord.Guild, active_break: dict | None) -> str | None:
    if guild is None or not active_break or active_break.get("break_type") != "scheduled":
        return None

    schedule = await _get_named_scheduled_break(bot, guild, active_break.get("reason") or "")
    if schedule is None:
        return None

    tz, timezone_name = await _get_guild_timezone(bot, str(guild.id))
    local_now = datetime.now(tz)
    _, end_local = _schedule_window_for_now(schedule, local_now)
    manual_end_from = end_local - timedelta(minutes=1)
    if local_now >= manual_end_from:
        return None

    return (
        "⚠️ This is a scheduled company break window.\n"
        f"You can end it manually from `{manual_end_from.strftime('%H:%M')}` ({timezone_name})."
    )


@break_group.command(name="start", description="Start a break")
@app_commands.describe(break_type="Type of break")
@app_commands.choices(break_type=[
    app_commands.Choice(name="Regular Break", value="break"),
    app_commands.Choice(name="Lunch", value="lunch"),
    app_commands.Choice(name="Personal", value="personal"),
])
async def break_start(interaction: discord.Interaction, break_type: str = "break"):
    db = interaction.client.db
    db_path = db.path
    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)

    active_entry = await db.get_active_entry(guild_id, user_id)
    if not active_entry:
        await interaction.response.send_message(
            "⚠️ You must be clocked in to start a break. Use `/clockin` first.",
            ephemeral=True
        )
        return

    active_break = await _get_active_break(db_path, guild_id, user_id)
    if active_break:
        started = active_break["break_start"].replace("T", " ")[:16]
        await interaction.response.send_message(
            f"⚠️ You're already on a **{active_break['break_type']}** since `{started} UTC`.\n"
            f"Use `/break end` to end it.",
            ephemeral=True
        )
        return

    _, now = await _start_break(
        db_path, guild_id, user_id, interaction.user.display_name, break_type, active_entry["id"]
    )

    try:
        if isinstance(interaction.user, discord.Member):
            await _apply_on_break_role(interaction.client, interaction.guild, interaction.user)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass

    icons = {"break": "☕", "lunch": "🍽️", "personal": "🚶"}
    icon = icons.get(break_type, "⏸️")

    embed = discord.Embed(
        title="☕ This break started",
        description=(
            f"You are now on **{_label_break({'break_type': break_type})}**.\n"
            "Please make sure to end the break when you're back. Otherwise it will only end after clock-out."
        ),
        color=0xFEE75C,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Type", value=_label_break({"break_type": break_type}), inline=True)
    embed.add_field(name="Started", value=f"`{now.replace('T',' ')[:16]} UTC`", inline=True)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await post_activity_log(
        interaction.client,
        str(interaction.guild_id),
        title=f"{icon} Break Started",
        color=0xFEE75C,
        fields=[
            ("Employee", interaction.user.mention, True),
            ("Break", _label_break({"break_type": break_type}), True),
            ("Started", f"`{now.replace('T', ' ')[:16]} UTC`", True),
        ],
        thumbnail_url=interaction.user.display_avatar.url,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@break_group.command(name="end", description="End your current break")
async def break_end(interaction: discord.Interaction):
    db_path = interaction.client.db.path
    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)
    active_break = await _get_active_break(db_path, guild_id, user_id)
    auto_break_message = await _get_active_auto_break_message(interaction.client, interaction.guild, active_break)
    if auto_break_message:
        await interaction.response.send_message(auto_break_message, ephemeral=True)
        return

    result = await _end_break(db_path, guild_id, user_id)
    if not result:
        await interaction.response.send_message("⚠️ You're not on a break.", ephemeral=True)
        return

    try:
        if isinstance(interaction.user, discord.Member):
            await _clear_on_break_role(interaction.client, interaction.guild, interaction.user)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass

    embed = discord.Embed(
        title="✅ This break ended",
        color=0x57F287,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Break Type", value=_label_break(result), inline=True)
    embed.add_field(name="Duration", value=f"**{fmt_duration(result['duration_minutes'])}**", inline=True)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await post_activity_log(
        interaction.client,
        str(interaction.guild_id),
        title="✅ Break Ended",
        color=0x57F287,
        fields=[
            ("Employee", interaction.user.mention, True),
            ("Break", _label_break(result), True),
            ("Duration", f"`{fmt_duration(result['duration_minutes'])}`", True),
        ],
        thumbnail_url=interaction.user.display_avatar.url,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@break_group.command(name="status", description="View your break history for the current session")
async def break_status(interaction: discord.Interaction):
    db = interaction.client.db
    db_path = db.path
    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)

    active_entry = await db.get_active_entry(guild_id, user_id)
    active_break = await _get_active_break(db_path, guild_id, user_id)

    embed = discord.Embed(
        title=f"☕ Break Status — {interaction.user.display_name}",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )

    if active_break:
        elapsed = int((datetime.utcnow() - datetime.fromisoformat(active_break["break_start"])).total_seconds() / 60)
        embed.add_field(
            name="🟡 Currently On Break",
            value=f"**{_label_break(active_break)}** — {fmt_duration(elapsed)} elapsed",
            inline=False
        )

    if active_entry:
        breaks = await _get_session_breaks(db_path, guild_id, user_id, active_entry["id"])
        completed = [b for b in breaks if b["break_end"]]
        total_break_mins = sum(b["duration_minutes"] or 0 for b in completed)
        embed.add_field(name="Breaks This Session", value=str(len(completed)), inline=True)
        embed.add_field(name="Total Break Time", value=fmt_duration(total_break_mins), inline=True)

        if completed:
            history = "\n".join(
                f"• {_label_break(b)}: {fmt_duration(b['duration_minutes'])} "
                f"({b['break_start'][11:16]}–{b['break_end'][11:16]} UTC)"
                for b in completed[-5:]
            )
            embed.add_field(name="Recent Breaks", value=history, inline=False)
    else:
        embed.add_field(name="Status", value="Not clocked in", inline=False)

    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@break_group.command(name="configure", description="Configure up to two scheduled company break periods")
@app_commands.describe(
    break1_name="Label for the first scheduled break",
    break1_start="First break start in HH:MM server timezone",
    break1_end="First break end in HH:MM server timezone",
    break2_name="Label for the second scheduled break",
    break2_start="Second break start in HH:MM server timezone",
    break2_end="Second break end in HH:MM server timezone",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def break_configure(
    interaction: discord.Interaction,
    break1_name: str,
    break1_start: str,
    break1_end: str,
    break2_name: str = None,
    break2_start: str = None,
    break2_end: str = None,
):
    schedules = []
    raw_schedules = [
        (break1_name, break1_start, break1_end),
        (break2_name, break2_start, break2_end),
    ]

    for idx, (name, start, end) in enumerate(raw_schedules, start=1):
        if not any((name, start, end)):
            continue
        if not all((name, start, end)):
            await interaction.response.send_message(
                f"❌ Break {idx} needs a name, start time, and end time.",
                ephemeral=True,
            )
            return
        try:
            start_time = _parse_hhmm(start)
            end_time = _parse_hhmm(end)
        except ValueError:
            await interaction.response.send_message(
                f"❌ Break {idx} time must use HH:MM format.",
                ephemeral=True,
            )
            return
        if end_time <= start_time:
            await interaction.response.send_message(
                f"❌ Break {idx} end time must be after start time.",
                ephemeral=True,
            )
            return
        schedules.append(
            {"name": name.strip(), "start_time": start, "end_time": end, "active": True}
        )

    if not schedules:
        await interaction.response.send_message("❌ Configure at least one break period.", ephemeral=True)
        return

    await interaction.client.db.replace_scheduled_breaks(str(interaction.guild_id), schedules)
    _, timezone_name = await _get_guild_timezone(interaction.client, str(interaction.guild_id))

    embed = discord.Embed(title="☕ Scheduled Breaks Configured", color=0x57F287)
    for schedule in schedules:
        embed.add_field(
            name=schedule["name"],
            value=f"`{schedule['start_time']}` → `{schedule['end_time']}` ({timezone_name})",
            inline=True,
        )
    embed.set_footer(text="Clocked-in staff will be auto-marked on break during these windows.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@break_group.command(name="schedule", description="View configured scheduled company break periods")
async def break_schedule(interaction: discord.Interaction):
    schedules = await interaction.client.db.get_scheduled_breaks(str(interaction.guild_id), active_only=False)
    if not schedules:
        await interaction.response.send_message("No scheduled break periods configured.", ephemeral=True)
        return

    _, timezone_name = await _get_guild_timezone(interaction.client, str(interaction.guild_id))
    embed = discord.Embed(title="☕ Scheduled Break Periods", color=0x5865F2)
    for schedule in schedules:
        embed.add_field(
            name=schedule["name"],
            value=f"`{schedule['start_time']}` → `{schedule['end_time']}` ({timezone_name})",
            inline=True,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = Breaks(bot)
    bot.tree.add_command(break_group)
    await bot.add_cog(cog)
