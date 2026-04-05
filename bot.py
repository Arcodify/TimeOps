"""
Discord HR Bot - Full Featured
Clock in/out, Leave Requests, Overtime, Standups, CSV exports
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import logging
import os
from dotenv import load_dotenv
from database import Database
from scheduler import StandupScheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("HRBot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database()
bot.db = db  # attach to bot so all cogs share the same instance
standup_scheduler = StandupScheduler(db)


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    standup_scheduler.set_bot(bot)
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")

    # Start background tasks (guard against double-start on reconnect)
    if not auto_checkout_loop.is_running():
        auto_checkout_loop.start()
    if not standup_check_loop.is_running():
        standup_check_loop.start()
    if not daily_export_loop.is_running():
        daily_export_loop.start()
    log.info("Background tasks started")


# ─── BACKGROUND TASKS ────────────────────────────────────────────────────────

@tasks.loop(minutes=5)
async def auto_checkout_loop():
    """Auto clock-out users who have been clocked in too long."""
    await db.auto_checkout_overdue(bot)

@tasks.loop(minutes=1)
async def standup_check_loop():
    """Check and trigger standup messages."""
    await standup_scheduler.check_and_send()

@tasks.loop(hours=1)
async def daily_export_loop():
    """Auto-export CSVs at midnight."""
    from datetime import datetime, time as dtime
    now = datetime.now()
    if now.hour == 0 and now.minute < 5:
        from csv_exporter import CSVExporter
        exporter = CSVExporter(db)
        await exporter.export_daily()
        log.info("Auto daily CSV export completed")


# ─── LOAD COGS ────────────────────────────────────────────────────────────────

async def main():
    async with bot:
        # Init DB first so bot.db is ready for all cogs
        await db.init()
        for ext in [
            "cogs.timeclock", "cogs.leave", "cogs.standup",
            "cogs.admin", "cogs.reports", "cogs.breaks",
            "cogs.holidays", "cogs.reminders", "cogs.help",
        ]:
            await bot.load_extension(ext)
        await bot.start(os.getenv("DISCORD_TOKEN"))


if __name__ == "__main__":
    asyncio.run(main())
