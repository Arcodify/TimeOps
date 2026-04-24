"""
Standup Scheduler
Parses HH:MM cron-style times, fires standup messages on schedule.
Supports multiple standups per guild, multiple times per day.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
import discord
from zoneinfo import ZoneInfo

log = logging.getLogger("StandupScheduler")


class StandupScheduler:
    def __init__(self, db):
        self.db = db
        self.bot = None
        self.active_voice_rooms = {}

    def set_bot(self, bot):
        self.bot = bot

    async def _get_standup_timezone(self, guild_id: str) -> tuple[ZoneInfo, str]:
        config = await self.db.get_guild_config(guild_id)
        timezone_name = config.get("timezone") or "UTC"
        try:
            return ZoneInfo(timezone_name), timezone_name
        except Exception:
            return ZoneInfo("UTC"), "UTC"

    async def _should_send(self, standup: dict) -> bool:
        """Check if a standup should fire right now (within 1-min window)."""
        cron_time = standup["cron_time"]  # format: "HH:MM" or "HH:MM,HH:MM,..."
        times = [t.strip() for t in cron_time.split(",")]

        tz, _ = await self._get_standup_timezone(str(standup["guild_id"]))
        now_utc = datetime.utcnow()
        now_str = datetime.now(tz).strftime("%H:%M")

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

        await self._cleanup_voice_rooms()
        standups = await self.db.get_all_active_standups()
        for standup in standups:
            if await self._should_send(standup):
                await self._send_standup(standup)

    async def _cleanup_voice_rooms(self):
        now = datetime.utcnow()
        stale_ids = []
        for channel_id, meta in self.active_voice_rooms.items():
            guild = self.bot.get_guild(int(meta["guild_id"]))
            channel = guild.get_channel(channel_id) if guild else None
            if channel is None:
                stale_ids.append(channel_id)
                continue

            cleanup_after = meta["cleanup_after"]
            if now < cleanup_after:
                continue
            if channel.members:
                continue

            try:
                await channel.delete(reason="Temporary standup room expired")
                log.info("Deleted temporary standup room %s", channel_id)
            except Exception as e:
                log.warning("Failed to delete temporary standup room %s: %s", channel_id, e)
            stale_ids.append(channel_id)

        for channel_id in stale_ids:
            self.active_voice_rooms.pop(channel_id, None)

    async def _create_temp_voice_channel(self, standup: dict, text_channel: discord.abc.GuildChannel):
        guild = text_channel.guild
        category = getattr(text_channel, "category", None)
        if category is None and hasattr(text_channel, "parent") and text_channel.parent is not None:
            category = getattr(text_channel.parent, "category", None)

        tz, timezone_name = await self._get_standup_timezone(str(standup["guild_id"]))
        scheduled_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
        voice_name = f"{standup['name']} • {scheduled_time}"
        voice_duration_minutes = int(standup.get("voice_duration_minutes") or 20)
        voice_channel = await guild.create_voice_channel(
            name=voice_name[:100],
            category=category,
            reason=f"Temporary standup room for {standup['name']}",
        )
        self.active_voice_rooms[voice_channel.id] = {
            "guild_id": str(guild.id),
            "standup_id": standup["id"],
            "cleanup_after": datetime.utcnow() + timedelta(minutes=voice_duration_minutes),
        }
        return voice_channel

    async def _get_eligible_standup_members(
        self, guild: discord.Guild, standup: dict
    ) -> list[discord.Member]:
        guild_id = str(standup["guild_id"])
        tz, _ = await self._get_standup_timezone(guild_id)
        local_date = datetime.now(tz).date().isoformat()
        active_entries = await self.db.get_active_entries_for_guild(guild_id)
        if not active_entries:
            return []

        on_leave_user_ids = await self.db.get_users_on_approved_leave(guild_id, local_date)
        on_break_user_ids = await self.db.get_users_with_active_breaks(guild_id)

        members: list[discord.Member] = []
        seen_user_ids: set[str] = set()
        for row in active_entries:
            user_id = str(row["user_id"])
            if user_id in seen_user_ids or user_id in on_leave_user_ids or user_id in on_break_user_ids:
                continue
            seen_user_ids.add(user_id)

            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except Exception:
                    member = None
            if member is not None:
                members.append(member)
        return members

    async def _get_present_ping_role(
        self,
        guild: discord.Guild,
        standup: dict,
    ) -> discord.Role | None:
        config = await self.db.get_guild_config(str(guild.id))
        present_role_id = config.get("present_role_id")
        if present_role_id:
            role = guild.get_role(int(present_role_id))
            if role is not None:
                return role

        ping_role_id = standup.get("ping_role")
        if ping_role_id:
            return guild.get_role(int(ping_role_id))
        return None

    async def _send_standup(self, standup: dict, *, update_last_sent: bool = True):
        try:
            channel = self.bot.get_channel(int(standup["channel_id"]))
            if not channel:
                channel = await self.bot.fetch_channel(int(standup["channel_id"]))
            if not channel:
                log.warning(f"Standup channel {standup['channel_id']} not found")
                return

            guild = getattr(channel, "guild", None)
            if guild is None:
                log.warning("Standup channel %s is not attached to a guild", standup["channel_id"])
                return

            eligible_members = await self._get_eligible_standup_members(guild, standup)
            if not eligible_members:
                if update_last_sent:
                    await self.db.update_standup_last_sent(standup["id"], datetime.utcnow().isoformat())
                log.info(
                    "Skipped standup '%s' in guild %s because nobody is currently eligible",
                    standup["name"],
                    standup["guild_id"],
                )
                return

            voice_channel = await self._create_temp_voice_channel(standup, channel)
            voice_duration_minutes = int(standup.get("voice_duration_minutes") or 20)
            description = standup["message"] or "Standup is live. Join the meeting below."
            occurrence_key = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            ping_role = await self._get_present_ping_role(guild, standup)
            from cogs.standup import (
                DEFAULT_FORM_TITLE_1,
                DEFAULT_FORM_TITLE_2,
                DEFAULT_FORM_TITLE_3,
                build_standup_submission_view,
            )

            embed = discord.Embed(
                title=f"📢 {standup['name']}",
                description=description,
                color=0x5865F2,
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Discord Voice", value=voice_channel.mention, inline=False)
            if standup.get("meeting_url"):
                embed.add_field(name="Meeting URL", value=standup["meeting_url"], inline=False)
            embed.add_field(
                name="Availability",
                value=f"Temporary voice room stays open for about `{voice_duration_minutes}` minutes.",
                inline=False,
            )
            embed.add_field(
                name="Recipients",
                value=(
                    f"`{len(eligible_members)}` present member(s) are eligible.\n"
                    f"Ping: {ping_role.mention if ping_role is not None else 'No role ping configured'}"
                ),
                inline=False,
            )
            embed.add_field(
                name="Standup Form",
                value=(
                    f"1. {standup.get('form_title_1') or DEFAULT_FORM_TITLE_1}\n"
                    f"2. {standup.get('form_title_2') or DEFAULT_FORM_TITLE_2}\n"
                    f"3. {standup.get('form_title_3') or DEFAULT_FORM_TITLE_3}"
                    f" ({'optional' if standup.get('form_title_3_optional', 1) else 'required'})"
                ),
                inline=False,
            )
            _, timezone_name = await self._get_standup_timezone(str(standup["guild_id"]))
            embed.set_footer(text=f"HR Bot • Standup • {timezone_name}")

            view = build_standup_submission_view(self.bot, standup, occurrence_key)
            await channel.send(
                content=ping_role.mention if ping_role is not None else None,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )
            if update_last_sent:
                await self.db.update_standup_last_sent(standup["id"], datetime.utcnow().isoformat())
            log.info(
                "Sent standup '%s' to channel %s with temporary voice room %s",
                standup["name"],
                standup["channel_id"],
                voice_channel.id,
            )
        except Exception as e:
            log.error(f"Failed to send standup {standup['id']}: {e}")
