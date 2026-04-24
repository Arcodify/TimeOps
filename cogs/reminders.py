"""
Reminders Cog
Background task + commands for end-of-day clock-out reminders.
/reminder set   — configure EOD reminder time per guild
/reminder test  — fire a test reminder right now
"""

import discord
from discord.ext import commands
from discord import app_commands
from discord.ext.tasks import loop
import aiosqlite
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from database import parse_blocked_weekdays, parse_hhmm

log = logging.getLogger("Reminders")

reminder_group = app_commands.Group(name="reminder", description="Clock-out reminder settings")


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        async with aiosqlite.connect(self.bot.db.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reminder_config (
                    guild_id        TEXT PRIMARY KEY,
                    reminder_time   TEXT DEFAULT '17:00',
                    enabled         INTEGER DEFAULT 1,
                    channel_id      TEXT
                )
            """)
            await db.commit()
        self.reminder_loop.start()

    async def cog_unload(self):
        self.reminder_loop.cancel()

    @loop(minutes=1)
    async def reminder_loop(self):
        """Check every minute whether it's time to send EOD reminders."""
        configs = await _get_all_configs(self.bot.db.path)
        now_str = datetime.utcnow().strftime("%H:%M")
        for config in configs:
            if config["reminder_time"] == now_str:
                await _send_reminders(self.bot, config["guild_id"], config.get("channel_id"))
        await _send_shift_start_reminders(self.bot)


async def _get_config(db_path: str, guild_id: str):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM reminder_config WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else {"reminder_time": "17:00", "enabled": 1, "channel_id": None}


async def _get_all_configs(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM reminder_config WHERE enabled=1") as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _send_reminders(bot, guild_id: str, channel_id: str = None):
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return

    import aiosqlite as _aio
    async with _aio.connect(bot.db.path) as db:
        db.row_factory = _aio.Row
        async with db.execute(
            "SELECT * FROM time_entries WHERE guild_id=? AND clock_out IS NULL",
            (guild_id,)
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return

    reminded = 0
    for row in rows:
        row = dict(row)
        clock_in = datetime.fromisoformat(row["clock_in"])
        elapsed = int((datetime.utcnow() - clock_in).total_seconds() / 60)
        try:
            member = guild.get_member(int(row["user_id"]))
            if not member:
                member = await guild.fetch_member(int(row["user_id"]))
            await member.send(
                f"⏰ **End-of-Day Reminder**\n"
                f"You're still clocked in from `{row['clock_in'].replace('T',' ')[:16]} UTC` "
                f"({elapsed//60}h {elapsed%60:02d}m ago).\n"
                f"Don't forget to `/clockout` when you're done!"
            )
            reminded += 1
        except Exception:
            pass

    if channel_id and reminded > 0:
        try:
            channel = guild.get_channel(int(channel_id))
            if channel:
                names = ", ".join(dict(r)["username"] for r in rows)
                await channel.send(
                    f"⏰ **EOD Reminder sent** — {reminded} employee(s) still clocked in: {names}"
                )
        except Exception:
            pass

    log.info(f"EOD reminders sent to {reminded} users in guild {guild_id}")


async def _get_guild_timezone(bot, guild_id: str) -> tuple[ZoneInfo, str]:
    config = await bot.db.get_guild_config(guild_id)
    timezone_name = config.get("timezone") or "UTC"
    try:
        return ZoneInfo(timezone_name), timezone_name
    except Exception:
        return ZoneInfo("UTC"), "UTC"


async def _send_shift_start_reminders(bot):
    for guild in bot.guilds:
        guild_id = str(guild.id)
        overtime_config = await bot.db.get_overtime_config(guild_id)
        if (overtime_config.get("mode") or "overtime") != "time_shift":
            continue

        shift_clock_in_time = (overtime_config.get("shift_clock_in_time") or "").strip()
        if not shift_clock_in_time:
            continue

        tz, timezone_name = await _get_guild_timezone(bot, guild_id)
        local_now = datetime.now(tz)
        if local_now.strftime("%H:%M") != shift_clock_in_time:
            continue

        work_rules = await bot.db.get_work_rules(guild_id)
        if local_now.weekday() in parse_blocked_weekdays(work_rules.get("blocked_weekdays")):
            continue

        local_date = local_now.date().isoformat()
        reminder_key = f"shift_start:{local_date}:{shift_clock_in_time}"
        on_leave_user_ids = await bot.db.get_users_on_approved_leave(guild_id, local_date)
        active_entries = await bot.db.get_active_entries_for_guild(guild_id)
        already_clocked_in = {str(entry["user_id"]) for entry in active_entries}
        known_user_ids = await bot.db.get_known_user_ids_for_guild(guild_id)
        shift_start_local = local_now.replace(
            hour=parse_hhmm(shift_clock_in_time).hour,
            minute=parse_hhmm(shift_clock_in_time).minute,
            second=0,
            microsecond=0,
        )

        for user_id in known_user_ids:
            if user_id in on_leave_user_ids or user_id in already_clocked_in:
                continue

            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except Exception:
                    member = None
            if member is None or member.bot:
                continue

            first_send = await bot.db.record_reminder_dispatch(guild_id, user_id, reminder_key)
            if not first_send:
                continue

            try:
                await member.send(
                    "⏰ **Shift Start Reminder**\n"
                    f"It's time to clock in for your shift.\n"
                    f"Scheduled clock-in time: `{shift_start_local.strftime('%Y-%m-%d %H:%M')} {timezone_name}`.\n"
                    "If you're starting now, use `/clockin`."
                )
            except Exception:
                pass


@reminder_group.command(name="set", description="Configure end-of-day clock-out reminder")
@app_commands.describe(
    time="Reminder time in HH:MM UTC (e.g. 17:00)",
    enabled="Turn reminders on or off",
    channel="Optional channel for a summary message"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def reminder_set(
    interaction: discord.Interaction,
    time: str = "17:00",
    enabled: bool = True,
    channel: discord.TextChannel = None
):
    db_path = interaction.client.db.path

    try:
        datetime.strptime(time, "%H:%M")
    except ValueError:
        await interaction.response.send_message("❌ Invalid time. Use HH:MM (24h UTC).", ephemeral=True)
        return

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO reminder_config (guild_id, reminder_time, enabled, channel_id) "
            "VALUES (?,?,?,?)",
            (str(interaction.guild_id), time, 1 if enabled else 0,
             str(channel.id) if channel else None)
        )
        await db.commit()

    embed = discord.Embed(title="⏰ Reminder Configured", color=0x57F287)
    embed.add_field(name="Time (UTC)", value=f"`{time}`", inline=True)
    embed.add_field(name="Status", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
    embed.add_field(name="Summary Channel", value=channel.mention if channel else "DM only", inline=True)
    embed.set_footer(text="Sends a DM to anyone still clocked in at the reminder time")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@reminder_group.command(name="test", description="Send EOD reminders right now (test)")
@app_commands.checks.has_permissions(manage_guild=True)
async def reminder_test(interaction: discord.Interaction):
    bot = interaction.client
    await interaction.response.defer(ephemeral=True)
    config = await _get_config(bot.db.path, str(interaction.guild_id))
    await _send_reminders(bot, str(interaction.guild_id), config.get("channel_id"))
    await interaction.followup.send("✅ Test reminders sent to all currently clocked-in users.", ephemeral=True)


@reminder_group.command(name="status", description="View current reminder settings")
async def reminder_status(interaction: discord.Interaction):
    config = await _get_config(interaction.client.db.path, str(interaction.guild_id))
    embed = discord.Embed(title="⏰ Reminder Settings", color=0x5865F2)
    embed.add_field(name="Time (UTC)", value=f"`{config['reminder_time']}`", inline=True)
    embed.add_field(
        name="Status",
        value="🟢 Enabled" if config.get("enabled", 1) else "🔴 Disabled",
        inline=True
    )
    ch = config.get("channel_id")
    embed.add_field(name="Channel", value=f"<#{ch}>" if ch else "DM only", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = Reminders(bot)
    bot.tree.add_command(reminder_group)
    await bot.add_cog(cog)
