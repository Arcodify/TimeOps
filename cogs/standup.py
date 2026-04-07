"""
Standup Cog
/standup add, /standup list, /standup delete, /standup toggle
Supports multiple standups per guild, multiple fire times per standup
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime

log = logging.getLogger("Standup")

standup_group = app_commands.Group(name="standup", description="Recurring standup meeting management")


class Standup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


@standup_group.command(name="add", description="Add a recurring standup")
@app_commands.describe(
    name="Standup name (e.g. 'Morning Standup')",
    channel="Channel to post in",
    times="Time(s) in HH:MM UTC format, comma-separated for multiple (e.g. '09:00' or '09:00,14:00')",
    message="The message to post",
    ping_role="Optional role to ping"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def standup_add(
    interaction: discord.Interaction,
    name: str,
    channel: discord.TextChannel,
    times: str,
    message: str,
    ping_role: discord.Role = None
):
    db = interaction.client.db

    time_list = [t.strip() for t in times.split(",")]
    for t in time_list:
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            await interaction.response.send_message(
                f"❌ Invalid time format: `{t}`. Use HH:MM (24h UTC), e.g. `09:00`",
                ephemeral=True
            )
            return

    standup_id = await db.add_standup(
        guild_id=str(interaction.guild_id),
        channel_id=str(channel.id),
        name=name,
        cron_time=times,
        message=message,
        ping_role=str(ping_role.id) if ping_role else None
    )

    embed = discord.Embed(
        title="✅ Standup Scheduled",
        color=0x57F287,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Name", value=name, inline=True)
    embed.add_field(name="ID", value=f"#{standup_id}", inline=True)
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Times (UTC)", value="\n".join(f"`{t}`" for t in time_list), inline=True)
    embed.add_field(name="Ping Role", value=ping_role.mention if ping_role else "None", inline=True)
    embed.add_field(name="Message Preview", value=message[:200], inline=False)

    await interaction.response.send_message(embed=embed)


@standup_group.command(name="list", description="List all standup schedules")
async def standup_list(interaction: discord.Interaction):
    db = interaction.client.db
    standups = await db.get_standups(str(interaction.guild_id), active_only=False)

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
        last = s["last_sent"][:16].replace("T", " ") if s.get("last_sent") else "Never"
        try:
            ch = interaction.guild.get_channel(int(s["channel_id"]))
            ch_mention = ch.mention if ch else f"<#{s['channel_id']}>"
        except Exception:
            ch_mention = f"<#{s['channel_id']}>"

        embed.add_field(
            name=f"#{s['id']} — {s['name']} ({status})",
            value=(
                f"📌 {ch_mention}\n"
                f"⏰ `{s['cron_time']} UTC`\n"
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

    channel = interaction.guild.get_channel(int(s["channel_id"]))
    if not channel:
        await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"📢 {s['name']} (Test)",
        description=s["message"],
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text="HR Bot • Standup Test")

    content = None
    if s.get("ping_role"):
        content = f"<@&{s['ping_role']}>"

    await channel.send(content=content, embed=embed)
    await interaction.response.send_message(f"✅ Test standup sent to {channel.mention}!", ephemeral=True)


async def setup(bot):
    cog = Standup(bot)
    bot.tree.add_command(standup_group)
    await bot.add_cog(cog)
