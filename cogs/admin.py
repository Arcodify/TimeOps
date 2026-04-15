"""
Admin Cog
/hrsetup — configure activity log channel and admin role
/hrconfig configure — configure activity log channel, admin role, present role, on-break role
/timezone set — configure guild timezone once for all bot features
/overtime config — set daily/weekly hours and auto-out limit
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
import re
from typing import Optional
from zoneinfo import ZoneInfo
from database import normalize_blocked_weekdays, parse_hhmm

log = logging.getLogger("Admin")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

admin_group = app_commands.Group(name="hrconfig", description="HR Bot server configuration")
overtime_group = app_commands.Group(name="overtime", description="Overtime configuration")
timezone_group = app_commands.Group(name="timezone", description="Guild timezone configuration")


def _mode_label(value: str) -> str:
    return "Time Shift" if (value or "").strip().lower() == "time_shift" else "Overtime"


def _weekday_labels(blocked_weekdays: str) -> str:
    labels = []
    for item in normalize_blocked_weekdays(blocked_weekdays).split(","):
        if not item:
            continue
        idx = int(item)
        if 0 <= idx <= 6:
            labels.append(WEEKDAY_NAMES[idx])
    return ", ".join(labels) if labels else "Saturday"


def _parse_snowflake(value):
    if value is None:
        return None

    if isinstance(value, int):
        return value

    text = str(value).strip()
    match = (
        re.fullmatch(r"<#(\d+)>", text)
        or re.fullmatch(r"<@&(\d+)>", text)
        or re.fullmatch(r"(\d+)", text)
    )
    return int(match.group(1)) if match else None


async def _resolve_channel_input(guild: discord.Guild, value):
    channel_id = _parse_snowflake(value)
    if channel_id is None:
        return None

    if hasattr(guild, "get_channel_or_thread"):
        channel = guild.get_channel_or_thread(channel_id)
        if channel:
            return channel

    channel = guild.get_channel(channel_id)
    if channel:
        return channel

    try:
        return await guild.fetch_channel(channel_id)
    except Exception:
        return None


def _resolve_role_input(guild: discord.Guild, value):
    role_id = _parse_snowflake(value)
    if role_id is None:
        return None
    return guild.get_role(role_id)


async def _resolve_text_channel(interaction: discord.Interaction, channel_input):
    if channel_input is None:
        return None

    if isinstance(channel_input, (discord.TextChannel, discord.Thread)):
        return channel_input

    channel = None
    if hasattr(channel_input, "resolve"):
        channel = channel_input.resolve()
    if channel is None and hasattr(channel_input, "fetch"):
        try:
            channel = await channel_input.fetch()
        except Exception:
            channel = None

    if channel is None:
        await interaction.response.send_message(
            "❌ I could not resolve that channel. Please pick a channel the bot can access.",
            ephemeral=True,
        )
        return None

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message(
            "❌ Activity logging needs a text channel or thread. Forum, voice, stage, and category channels are not supported.",
            ephemeral=True,
        )
        return None

    return channel


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @commands.command(name="hrsetup")
    @commands.has_permissions(administrator=True)
    async def hrsetup_prefix(
        self,
        ctx: commands.Context,
        activity_log_channel: str = None,
        admin_role: str = None,
    ):
        activity_obj = await _resolve_channel_input(ctx.guild, activity_log_channel) if activity_log_channel else None
        role_obj = _resolve_role_input(ctx.guild, admin_role) if admin_role else None

        if activity_log_channel and activity_obj is None:
            await ctx.send("❌ I could not resolve that activity log channel. Use a channel mention like `#hr-activity` or a channel ID.")
            return

        if admin_role and role_obj is None:
            await ctx.send("❌ I could not resolve that admin role. Use a role mention like `@Managers` or a role ID.")
            return

        await self.db.set_guild_config(
            str(ctx.guild.id),
            activity_log_channel_id=str(activity_obj.id) if activity_obj else None,
            admin_role_id=str(role_obj.id) if role_obj else None,
        )

        embed = discord.Embed(title="⚙️ HR Bot Configured", color=0x57F287)
        embed.add_field(name="Activity Log Channel", value=activity_obj.mention if activity_obj else "Not set", inline=True)
        embed.add_field(name="Admin Role", value=role_obj.mention if role_obj else "Not set", inline=True)
        await ctx.send(embed=embed)


@admin_group.command(name="configure", description="Configure HR bot for this server")
@app_commands.describe(
    activity_log_channel="Text channel or thread for attendance and leave activity logs",
    admin_role="Role that can approve leave and view admin reports",
    present_role="Role assigned while someone is clocked in",
    on_break_role="Role assigned while someone is on a manual break"
)
@app_commands.checks.has_permissions(administrator=True)
async def hrconfigure(
    interaction: discord.Interaction,
    activity_log_channel: discord.app_commands.AppCommandChannel = None,
    admin_role: discord.Role = None,
    present_role: discord.Role = None,
    on_break_role: discord.Role = None,
):
    db = interaction.client.db
    activity_log_channel = await _resolve_text_channel(interaction, activity_log_channel)
    if activity_log_channel is None and interaction.response.is_done():
        return

    await db.set_guild_config(
        str(interaction.guild_id),
        activity_log_channel_id=str(activity_log_channel.id) if activity_log_channel else None,
        admin_role_id=str(admin_role.id) if admin_role else None,
        present_role_id=str(present_role.id) if present_role else None,
        on_break_role_id=str(on_break_role.id) if on_break_role else None,
    )

    embed = discord.Embed(title="⚙️ HR Bot Configured", color=0x57F287)
    embed.add_field(name="Activity Log Channel", value=activity_log_channel.mention if activity_log_channel else "Not set", inline=True)
    embed.add_field(name="Admin Role", value=admin_role.mention if admin_role else "Not set", inline=True)
    embed.add_field(name="Present Role", value=present_role.mention if present_role else "Not set", inline=True)
    embed.add_field(name="On Break Role", value=on_break_role.mention if on_break_role else "Not set", inline=True)
    embed.set_footer(text="Use /timezone set and /overtime config for time behavior")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="view", description="View current HR bot configuration")
@app_commands.checks.has_permissions(manage_guild=True)
async def hrview(interaction: discord.Interaction):
    db = interaction.client.db
    config = await db.get_guild_config(str(interaction.guild_id))
    ot_config = await db.get_overtime_config(str(interaction.guild_id))
    work_rules = await db.get_work_rules(str(interaction.guild_id))

    embed = discord.Embed(title="⚙️ HR Bot Configuration", color=0x5865F2)

    activity_ch = config.get("activity_log_channel_id") or config.get("leave_channel_id")
    embed.add_field(
        name="Activity Log Channel",
        value=f"<#{activity_ch}>" if activity_ch else "Not configured",
        inline=True
    )

    admin_r = config.get("admin_role_id")
    embed.add_field(
        name="Admin Role",
        value=f"<@&{admin_r}>" if admin_r else "Not configured",
        inline=True
    )

    present_r = config.get("present_role_id")
    embed.add_field(
        name="Present Role",
        value=f"<@&{present_r}>" if present_r else "Not configured",
        inline=True
    )

    on_break_r = config.get("on_break_role_id")
    embed.add_field(
        name="On Break Role",
        value=f"<@&{on_break_r}>" if on_break_r else "Not configured",
        inline=True
    )

    embed.add_field(name="Timezone", value=f"`{config.get('timezone', 'UTC')}`", inline=True)
    embed.add_field(name="Time Mode", value=f"`{_mode_label(ot_config.get('mode'))}`", inline=True)
    if (ot_config.get("mode") or "overtime") == "time_shift":
        embed.add_field(
            name="Clock In Opens",
            value=f"`{ot_config.get('shift_clock_in_time') or 'Not configured'}`",
            inline=True,
        )
        embed.add_field(
            name="Clock Out From",
            value=f"`{ot_config.get('shift_clock_out_time') or 'Not configured'}`",
            inline=True,
        )
        embed.add_field(
            name="Default Clock-Out",
            value=f"`{ot_config.get('default_clock_out_time') or 'Not configured'}`",
            inline=True,
        )
    else:
        embed.add_field(name="Daily Work Hours", value=f"`{ot_config['daily_hours']}h`", inline=True)
        embed.add_field(name="Weekly Work Hours", value=f"`{ot_config['weekly_hours']}h`", inline=True)
        embed.add_field(name="Auto Clock-Out After", value=f"`{ot_config['auto_out_hours']}h`", inline=True)
    embed.add_field(name="Default Break", value=f"`{work_rules['default_break_minutes']} min`", inline=True)
    embed.add_field(name="Blocked Clock-In Days", value=_weekday_labels(work_rules.get("blocked_weekdays")), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="reset", description="Delete all HR bot data and setup for this server")
@app_commands.describe(confirm="Type DELETE to confirm wiping this server's HR bot data")
@app_commands.checks.has_permissions(administrator=True)
async def hrreset(interaction: discord.Interaction, confirm: str):
    if confirm.strip() != "DELETE":
        await interaction.response.send_message(
            "❌ Reset cancelled. Type exactly `DELETE` in the confirm field to wipe this server's HR bot data.",
            ephemeral=True,
        )
        return

    scheduler = getattr(interaction.client, "standup_scheduler", None)
    if scheduler is not None:
        stale_ids = []
        for channel_id, meta in list(scheduler.active_voice_rooms.items()):
            if str(meta.get("guild_id")) != str(interaction.guild_id):
                continue
            channel = interaction.guild.get_channel(channel_id)
            if channel is not None:
                try:
                    await channel.delete(reason="HR bot server reset")
                except Exception:
                    pass
            stale_ids.append(channel_id)
        for channel_id in stale_ids:
            scheduler.active_voice_rooms.pop(channel_id, None)

    await interaction.client.db.reset_guild_data(str(interaction.guild_id))

    embed = discord.Embed(title="🧨 HR Bot Server Reset", color=0xED4245)
    embed.description = "All stored HR bot setup and data for this server have been deleted."
    embed.add_field(
        name="Removed",
        value=(
            "Config, time entries, leave requests, standups, break logs, scheduled breaks, "
            "work updates, reminders, and holidays."
        ),
        inline=False,
    )
    embed.add_field(
        name="Manual Cleanup Still Needed",
        value="Previously posted panel messages in channels are not tracked, so delete old panels manually if you want them gone.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@timezone_group.command(name="set", description="Set the timezone used across this server")
@app_commands.describe(value="Timezone name like UTC, US/Eastern, Europe/London, Asia/Kathmandu")
@app_commands.checks.has_permissions(manage_guild=True)
async def timezone_set(interaction: discord.Interaction, value: str):
    timezone_name = value.strip()
    try:
        ZoneInfo(timezone_name)
    except Exception:
        await interaction.response.send_message(
            "❌ Invalid timezone. Use an IANA timezone like `UTC`, `US/Eastern`, `Europe/London`, or `Asia/Kathmandu`.",
            ephemeral=True,
        )
        return

    await interaction.client.db.set_guild_config(str(interaction.guild_id), timezone=timezone_name)

    embed = discord.Embed(title="🕒 Timezone Updated", color=0x57F287)
    embed.add_field(name="Timezone", value=f"`{timezone_name}`", inline=True)
    embed.add_field(
        name="Used By",
        value="Standups, breaks, blocked clock-in days, and other server time features.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@timezone_group.command(name="view", description="View the timezone used across this server")
async def timezone_view(interaction: discord.Interaction):
    config = await interaction.client.db.get_guild_config(str(interaction.guild_id))
    timezone_name = config.get("timezone") or "UTC"
    embed = discord.Embed(title="🕒 Server Timezone", color=0x5865F2)
    embed.add_field(name="Timezone", value=f"`{timezone_name}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@overtime_group.command(name="config", description="Configure overtime mode or fixed time-shift mode")
@app_commands.describe(
    mode="Choose whether the server uses overtime rules or fixed time-shift rules",
    daily_hours="Standard work hours per day for overtime mode",
    weekly_hours="Standard work hours per week for overtime mode",
    auto_out_hours="Auto clock-out after this many hours in overtime mode (0 to disable)",
    shift_clock_in_time="Time-shift clock-in opening time in HH:MM server timezone",
    shift_clock_out_time="Time-shift clock-out start time in HH:MM server timezone",
    default_clock_out_time="Time-shift default clock-out time in HH:MM server timezone",
    default_break_minutes="Default break allowance per workday in minutes when no scheduled break windows exist",
    blocked_weekdays="Comma-separated blocked clock-in days"
)
@app_commands.choices(mode=[
    app_commands.Choice(name="Overtime", value="overtime"),
    app_commands.Choice(name="Time Shift", value="time_shift"),
])
@app_commands.checks.has_permissions(manage_guild=True)
async def overtime_config(
    interaction: discord.Interaction,
    mode: Optional[str] = None,
    daily_hours: Optional[float] = None,
    weekly_hours: Optional[float] = None,
    auto_out_hours: Optional[float] = None,
    shift_clock_in_time: Optional[str] = None,
    shift_clock_out_time: Optional[str] = None,
    default_clock_out_time: Optional[str] = None,
    default_break_minutes: Optional[float] = None,
    blocked_weekdays: Optional[str] = None,
):
    db = interaction.client.db
    guild_id = str(interaction.guild_id)
    current_config = await db.get_overtime_config(guild_id)
    current_rules = await db.get_work_rules(guild_id)
    previous_mode = (current_config.get("mode") or "overtime").strip().lower()

    selected_mode = (mode or current_config.get("mode") or "overtime").strip().lower()
    if selected_mode not in {"overtime", "time_shift"}:
        await interaction.response.send_message("❌ Invalid mode.", ephemeral=True)
        return

    if default_break_minutes is None:
        default_break_minutes = float(current_rules.get("default_break_minutes") or 0)
    if blocked_weekdays is None:
        blocked_weekdays = current_rules.get("blocked_weekdays") or "sat"
    if default_break_minutes < 0:
        await interaction.response.send_message("❌ Default break minutes cannot be negative.", ephemeral=True)
        return

    payload = {"mode": selected_mode}
    if selected_mode == "time_shift":
        shift_clock_in_time = (shift_clock_in_time or current_config.get("shift_clock_in_time") or "").strip()
        shift_clock_out_time = (shift_clock_out_time or current_config.get("shift_clock_out_time") or "").strip()
        default_clock_out_time = (default_clock_out_time or current_config.get("default_clock_out_time") or "").strip()
        if not all((shift_clock_in_time, shift_clock_out_time, default_clock_out_time)):
            await interaction.response.send_message(
                "❌ Time-shift mode needs `shift_clock_in_time`, `shift_clock_out_time`, and `default_clock_out_time` in HH:MM format.",
                ephemeral=True,
            )
            return
        try:
            start_time = parse_hhmm(shift_clock_in_time)
            end_time = parse_hhmm(shift_clock_out_time)
            default_out_time = parse_hhmm(default_clock_out_time)
        except ValueError:
            await interaction.response.send_message("❌ Time-shift times must use HH:MM format.", ephemeral=True)
            return
        if end_time <= start_time:
            await interaction.response.send_message(
                "❌ `shift_clock_out_time` must be later than `shift_clock_in_time`.",
                ephemeral=True,
            )
            return
        if default_out_time < end_time:
            await interaction.response.send_message(
                "❌ `default_clock_out_time` must be the same as or later than `shift_clock_out_time`.",
                ephemeral=True,
            )
            return
        payload.update(
            {
                "shift_clock_in_time": shift_clock_in_time,
                "shift_clock_out_time": shift_clock_out_time,
                "default_clock_out_time": default_clock_out_time,
            }
        )
    else:
        daily_hours = daily_hours if daily_hours is not None else float(current_config.get("daily_hours") or 8.0)
        weekly_hours = weekly_hours if weekly_hours is not None else float(current_config.get("weekly_hours") or 40.0)
        auto_out_hours = auto_out_hours if auto_out_hours is not None else float(current_config.get("auto_out_hours") or 12.0)
        if daily_hours <= 0 or weekly_hours <= 0:
            await interaction.response.send_message("❌ Hours must be positive.", ephemeral=True)
            return
        payload.update(
            {
                "daily_hours": daily_hours,
                "weekly_hours": weekly_hours,
                "auto_out_hours": auto_out_hours,
            }
        )

    await db.set_overtime_config(guild_id, **payload)
    await db.set_work_rules(
        guild_id,
        default_break_minutes=default_break_minutes,
        blocked_weekdays=blocked_weekdays
    )
    cleared_pending_prompts = 0
    if previous_mode != selected_mode:
        cleared_pending_prompts = await db.clear_pending_work_updates(guild_id)

    embed = discord.Embed(title="⚙️ Time Config Updated", color=0x57F287)
    embed.add_field(name="Mode", value=f"`{_mode_label(selected_mode)}`", inline=True)
    if selected_mode == "time_shift":
        embed.add_field(name="Clock In Opens", value=f"`{shift_clock_in_time}`", inline=True)
        embed.add_field(name="Clock Out From", value=f"`{shift_clock_out_time}`", inline=True)
        embed.add_field(name="Default Clock-Out", value=f"`{default_clock_out_time}`", inline=True)
    else:
        embed.add_field(name="Daily Hours", value=f"`{daily_hours}h`", inline=True)
        embed.add_field(name="Weekly Hours", value=f"`{weekly_hours}h`", inline=True)
        embed.add_field(
            name="Auto Clock-Out",
            value=f"`{auto_out_hours}h`" if auto_out_hours > 0 else "Disabled",
            inline=True
        )
    if cleared_pending_prompts:
        embed.add_field(
            name="Pending Work Updates",
            value=f"Cleared `{cleared_pending_prompts}` stale prompt(s) from the previous mode.",
            inline=False,
        )
    embed.add_field(name="Default Break", value=f"`{default_break_minutes} min`", inline=True)
    embed.add_field(name="Blocked Clock-In Days", value=_weekday_labels(blocked_weekdays), inline=True)
    if selected_mode == "time_shift":
        embed.set_footer(text="Clock-in and clock-out are enforced using the configured server timezone.")
    else:
        embed.set_footer(text="Overtime = worked hours − daily_hours × days_worked")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = Admin(bot)
    bot.tree.add_command(admin_group)
    bot.tree.add_command(overtime_group)
    bot.tree.add_command(timezone_group)
    await bot.add_cog(cog)
