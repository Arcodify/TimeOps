"""
Breaks Cog
/break start, /break end, /break status
Tracks paid/unpaid break time within a session.
Break time is subtracted from total worked hours.
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime

log = logging.getLogger("Breaks")


def fmt_duration(minutes: int) -> str:
    if not minutes:
        return "0h 00m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


break_group = app_commands.Group(name="break", description="Break time tracking")


class Breaks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        # Ensure breaks table exists
        import aiosqlite
        async with aiosqlite.connect(self.db.path) as db:
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
                    break_type  TEXT DEFAULT 'break'
                )
            """)
            await db.commit()

    async def _get_active_break(self, guild_id, user_id):
        import aiosqlite
        async with aiosqlite.connect(self.db.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM break_entries WHERE guild_id=? AND user_id=? AND break_end IS NULL",
                (guild_id, user_id)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def _start_break(self, guild_id, user_id, username, break_type, entry_id):
        import aiosqlite
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db.path) as db:
            cursor = await db.execute(
                "INSERT INTO break_entries (guild_id, user_id, username, time_entry_id, break_start, break_type) "
                "VALUES (?,?,?,?,?,?)",
                (guild_id, user_id, username, entry_id, now, break_type)
            )
            await db.commit()
            return cursor.lastrowid, now

    async def _end_break(self, guild_id, user_id):
        import aiosqlite
        active = await self._get_active_break(guild_id, user_id)
        if not active:
            return None
        now = datetime.utcnow()
        start = datetime.fromisoformat(active["break_start"])
        duration = int((now - start).total_seconds() / 60)
        async with aiosqlite.connect(self.db.path) as db:
            await db.execute(
                "UPDATE break_entries SET break_end=?, duration_minutes=? WHERE id=?",
                (now.isoformat(), duration, active["id"])
            )
            await db.commit()
        return {**active, "break_end": now.isoformat(), "duration_minutes": duration}

    async def _get_session_breaks(self, guild_id, user_id, entry_id):
        import aiosqlite
        async with aiosqlite.connect(self.db.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM break_entries WHERE guild_id=? AND user_id=? AND time_entry_id=?",
                (guild_id, user_id, entry_id)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    @break_group.command(name="start", description="Start a break")
    @app_commands.describe(break_type="Type of break")
    @app_commands.choices(break_type=[
        app_commands.Choice(name="Regular Break", value="break"),
        app_commands.Choice(name="Lunch", value="lunch"),
        app_commands.Choice(name="Personal", value="personal"),
    ])
    async def break_start(self, interaction: discord.Interaction, break_type: str = "break"):
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)

        # Must be clocked in
        active_entry = await self.db.get_active_entry(guild_id, user_id)
        if not active_entry:
            await interaction.response.send_message(
                "⚠️ You must be clocked in to start a break. Use `/clockin` first.",
                ephemeral=True
            )
            return

        # Can't already be on a break
        active_break = await self._get_active_break(guild_id, user_id)
        if active_break:
            started = active_break["break_start"].replace("T", " ")[:16]
            await interaction.response.send_message(
                f"⚠️ You're already on a **{active_break['break_type']}** since `{started} UTC`.\n"
                f"Use `/break end` to end it.",
                ephemeral=True
            )
            return

        _, now = await self._start_break(
            guild_id, user_id, interaction.user.display_name,
            break_type, active_entry["id"]
        )

        icons = {"break": "☕", "lunch": "🍽️", "personal": "🚶"}
        icon = icons.get(break_type, "⏸️")

        embed = discord.Embed(
            title=f"{icon} Break Started",
            description=f"Enjoy your {break_type}!",
            color=0xFEE75C,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Type", value=break_type.title(), inline=True)
        embed.add_field(name="Started", value=f"`{now.replace('T',' ')[:16]} UTC`", inline=True)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @break_group.command(name="end", description="End your current break")
    async def break_end(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)

        result = await self._end_break(guild_id, user_id)
        if not result:
            await interaction.response.send_message("⚠️ You're not on a break.", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ Break Ended",
            color=0x57F287,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Break Type", value=result["break_type"].title(), inline=True)
        embed.add_field(name="Duration", value=f"**{fmt_duration(result['duration_minutes'])}**", inline=True)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @break_group.command(name="status", description="View your break history for the current session")
    async def break_status(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        user_id = str(interaction.user.id)

        active_entry = await self.db.get_active_entry(guild_id, user_id)
        active_break = await self._get_active_break(guild_id, user_id)

        embed = discord.Embed(
            title=f"☕ Break Status — {interaction.user.display_name}",
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )

        if active_break:
            elapsed = int((datetime.utcnow() - datetime.fromisoformat(active_break["break_start"])).total_seconds() / 60)
            embed.add_field(
                name="🟡 Currently On Break",
                value=f"**{active_break['break_type'].title()}** — {fmt_duration(elapsed)} elapsed",
                inline=False
            )

        if active_entry:
            breaks = await self._get_session_breaks(guild_id, user_id, active_entry["id"])
            completed = [b for b in breaks if b["break_end"]]
            total_break_mins = sum(b["duration_minutes"] or 0 for b in completed)
            embed.add_field(name="Breaks This Session", value=str(len(completed)), inline=True)
            embed.add_field(name="Total Break Time", value=fmt_duration(total_break_mins), inline=True)

            if completed:
                history = "\n".join(
                    f"• {b['break_type'].title()}: {fmt_duration(b['duration_minutes'])} "
                    f"({b['break_start'][11:16]}–{b['break_end'][11:16]} UTC)"
                    for b in completed[-5:]
                )
                embed.add_field(name="Recent Breaks", value=history, inline=False)
        else:
            embed.add_field(name="Status", value="Not clocked in", inline=False)

        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = Breaks(bot)
    bot.tree.add_command(break_group)
    await bot.add_cog(cog)
