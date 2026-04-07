"""
Admin Cog
/hrsetup — configure activity log channel, admin role, timezone
/hrconfig configure — configure activity log channel, admin role, present role, on-break role, timezone
/overtime config — set daily/weekly hours and auto-out limit
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
import re
from database import normalize_blocked_weekdays

log = logging.getLogger("Admin")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

admin_group = app_commands.Group(name="hrconfig", description="HR Bot server configuration")
overtime_group = app_commands.Group(name="overtime", description="Overtime configuration")


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
        timezone: str = "UTC"
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
            timezone=timezone
        )

        embed = discord.Embed(title="⚙️ HR Bot Configured", color=0x57F287)
        embed.add_field(name="Activity Log Channel", value=activity_obj.mention if activity_obj else "Not set", inline=True)
        embed.add_field(name="Admin Role", value=role_obj.mention if role_obj else "Not set", inline=True)
        embed.add_field(name="Timezone", value=f"`{timezone}`", inline=True)
        await ctx.send(embed=embed)


@admin_group.command(name="configure", description="Configure HR bot for this server")
@app_commands.describe(
    activity_log_channel="Text channel or thread for attendance and leave activity logs",
    admin_role="Role that can approve leave and view admin reports",
    present_role="Role assigned while someone is clocked in",
    on_break_role="Role assigned while someone is on a manual break",
    timezone="Server timezone for display (e.g. UTC, US/Eastern, Asia/Kathmandu)"
)
@app_commands.checks.has_permissions(administrator=True)
async def hrconfigure(
    interaction: discord.Interaction,
    activity_log_channel: discord.app_commands.AppCommandChannel = None,
    admin_role: discord.Role = None,
    present_role: discord.Role = None,
    on_break_role: discord.Role = None,
    timezone: str = "UTC"
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
        timezone=timezone
    )

    embed = discord.Embed(title="⚙️ HR Bot Configured", color=0x57F287)
    embed.add_field(name="Activity Log Channel", value=activity_log_channel.mention if activity_log_channel else "Not set", inline=True)
    embed.add_field(name="Admin Role", value=admin_role.mention if admin_role else "Not set", inline=True)
    embed.add_field(name="Present Role", value=present_role.mention if present_role else "Not set", inline=True)
    embed.add_field(name="On Break Role", value=on_break_role.mention if on_break_role else "Not set", inline=True)
    embed.add_field(name="Timezone", value=f"`{timezone}`", inline=True)
    embed.set_footer(text="Use /hrconfig overtime to set work hour policies")
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
    embed.add_field(name="Daily Work Hours", value=f"`{ot_config['daily_hours']}h`", inline=True)
    embed.add_field(name="Weekly Work Hours", value=f"`{ot_config['weekly_hours']}h`", inline=True)
    embed.add_field(name="Auto Clock-Out After", value=f"`{ot_config['auto_out_hours']}h`", inline=True)
    embed.add_field(name="Default Break", value=f"`{work_rules['default_break_minutes']} min`", inline=True)
    embed.add_field(name="Blocked Clock-In Days", value=_weekday_labels(work_rules.get("blocked_weekdays")), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@overtime_group.command(name="config", description="Configure overtime and auto clock-out rules")
@app_commands.describe(
    daily_hours="Standard work hours per day (default 8)",
    weekly_hours="Standard work hours per week (default 40)",
    auto_out_hours="Auto clock-out after this many hours if user forgets (default 12, 0 to disable)",
    default_break_minutes="Default break allowance per workday in minutes (default 60)",
    blocked_weekdays="Comma-separated blocked clock-in days (default sat)"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def overtime_config(
    interaction: discord.Interaction,
    daily_hours: float = 8.0,
    weekly_hours: float = 40.0,
    auto_out_hours: float = 12.0,
    default_break_minutes: float = 60.0,
    blocked_weekdays: str = "sat"
):
    db = interaction.client.db

    if daily_hours <= 0 or weekly_hours <= 0:
        await interaction.response.send_message("❌ Hours must be positive.", ephemeral=True)
        return
    if default_break_minutes < 0:
        await interaction.response.send_message("❌ Default break minutes cannot be negative.", ephemeral=True)
        return

    await db.set_overtime_config(
        str(interaction.guild_id), daily_hours, weekly_hours, auto_out_hours
    )
    await db.set_work_rules(
        str(interaction.guild_id),
        default_break_minutes=default_break_minutes,
        blocked_weekdays=blocked_weekdays
    )

    embed = discord.Embed(title="⚡ Overtime Config Updated", color=0x57F287)
    embed.add_field(name="Daily Hours", value=f"`{daily_hours}h`", inline=True)
    embed.add_field(name="Weekly Hours", value=f"`{weekly_hours}h`", inline=True)
    embed.add_field(
        name="Auto Clock-Out",
        value=f"`{auto_out_hours}h`" if auto_out_hours > 0 else "Disabled",
        inline=True
    )
    embed.add_field(name="Default Break", value=f"`{default_break_minutes} min`", inline=True)
    embed.add_field(name="Blocked Clock-In Days", value=_weekday_labels(blocked_weekdays), inline=True)
    embed.set_footer(text="Overtime = worked hours − daily_hours × days_worked")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = Admin(bot)
    bot.tree.add_command(admin_group)
    bot.tree.add_command(overtime_group)
    await bot.add_cog(cog)
