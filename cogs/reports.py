"""
Reports Cog
/report timesheet, /report summary, /report overtime, /report leave
Exports CSV files and attaches them to Discord
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime
from csv_exporter import CSVExporter

log = logging.getLogger("Reports")

PERIODS = ["today", "yesterday", "week", "last_week", "month", "last_month"]

report_group = app_commands.Group(name="report", description="Generate time and leave reports")


def fmt_duration(minutes: int) -> str:
    if not minutes:
        return "0h 00m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _format_period_range(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%Y-%m-%d %H:%M UTC')} → {end.strftime('%Y-%m-%d %H:%M UTC')}"


class Reports(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


@report_group.command(name="timesheet", description="Export detailed time entries as CSV")
@app_commands.describe(
    period="Time period to export",
    member="Specific member (leave empty for all)"
)
@app_commands.choices(period=[app_commands.Choice(name=p, value=p) for p in PERIODS])
@app_commands.checks.has_permissions(manage_guild=True)
async def report_timesheet(
    interaction: discord.Interaction,
    period: str = "week",
    member: discord.Member = None
):
    db = interaction.client.db
    exporter = CSVExporter(db)
    await interaction.response.defer(ephemeral=True)

    start, end = exporter.get_period_dates(period)
    guild_id = str(interaction.guild_id)

    if member:
        entries = await db.get_entries_range(guild_id, str(member.id), start, end)
        label = f"{member.display_name}_{period}"
    else:
        entries = await db.get_all_entries_range(guild_id, start, end)
        label = f"all_{period}"

    if not entries:
        await interaction.followup.send("📭 No time entries found for that period.", ephemeral=True)
        return

    path = await exporter.export_timesheet(guild_id, label, start, end)

    embed = discord.Embed(
        title="📊 Timesheet Report",
        description=f"Period: **{period}** ({_format_period_range(start, end)})",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Entries", value=str(len(entries)), inline=True)
    embed.add_field(name="Filter", value=member.mention if member else "All employees", inline=True)

    file = discord.File(path)
    await interaction.followup.send(embed=embed, file=file, ephemeral=True)


@report_group.command(name="summary", description="Export per-employee summary with overtime as CSV")
@app_commands.describe(period="Time period to summarize")
@app_commands.choices(period=[app_commands.Choice(name=p, value=p) for p in PERIODS])
@app_commands.checks.has_permissions(manage_guild=True)
async def report_summary(interaction: discord.Interaction, period: str = "week"):
    db = interaction.client.db
    exporter = CSVExporter(db)
    await interaction.response.defer(ephemeral=True)

    start, end = exporter.get_period_dates(period)
    guild_id = str(interaction.guild_id)

    path = await exporter.export_summary(guild_id, period, start, end)

    entries = await db.get_all_entries_range(guild_id, start, end)
    user_count = len(set(e["user_id"] for e in entries))
    total_mins = 0
    for uid in set(e["user_id"] for e in entries):
        total_mins += (await db.get_user_summary(guild_id, uid, start, end))["total_minutes"]

    embed = discord.Embed(
        title="📈 Summary Report",
        description=f"Period: **{period}** ({_format_period_range(start, end)})",
        color=0x57F287,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Employees", value=str(user_count), inline=True)
    embed.add_field(name="Total Hours", value=fmt_duration(total_mins), inline=True)
    embed.set_footer(text="Includes overtime breakdown per employee")

    file = discord.File(path)
    await interaction.followup.send(embed=embed, file=file, ephemeral=True)


@report_group.command(name="overtime", description="Show overtime summary in Discord (no file)")
@app_commands.describe(period="Time period")
@app_commands.choices(period=[app_commands.Choice(name=p, value=p) for p in PERIODS])
@app_commands.checks.has_permissions(manage_guild=True)
async def report_overtime(interaction: discord.Interaction, period: str = "week"):
    db = interaction.client.db
    exporter = CSVExporter(db)
    await interaction.response.defer(ephemeral=True)

    start, end = exporter.get_period_dates(period)
    guild_id = str(interaction.guild_id)
    entries = await db.get_all_entries_range(guild_id, start, end)
    config = await db.get_overtime_config(guild_id)
    if (config.get("mode") or "overtime") != "overtime":
        await interaction.followup.send(
            "⚠️ Overtime reporting is only available while the server is using `overtime` mode.",
            ephemeral=True,
        )
        return

    from collections import defaultdict
    user_data = defaultdict(lambda: {"username": "", "entry_count": 0})
    for e in entries:
        uid = e["user_id"]
        user_data[uid]["username"] = e["username"]
        user_data[uid]["entry_count"] += 1

    if not user_data:
        await interaction.followup.send("📭 No data for that period.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"⚡ Overtime Report — {period}",
        description=f"Baseline: **{config['daily_hours']}h/day**",
        color=0xFEE75C,
        timestamp=datetime.utcnow()
    )

    overtime_users = []
    for uid, u in user_data.items():
        summary = await db.get_user_summary(guild_id, uid, start, end)
        days = summary["days_worked"]
        total = summary["total_minutes"]
        expected = config["daily_hours"] * 60 * days
        ot = total - expected
        overtime_users.append((u["username"], total, int(ot), days))

    overtime_users.sort(key=lambda x: x[2], reverse=True)

    for username, total, ot, days in overtime_users[:15]:
        ot_str = f"+{fmt_duration(ot)}" if ot > 0 else (f"-{fmt_duration(abs(ot))}" if ot < 0 else "None")
        embed.add_field(
            name=username,
            value=f"Total: {fmt_duration(total)}\n⚡ OT: **{ot_str}**\nDays: {days}",
            inline=True
        )

    await interaction.followup.send(embed=embed, ephemeral=True)


@report_group.command(name="leave", description="Export all leave requests as CSV")
@app_commands.describe(status="Filter by status (leave empty for all)")
@app_commands.choices(status=[
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Pending", value="pending"),
    app_commands.Choice(name="Approved", value="approved"),
    app_commands.Choice(name="Denied", value="denied"),
])
@app_commands.checks.has_permissions(manage_guild=True)
async def report_leave(interaction: discord.Interaction, status: str = "all"):
    db = interaction.client.db
    exporter = CSVExporter(db)
    await interaction.response.defer(ephemeral=True)

    filter_status = None if status == "all" else status
    path = await exporter.export_leave(str(interaction.guild_id), filter_status)
    requests = await db.get_leave_requests(str(interaction.guild_id), status=filter_status)

    embed = discord.Embed(
        title="📋 Leave Requests Export",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Records", value=str(len(requests)), inline=True)
    embed.add_field(name="Filter", value=status.upper(), inline=True)

    file = discord.File(path)
    await interaction.followup.send(embed=embed, file=file, ephemeral=True)


@report_group.command(name="updates", description="Review stored work updates for one employee or everyone")
@app_commands.describe(
    period="Time period to review",
    member="Specific member to review (leave empty for everyone)"
)
@app_commands.choices(period=[app_commands.Choice(name=p, value=p) for p in PERIODS])
@app_commands.checks.has_permissions(manage_guild=True)
async def report_updates(
    interaction: discord.Interaction,
    period: str = "today",
    member: discord.Member = None
):
    db = interaction.client.db
    exporter = CSVExporter(db)
    await interaction.response.defer(ephemeral=True)

    start, end = exporter.get_period_dates(period)
    guild_id = str(interaction.guild_id)
    rows = await db.get_work_updates(
        guild_id,
        start,
        end,
        user_id=str(member.id) if member else None,
    )
    if not rows:
        await interaction.followup.send("📭 No submitted work updates found for that period.", ephemeral=True)
        return

    label = member.display_name if member else "all"
    path = await exporter.export_work_updates(
        guild_id,
        period,
        start,
        end,
        user_id=str(member.id) if member else None,
        label=label,
    )

    distinct_users = len({row["user_id"] for row in rows})
    embed = discord.Embed(
        title="📝 Work Updates Report",
        description=f"Period: **{period}** ({_format_period_range(start, end)})",
        color=0x5865F2,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Entries", value=str(len(rows)), inline=True)
    embed.add_field(name="Employees", value=str(distinct_users), inline=True)
    embed.add_field(name="Filter", value=member.mention if member else "All employees", inline=True)

    await interaction.followup.send(embed=embed, file=discord.File(path), ephemeral=True)


@report_group.command(name="mine", description="View your personal hours summary")
@app_commands.describe(period="Time period")
@app_commands.choices(period=[app_commands.Choice(name=p, value=p) for p in PERIODS])
async def report_mine(interaction: discord.Interaction, period: str = "week"):
    db = interaction.client.db
    exporter = CSVExporter(db)
    await interaction.response.defer(ephemeral=True)

    start, end = exporter.get_period_dates(period)
    guild_id = str(interaction.guild_id)

    summary = await db.get_user_summary(guild_id, str(interaction.user.id), start, end)
    config = await db.get_overtime_config(guild_id)

    embed = discord.Embed(
        title=f"📊 Your Hours — {period.replace('_', ' ').title()}",
        color=0x5865F2,
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.add_field(name="Period", value=_format_period_range(start, end), inline=False)
    embed.add_field(name="Total Hours", value=f"**{fmt_duration(summary['total_minutes'])}**", inline=True)
    embed.add_field(name="Break Time", value=fmt_duration(summary["break_minutes"]), inline=True)
    embed.add_field(name="Days Worked", value=str(summary["days_worked"]), inline=True)
    embed.add_field(name="Sessions", value=str(summary["entry_count"]), inline=True)

    if (config.get("mode") or "overtime") == "overtime":
        expected = config["daily_hours"] * 60 * summary["days_worked"]
        embed.add_field(name="Expected Hours", value=fmt_duration(int(expected)), inline=True)

        if summary["overtime_minutes"] > 0:
            embed.add_field(name="⚡ Overtime", value=f"**+{fmt_duration(summary['overtime_minutes'])}**", inline=True)
        elif summary["total_minutes"] < expected and summary["days_worked"] > 0:
            under = int(expected - summary["total_minutes"])
            embed.add_field(name="⚠️ Under Hours", value=f"-{fmt_duration(under)}", inline=True)

    await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    cog = Reports(bot)
    bot.tree.add_command(report_group)
    await bot.add_cog(cog)
