"""
Help Cog
/hrhelp — paginated command reference by category
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging

log = logging.getLogger("Help")

PAGES = [
    {
        "title": "⏱️ Time Clock",
        "color": 0x57F287,
        "fields": [
            ("/clockin [note]", "Clock in to start your session"),
            ("/clockout", "Clock out and see your session duration"),
            ("/status", "Check if you're clocked in + today's hours"),
            ("/clockpanel", "Post a button panel in this channel *(Admin)*"),
        ]
    },
    {
        "title": "☕ Breaks",
        "color": 0xFEE75C,
        "fields": [
            ("/break start [type]", "Start a manual break — can assign the configured On Break role"),
            ("/break end", "End your current break"),
            ("/break status", "View break history for your active session"),
            ("/break configure [break1...] [break2...]", "Set up to two automatic company break windows *(Admin)*"),
            ("/break schedule", "View the configured automatic break windows"),
        ]
    },
    {
        "title": "📋 Leave Requests",
        "color": 0x5865F2,
        "fields": [
            ("/leave request", "Submit a leave request (opens a form)"),
            ("/leave list", "View your own leave requests + statuses"),
            ("/leave pending", "View all pending requests *(Admin)*"),
            ("/leavepanel", "Post a leave request panel in this channel *(Admin)*"),
        ]
    },
    {
        "title": "📅 Standups",
        "color": 0xEB459E,
        "fields": [
            ("/standup add", "Schedule a recurring standup with a temporary voice room *(Admin)*"),
            ("/standup list", "List all standup schedules"),
            ("/standup pause", "Pause or resume a standup *(Admin)*"),
            ("/standup delete", "Remove a standup schedule *(Admin)*"),
            ("/standup test", "Fire a standup now and create its temporary voice room *(Admin)*"),
        ]
    },
    {
        "title": "📊 Reports & Exports",
        "color": 0x57F287,
        "fields": [
            ("/report mine [period]", "Your own hours summary (any period)"),
            ("/report timesheet [period]", "Detailed time entries CSV *(Admin)*"),
            ("/report summary [period]", "Per-employee summary + overtime CSV *(Admin)*"),
            ("/report overtime [period]", "Overtime overview embed *(Admin)*"),
            ("/report leave", "All leave requests CSV *(Admin)*"),
            ("/report updates [period] [member]", "Work updates CSV for one employee or everyone *(Admin)*"),
        ]
    },
    {
        "title": "📝 Work Updates",
        "color": 0x5865F2,
        "fields": [
            ("/update status", "View periodic work-update prompt settings"),
            ("/update config [enabled] [interval_hours] [question] [archive_channel]", "Set prompt timing, question, and archive channel *(Admin)*"),
            ("/update submit", "Submit your pending work update"),
        ]
    },
    {
        "title": "🎉 Holidays",
        "color": 0xFEE75C,
        "fields": [
            ("/holiday add", "Add a company holiday *(Admin)*"),
            ("/holiday list", "List all holidays"),
            ("/holiday upcoming [count]", "Show next N upcoming holidays"),
            ("/holiday delete", "Remove a holiday *(Admin)*"),
        ]
    },
    {
        "title": "⏰ Reminders",
        "color": 0xED4245,
        "fields": [
            ("/reminder set [time]", "Set EOD clock-out reminder time *(Admin)*"),
            ("/reminder test", "Fire reminders now to all clocked-in users *(Admin)*"),
            ("/reminder status", "View current reminder settings"),
        ]
    },
    {
        "title": "⚙️ Admin Configuration",
        "color": 0x99AAB5,
        "fields": [
            ("/hrconfig configure", "Set activity log channel, admin role, present role, on-break role, and timezone"),
            ("/hrconfig view", "View current bot configuration"),
            ("/overtime config", "Set daily/weekly hours, break allowance, and blocked clock-in days"),
        ]
    },
]

PERIODS = "`today` · `yesterday` · `week` · `last_week` · `month` · `last_month`"


def build_embed(page_index: int) -> discord.Embed:
    page = PAGES[page_index]
    embed = discord.Embed(
        title=f"HR Bot Help — {page['title']}",
        color=page["color"]
    )
    for name, value in page["fields"]:
        embed.add_field(name=f"`{name}`" if not name.startswith("`") else name, value=value, inline=False)
    embed.set_footer(text=f"Page {page_index + 1}/{len(PAGES)}  •  Report periods: {PERIODS}")
    return embed


class HelpView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=120)
        self.page = page
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page == len(PAGES) - 1
        self.page_btn.label = f"{self.page + 1} / {len(PAGES)}"

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=build_embed(self.page), view=self)

    @discord.ui.button(label="1 / 8", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # display only

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(PAGES) - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=build_embed(self.page), view=self)


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="hrhelp", description="Show all HR bot commands")
    async def hrhelp(self, interaction: discord.Interaction):
        view = HelpView(page=0)
        await interaction.response.send_message(embed=build_embed(0), view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Help(bot))
