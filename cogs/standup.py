"""
Standup Cog
/standup add, /standup list, /standup delete, /standup toggle
Supports scheduled standup announcements with temporary voice rooms
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("Standup")

standup_group = app_commands.Group(name="standup", description="Recurring standup meeting management")


async def _get_guild_timezone(bot, guild_id: str) -> tuple[ZoneInfo, str]:
    config = await bot.db.get_guild_config(guild_id)
    timezone_name = config.get("timezone") or "UTC"
    try:
        return ZoneInfo(timezone_name), timezone_name
    except Exception:
        return ZoneInfo("UTC"), "UTC"


class Standup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


@standup_group.command(name="add", description="Add a recurring standup")
@app_commands.describe(
    name="Standup name (e.g. 'Morning Standup')",
    channel="Channel to post in",
    times="Time(s) in HH:MM server timezone format, comma-separated for multiple (e.g. '09:00' or '09:00,14:00')",
    message="Optional short text shown above the meeting links",
    ping_role="Optional role to ping",
    meeting_url="Optional external meeting URL (Google Meet, Zoom, etc.)",
    voice_duration_minutes="How long the temporary voice room should stay available"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def standup_add(
    interaction: discord.Interaction,
    name: str,
    channel: discord.TextChannel,
    times: str,
    message: str = "Standup is live. Join the meeting below.",
    ping_role: discord.Role = None,
    meeting_url: str = None,
    voice_duration_minutes: app_commands.Range[int, 5, 180] = 20,
):
    db = interaction.client.db
    _, timezone_name = await _get_guild_timezone(interaction.client, str(interaction.guild_id))

    time_list = [t.strip() for t in times.split(",")]
    for t in time_list:
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            await interaction.response.send_message(
                f"❌ Invalid time format: `{t}`. Use HH:MM in your configured server timezone (`{timezone_name}`), e.g. `09:00`",
                ephemeral=True
            )
            return

    standup_id = await db.add_standup(
        guild_id=str(interaction.guild_id),
        channel_id=str(channel.id),
        name=name,
        cron_time=times,
        message=message,
        ping_role=str(ping_role.id) if ping_role else None,
        meeting_url=meeting_url.strip() if meeting_url else None,
        voice_duration_minutes=int(voice_duration_minutes),
    )

    embed = discord.Embed(
        title="✅ Standup Scheduled",
        color=0x57F287,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Name", value=name, inline=True)
    embed.add_field(name="ID", value=f"#{standup_id}", inline=True)
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name=f"Times ({timezone_name})", value="\n".join(f"`{t}`" for t in time_list), inline=True)
    embed.add_field(name="Ping Role", value=ping_role.mention if ping_role else "None", inline=True)
    embed.add_field(name="Voice Room", value=f"`{voice_duration_minutes} min temporary room`", inline=True)
    embed.add_field(name="Meeting URL", value=meeting_url if meeting_url else "Discord voice only", inline=False)
    embed.add_field(name="Message Preview", value=message[:200], inline=False)

    await interaction.response.send_message(embed=embed)


@standup_group.command(name="list", description="List all standup schedules")
async def standup_list(interaction: discord.Interaction):
    db = interaction.client.db
    standups = await db.get_standups(str(interaction.guild_id), active_only=False)
    tz, timezone_name = await _get_guild_timezone(interaction.client, str(interaction.guild_id))

    if not standups:
        await interaction.response.send_message(
            "No standups configured. Use `/standup add` to create one.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"📅 Standup Schedules ({len(standups)})",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )

    for s in standups:
        status = "🟢 Active" if s["active"] else "🔴 Paused"
        if s.get("last_sent"):
            try:
                last = datetime.fromisoformat(s["last_sent"]).astimezone(tz).strftime("%Y-%m-%d %H:%M")
            except Exception:
                last = s["last_sent"][:16].replace("T", " ")
        else:
            last = "Never"
        try:
            ch = interaction.guild.get_channel(int(s["channel_id"]))
            ch_mention = ch.mention if ch else f"<#{s['channel_id']}>"
        except Exception:
            ch_mention = f"<#{s['channel_id']}>"

        embed.add_field(
            name=f"#{s['id']} — {s['name']} ({status})",
            value=(
                f"📌 {ch_mention}\n"
                f"⏰ `{s['cron_time']} {timezone_name}`\n"
                f"🎙️ Temp room: `{int(s.get('voice_duration_minutes') or 20)} min`\n"
                f"🔗 Meeting URL: {s.get('meeting_url') or 'Discord voice only'}\n"
                f"📨 Last sent: `{last}`"
            ),
            inline=True
        )

    await interaction.response.send_message(embed=embed)


@standup_group.command(name="delete", description="Delete a standup schedule")
@app_commands.describe(standup_id="The standup ID (from /standup list)")
@app_commands.checks.has_permissions(manage_guild=True)
async def standup_delete(interaction: discord.Interaction, standup_id: int):
    await interaction.client.db.delete_standup(standup_id)
    await interaction.response.send_message(f"✅ Standup `#{standup_id}` deleted.", ephemeral=True)


@standup_group.command(name="pause", description="Pause or resume a standup")
@app_commands.describe(
    standup_id="The standup ID (from /standup list)",
    active="True to resume, False to pause"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def standup_toggle(interaction: discord.Interaction, standup_id: int, active: bool):
    await interaction.client.db.toggle_standup(standup_id, active)
    state = "▶️ resumed" if active else "⏸️ paused"
    await interaction.response.send_message(f"Standup `#{standup_id}` {state}.", ephemeral=True)


@standup_group.command(name="test", description="Test-send a standup right now")
@app_commands.describe(standup_id="The standup ID")
@app_commands.checks.has_permissions(manage_guild=True)
async def standup_test(interaction: discord.Interaction, standup_id: int):
    db = interaction.client.db
    standups = await db.get_standups(str(interaction.guild_id), active_only=False)
    s = next((x for x in standups if x["id"] == standup_id), None)

    if not s:
        await interaction.response.send_message("❌ Standup not found.", ephemeral=True)
        return

    scheduler = getattr(interaction.client, "standup_scheduler", None)
    if scheduler is None:
        await interaction.response.send_message("❌ Standup scheduler is unavailable.", ephemeral=True)
        return

    await scheduler._send_standup(s, update_last_sent=False)
    await interaction.response.send_message("✅ Test standup sent with a temporary voice room.", ephemeral=True)


async def setup(bot):
    cog = Standup(bot)
    bot.tree.add_command(standup_group)
    await bot.add_cog(cog)
