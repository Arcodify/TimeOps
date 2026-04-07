"""
Holidays Cog
/holiday add, /holiday list, /holiday delete
Company-wide holidays stored per guild.
Leave requests warn if they overlap with a holiday.
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import logging
from datetime import datetime, timedelta

log = logging.getLogger("Holidays")

holiday_group = app_commands.Group(name="holiday", description="Company holiday management")


class Holidays(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    async def cog_load(self):
        async with aiosqlite.connect(self.db.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS holidays (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id    TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    date        TEXT NOT NULL,
                    recurring   INTEGER DEFAULT 0,
                    UNIQUE(guild_id, date)
                )
            """)
            await db.commit()



async def _get_holidays(db_path: str, guild_id: str, upcoming_only: bool = False):
    query = "SELECT * FROM holidays WHERE guild_id=?"
    params = [guild_id]
    if upcoming_only:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        query += " AND date >= ?"
        params.append(today)
    query += " ORDER BY date ASC"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@holiday_group.command(name="add", description="Add a company holiday")
@app_commands.describe(
    name="Holiday name (e.g. 'Christmas Day')",
    date="Date in YYYY-MM-DD format",
    recurring="Repeat every year on the same date"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def holiday_add(
    interaction: discord.Interaction,
    name: str,
    date: str,
    recurring: bool = False
):
    db_path = interaction.client.db.path

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        await interaction.response.send_message("❌ Invalid date format. Use YYYY-MM-DD.", ephemeral=True)
        return

    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO holidays (guild_id, name, date, recurring) VALUES (?,?,?,?)",
                (str(interaction.guild_id), name, date, 1 if recurring else 0)
            )
            await db.commit()
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
        return

    day_name = datetime.strptime(date, "%Y-%m-%d").strftime("%A, %B %d %Y")

    embed = discord.Embed(title="🎉 Holiday Added", color=0x57F287)
    embed.add_field(name="Name", value=name, inline=True)
    embed.add_field(name="Date", value=f"`{date}` ({day_name})", inline=True)
    embed.add_field(name="Recurring", value="Yes (yearly)" if recurring else "No", inline=True)
    await interaction.response.send_message(embed=embed)


@holiday_group.command(name="list", description="List all company holidays")
@app_commands.describe(upcoming_only="Show only upcoming holidays")
async def holiday_list(interaction: discord.Interaction, upcoming_only: bool = True):
    holidays = await _get_holidays(interaction.client.db.path, str(interaction.guild_id), upcoming_only=upcoming_only)

    if not holidays:
        msg = "No upcoming holidays." if upcoming_only else "No holidays configured."
        await interaction.response.send_message(
            f"{msg}\nAdmins can add holidays with `/holiday add`.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"📅 Company Holidays {'(Upcoming)' if upcoming_only else '(All)'}",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )

    today = datetime.utcnow().strftime("%Y-%m-%d")
    for h in holidays[:20]:
        is_today = h["date"] == today
        is_past = h["date"] < today
        icon = "🔴" if is_today else ("⚫" if is_past else "🟢")
        day_name = datetime.strptime(h["date"], "%Y-%m-%d").strftime("%a, %b %d %Y")
        recurring_tag = " 🔁" if h["recurring"] else ""
        embed.add_field(
            name=f"{icon} {h['name']}{recurring_tag}",
            value=f"`{h['date']}` — {day_name}",
            inline=True
        )

    embed.set_footer(text=f"{len(holidays)} holiday(s) • 🔁 = recurring yearly")
    await interaction.response.send_message(embed=embed)


@holiday_group.command(name="delete", description="Remove a company holiday")
@app_commands.describe(date="The date of the holiday to remove (YYYY-MM-DD)")
@app_commands.checks.has_permissions(manage_guild=True)
async def holiday_delete(interaction: discord.Interaction, date: str):
    async with aiosqlite.connect(interaction.client.db.path) as db:
        result = await db.execute(
            "DELETE FROM holidays WHERE guild_id=? AND date=?",
            (str(interaction.guild_id), date)
        )
        await db.commit()
        if result.rowcount == 0:
            await interaction.response.send_message(
                f"❌ No holiday found for `{date}`.", ephemeral=True
            )
            return

    await interaction.response.send_message(f"✅ Holiday on `{date}` removed.", ephemeral=True)


@holiday_group.command(name="upcoming", description="Show the next N holidays")
@app_commands.describe(count="How many to show (default 5)")
async def holiday_upcoming(interaction: discord.Interaction, count: int = 5):
    holidays = await _get_holidays(interaction.client.db.path, str(interaction.guild_id), upcoming_only=True)
    holidays = holidays[:max(1, min(count, 15))]

    if not holidays:
        await interaction.response.send_message("No upcoming holidays!", ephemeral=True)
        return

    lines = []
    today = datetime.utcnow().date()
    for h in holidays:
        hdate = datetime.strptime(h["date"], "%Y-%m-%d").date()
        days_away = (hdate - today).days
        if days_away == 0:
            when = "**Today!** 🎉"
        elif days_away == 1:
            when = "Tomorrow"
        else:
            when = f"in {days_away} days"
        lines.append(f"• **{h['name']}** — `{h['date']}` ({when})")

    embed = discord.Embed(
        title="📅 Upcoming Holidays",
        description="\n".join(lines),
        color=0x5865F2
    )
    await interaction.response.send_message(embed=embed)


async def setup(bot):
    cog = Holidays(bot)
    bot.tree.add_command(holiday_group)
    await bot.add_cog(cog)
