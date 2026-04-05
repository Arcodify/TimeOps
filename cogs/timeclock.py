"""
TimeClock Cog
/clockin, /clockout, /status commands
Interactive button clock-in/out panel
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from database import Database, parse_blocked_weekdays

log = logging.getLogger("TimeClock")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def fmt_duration(minutes: int) -> str:
    if minutes is None:
        return "—"
    h = minutes // 60
    m = minutes % 60
    return f"{h}h {m:02d}m"


def _format_weekdays(days: list[int]) -> str:
    labels = [WEEKDAY_NAMES[day] for day in days if 0 <= day <= 6]
    return ", ".join(labels) if labels else "No blocked days"


async def _can_clock_in(db: Database, guild_id: str) -> tuple[bool, str | None]:
    guild_config = await db.get_guild_config(guild_id)
    work_rules = await db.get_work_rules(guild_id)

    timezone_name = guild_config.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo("UTC")
        timezone_name = "UTC"

    local_now = datetime.now(tz)
    blocked_days = parse_blocked_weekdays(work_rules.get("blocked_weekdays"))
    if local_now.weekday() in blocked_days:
        return False, (
            f"⚠️ Clock in is disabled on **{local_now.strftime('%A')}** in `{timezone_name}`. "
            f"Blocked days: **{_format_weekdays(blocked_days)}**."
        )

    return True, None


class ClockPanel(discord.ui.View):
    """Persistent clock in/out panel with buttons."""
    def __init__(self, db: Database):
        super().__init__(timeout=None)
        self.db = db

    @discord.ui.button(label="🟢 Clock In", style=discord.ButtonStyle.success, custom_id="hr:clockin")
    async def clock_in_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        allowed, message = await _can_clock_in(self.db, str(interaction.guild_id))
        if not allowed:
            await interaction.response.send_message(message, ephemeral=True)
            return

        result = await self.db.clock_in(
            str(interaction.guild_id),
            str(interaction.user.id),
            interaction.user.display_name
        )
        if not result["success"]:
            entry = result["entry"]
            clock_in_time = entry["clock_in"].replace("T", " ")[:16]
            await interaction.response.send_message(
                f"⚠️ You're already clocked in since `{clock_in_time} UTC`.\nUse **Clock Out** first.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="✅ Clocked In",
            description=f"{interaction.user.mention} is now on the clock!",
            color=0x57F287,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Started", value=f"`{result['clock_in'].replace('T',' ')[:16]} UTC`")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🔴 Clock Out", style=discord.ButtonStyle.danger, custom_id="hr:clockout")
    async def clock_out_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = await self.db.clock_out(
            str(interaction.guild_id),
            str(interaction.user.id)
        )
        if not result["success"]:
            await interaction.response.send_message(
                "⚠️ You're not currently clocked in.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="🔴 Clocked Out",
            description=f"{interaction.user.mention} has clocked out.",
            color=0xED4245,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Duration", value=f"**{fmt_duration(result['duration_minutes'])}**", inline=True)
        embed.add_field(name="Clock In", value=f"`{result['clock_in'].replace('T',' ')[:16]} UTC`", inline=True)
        embed.add_field(name="Clock Out", value=f"`{result['clock_out'].replace('T',' ')[:16]} UTC`", inline=True)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📊 My Status", style=discord.ButtonStyle.secondary, custom_id="hr:status")
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _send_status(interaction, self.db, interaction.user)


async def _send_status(interaction: discord.Interaction, db: Database, user: discord.User = None):
    if user is None:
        user = interaction.user
    active = await db.get_active_entry(str(interaction.guild_id), str(user.id))
    
    # Today's stats
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    summary = await db.get_user_summary(
        str(interaction.guild_id), str(user.id),
        today, today + timedelta(days=1)
    )
    
    embed = discord.Embed(
        title=f"📊 Status — {user.display_name}",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    
    if active:
        clock_in = datetime.fromisoformat(active["clock_in"])
        elapsed = int((datetime.utcnow() - clock_in).total_seconds() / 60)
        embed.add_field(name="Status", value="🟢 **Clocked In**", inline=False)
        embed.add_field(name="Since", value=f"`{active['clock_in'].replace('T',' ')[:16]} UTC`", inline=True)
        embed.add_field(name="Elapsed", value=f"**{fmt_duration(elapsed)}**", inline=True)
    else:
        embed.add_field(name="Status", value="🔴 **Clocked Out**", inline=False)
    
    embed.add_field(name="Today Total", value=fmt_duration(summary["total_minutes"]), inline=True)
    embed.add_field(name="Break Time", value=fmt_duration(summary["break_minutes"]), inline=True)
    embed.add_field(name="Today Sessions", value=str(summary["entry_count"]), inline=True)
    if summary["overtime_minutes"] > 0:
        embed.add_field(name="⚡ Overtime Today", value=fmt_duration(summary["overtime_minutes"]), inline=True)
    
    embed.set_thumbnail(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


class TimeClock(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db: Database = bot.db

    @app_commands.command(name="clockin", description="Clock in to start your work session")
    @app_commands.describe(note="Optional note for this session")
    async def clockin(self, interaction: discord.Interaction, note: str = None):
        allowed, message = await _can_clock_in(self.db, str(interaction.guild_id))
        if not allowed:
            await interaction.response.send_message(message, ephemeral=True)
            return

        result = await self.db.clock_in(
            str(interaction.guild_id),
            str(interaction.user.id),
            interaction.user.display_name,
            note
        )
        if not result["success"]:
            entry = result["entry"]
            clock_in_time = entry["clock_in"].replace("T", " ")[:16]
            await interaction.response.send_message(
                f"⚠️ You're already clocked in since `{clock_in_time} UTC`.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="✅ Clocked In",
            color=0x57F287,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Employee", value=interaction.user.mention)
        embed.add_field(name="Started", value=f"`{result['clock_in'].replace('T',' ')[:16]} UTC`")
        if note:
            embed.add_field(name="Note", value=note, inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clockout", description="Clock out to end your work session")
    async def clockout(self, interaction: discord.Interaction):
        result = await self.db.clock_out(
            str(interaction.guild_id),
            str(interaction.user.id)
        )
        if not result["success"]:
            await interaction.response.send_message("⚠️ You're not currently clocked in.", ephemeral=True)
            return
        
        break_minutes = await self.db.get_break_minutes_for_entry(result["entry_id"])
        config = await self.db.get_overtime_config(str(interaction.guild_id))
        daily_mins = config["daily_hours"] * 60
        net_minutes = max(0, result["duration_minutes"] - break_minutes)
        overtime = max(0, net_minutes - daily_mins)
        
        embed = discord.Embed(title="🔴 Clocked Out", color=0xED4245, timestamp=datetime.utcnow())
        embed.add_field(name="Gross Duration", value=f"`{fmt_duration(result['duration_minutes'])}`", inline=True)
        embed.add_field(name="Break Time", value=f"`{fmt_duration(break_minutes)}`", inline=True)
        embed.add_field(name="Net Duration", value=f"**{fmt_duration(net_minutes)}**", inline=True)
        embed.add_field(name="Clock In", value=f"`{result['clock_in'].replace('T',' ')[:16]} UTC`", inline=True)
        embed.add_field(name="Clock Out", value=f"`{result['clock_out'].replace('T',' ')[:16]} UTC`", inline=True)
        if overtime > 0:
            embed.add_field(name="⚡ Overtime This Session", value=fmt_duration(int(overtime)), inline=True)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="status", description="Check your current clock status and today's hours")
    async def status(self, interaction: discord.Interaction):
        await _send_status(interaction, self.db)

    @app_commands.command(name="clockpanel", description="Post a clock in/out button panel (Admin)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clock_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⏱️ HR Time Clock",
            description=(
                "Use the buttons below to clock in or out.\n\n"
                "🟢 **Clock In** — Start your work session\n"
                "🔴 **Clock Out** — End your work session\n"
                "📊 **My Status** — Check your current hours"
            ),
            color=0x5865F2
        )
        embed.set_footer(text="HR Bot • Time Tracker")
        view = ClockPanel(self.db)
        try:
            if interaction.channel is None:
                raise RuntimeError("Missing channel")
            await interaction.channel.send(embed=embed, view=view)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have access to post in this channel. Move this command to a channel the bot can see and send messages in.",
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ Failed to post the panel: {e}",
                ephemeral=True
            )
            return
        except RuntimeError:
            await interaction.response.send_message(
                "❌ I couldn't find a text channel to post the panel into.",
                ephemeral=True
            )
            return

        await interaction.response.send_message("✅ Clock panel posted!", ephemeral=True)


async def setup(bot: commands.Bot):
    # bot.db is already initialized in main() before cogs load
    cog = TimeClock(bot)
    await bot.add_cog(cog)
    # Re-register persistent view so buttons survive restarts
    bot.add_view(ClockPanel(bot.db))
