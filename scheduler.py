"""
Standup Scheduler
Parses HH:MM cron-style times, fires standup messages on schedule.
Supports multiple standups per guild, multiple times per day.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
import discord

log = logging.getLogger("StandupScheduler")


class StandupScheduler:
    def __init__(self, db):
        self.db = db
        self.bot = None

    def set_bot(self, bot):
        self.bot = bot

    def _should_send(self, standup: dict) -> bool:
        """Check if a standup should fire right now (within 1-min window)."""
        cron_time = standup["cron_time"]  # format: "HH:MM" or "HH:MM,HH:MM,..."
        times = [t.strip() for t in cron_time.split(",")]
        
        now_utc = datetime.utcnow()
        now_str = now_utc.strftime("%H:%M")
        
        if now_str not in times:
            return False
        
        # Check if already sent in this minute window
        last_sent = standup.get("last_sent")
        if last_sent:
            try:
                last = datetime.fromisoformat(last_sent)
                if (now_utc - last).total_seconds() < 90:
                    return False
            except Exception:
                pass
        
        return True

    async def check_and_send(self):
        """Called every minute. Sends any due standups."""
        if not self.bot:
            return
        
        standups = await self.db.get_all_active_standups()
        for standup in standups:
            if self._should_send(standup):
                await self._send_standup(standup)

    async def _send_standup(self, standup: dict):
        try:
            channel = self.bot.get_channel(int(standup["channel_id"]))
            if not channel:
                channel = await self.bot.fetch_channel(int(standup["channel_id"]))
            if not channel:
                log.warning(f"Standup channel {standup['channel_id']} not found")
                return

            embed = discord.Embed(
                title=f"📢 {standup['name']}",
                description=standup["message"],
                color=0x5865F2,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="HR Bot • Standup")

            content = ""
            if standup.get("ping_role"):
                content = f"<@&{standup['ping_role']}>"

            await channel.send(content=content or None, embed=embed)
            await self.db.update_standup_last_sent(standup["id"], datetime.utcnow().isoformat())
            log.info(f"Sent standup '{standup['name']}' to channel {standup['channel_id']}")
        except Exception as e:
            log.error(f"Failed to send standup {standup['id']}: {e}")
