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
    handlers=[logging.FileHandler("logs/bot.log"), logging.StreamHandler()],
)
log = logging.getLogger("HRBot")

SYNC_GUILD_ID = os.getenv("DISCORD_GUILD_ID")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
db = Database()
bot.db = db
standup_scheduler = StandupScheduler(db)
bot.standup_scheduler = standup_scheduler


async def sync_app_commands() -> None:
    if SYNC_GUILD_ID:
        guild = discord.Object(id=int(SYNC_GUILD_ID))
        application_id = bot.application_id
        if application_id is None:
            raise RuntimeError("Bot application ID is unavailable during command sync")

        # Remove stale global command definitions so Discord cannot serve an
        # outdated schema for the same command names while this bot is using
        # guild-scoped sync for faster iteration.
        await bot.http.bulk_upsert_global_commands(application_id, payload=[])
        log.info("Cleared remote global slash commands before guild sync")

        bot.tree.clear_commands(guild=guild)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info(f"Synced {len(synced)} slash commands to guild {SYNC_GUILD_ID}")
        return

    synced = await bot.tree.sync()
    log.info(f"Synced {len(synced)} slash commands globally")


# ─── BACKGROUND TASKS ────────────────────────────────────────────────────────
@tasks.loop(minutes=5)
async def auto_checkout_loop():
    await db.auto_checkout_overdue(bot)


@tasks.loop(minutes=1)
async def standup_check_loop():
    await standup_scheduler.check_and_send()


@tasks.loop(hours=1)
async def daily_export_loop():
    from datetime import datetime

    now = datetime.now()
    if now.hour == 0 and now.minute < 5:
        from csv_exporter import CSVExporter

        exporter = CSVExporter(db)
        await exporter.export_daily()
        log.info("Auto daily CSV export completed")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
async def main():
    async with bot:
        await db.init()

        for ext in [
            "cogs.timeclock",
            "cogs.leave",
            "cogs.standup",
            "cogs.admin",
            "cogs.reports",
            "cogs.breaks",
            "cogs.holidays",
            "cogs.reminders",
            "cogs.updates",
            "cogs.help",
        ]:
            await bot.load_extension(ext)

        # on_ready defined here so cogs are already loaded when it fires
        @bot.event
        async def on_ready():
            log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
            standup_scheduler.set_bot(bot)

            try:
                await sync_app_commands()
            except Exception as e:
                log.error(f"Failed to sync commands: {e}")

            if not auto_checkout_loop.is_running():
                auto_checkout_loop.start()
            if not standup_check_loop.is_running():
                standup_check_loop.start()
            if not daily_export_loop.is_running():
                daily_export_loop.start()
            log.info("Background tasks started")

        await bot.start(os.getenv("DISCORD_TOKEN"))


if __name__ == "__main__":
    asyncio.run(main())
