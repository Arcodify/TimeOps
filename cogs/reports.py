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


class Reports(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.exporter = CSVExporter(bot.db)

    @report_group.command(name="timesheet", description="Export detailed time entries as CSV")
    @app_commands.describe(
        period="Time period to export",
        member="Specific member (leave empty for all)"
    )
    @app_commands.choices(period=[app_commands.Choice(name=p, value=p) for p in PERIODS])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def report_timesheet(
        self,
        interaction: discord.Interaction,
        period: str = "week",
        member: discord.Member = None
    ):
        await interaction.response.defer(ephemeral=True)
        
        start, end = self.exporter.get_period_dates(period)
        guild_id = str(interaction.guild_id)
        
        if member:
            entries = await self.db.get_entries_range(guild_id, str(member.id), start, end)
            label = f"{member.display_name}_{period}"
        else:
            entries = await self.db.get_all_entries_range(guild_id, start, end)
            label = f"all_{period}"
        
        if not entries:
            await interaction.followup.send("📭 No time entries found for that period.", ephemeral=True)
            return
        
        path = await self.exporter.export_timesheet(guild_id, label, start, end)
        
        embed = discord.Embed(
            title=f"📊 Timesheet Report",
            description=f"Period: **{period}** ({start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')})",
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
    async def report_summary(self, interaction: discord.Interaction, period: str = "week"):
        await interaction.response.defer(ephemeral=True)
        
        start, end = self.exporter.get_period_dates(period)
        guild_id = str(interaction.guild_id)
        
        path = await self.exporter.export_summary(guild_id, period, start, end)
        
        # Quick stats for embed
        entries = await self.db.get_all_entries_range(guild_id, start, end)
        user_count = len(set(e["user_id"] for e in entries))
        total_mins = sum(e["duration_minutes"] or 0 for e in entries if e["clock_out"])
        
        embed = discord.Embed(
            title="📈 Summary Report",
            description=f"Period: **{period}** ({start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')})",
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
    async def report_overtime(self, interaction: discord.Interaction, period: str = "week"):
        await interaction.response.defer(ephemeral=True)
        
        start, end = self.exporter.get_period_dates(period)
        guild_id = str(interaction.guild_id)
        entries = await self.db.get_all_entries_range(guild_id, start, end)
        config = await self.db.get_overtime_config(guild_id)
        
        # Group by user
        from collections import defaultdict
        user_data = defaultdict(lambda: {"username": "", "total_mins": 0, "days": set()})
        for e in entries:
            if e["clock_out"]:
                uid = e["user_id"]
                user_data[uid]["username"] = e["username"]
                user_data[uid]["total_mins"] += e["duration_minutes"] or 0
                user_data[uid]["days"].add(e["clock_in"][:10])
        
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
            days = len(u["days"])
            expected = config["daily_hours"] * 60 * days
            ot = u["total_mins"] - expected
            overtime_users.append((u["username"], u["total_mins"], int(ot), days))
        
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
    async def report_leave(self, interaction: discord.Interaction, status: str = "all"):
        await interaction.response.defer(ephemeral=True)
        
        filter_status = None if status == "all" else status
        path = await self.exporter.export_leave(str(interaction.guild_id), filter_status)
        
        requests = await self.db.get_leave_requests(str(interaction.guild_id), status=filter_status)
        
        embed = discord.Embed(
            title="📋 Leave Requests Export",
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Records", value=str(len(requests)), inline=True)
        embed.add_field(name="Filter", value=status.upper(), inline=True)
        
        file = discord.File(path)
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @report_group.command(name="mine", description="View your personal hours summary")
    @app_commands.describe(period="Time period")
    @app_commands.choices(period=[app_commands.Choice(name=p, value=p) for p in PERIODS])
    async def report_mine(self, interaction: discord.Interaction, period: str = "week"):
        await interaction.response.defer(ephemeral=True)
        
        start, end = self.exporter.get_period_dates(period)
        guild_id = str(interaction.guild_id)
        
        summary = await self.db.get_user_summary(guild_id, str(interaction.user.id), start, end)
        config = await self.db.get_overtime_config(guild_id)
        
        embed = discord.Embed(
            title=f"📊 Your Hours — {period.replace('_', ' ').title()}",
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Period", value=f"{start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}", inline=False)
        embed.add_field(name="Total Hours", value=f"**{fmt_duration(summary['total_minutes'])}**", inline=True)
        embed.add_field(name="Days Worked", value=str(summary["days_worked"]), inline=True)
        embed.add_field(name="Sessions", value=str(summary["entry_count"]), inline=True)
        
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
