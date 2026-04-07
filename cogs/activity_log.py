import logging
from datetime import datetime

import discord


log = logging.getLogger("ActivityLog")


async def resolve_activity_log_channel(bot, guild: discord.Guild):
    if guild is None:
        return None

    config = await bot.db.get_guild_config(str(guild.id))
    channel_id = config.get("activity_log_channel_id") or config.get("leave_channel_id")
    if not channel_id:
        return None

    channel = None
    if hasattr(guild, "get_channel_or_thread"):
        channel = guild.get_channel_or_thread(int(channel_id))
    else:
        channel = guild.get_channel(int(channel_id))

    if channel is None:
        try:
            channel = await guild.fetch_channel(int(channel_id))
        except Exception:
            channel = None

    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        return channel
    return None


async def post_activity_log(
    bot,
    guild_id: str,
    *,
    title: str,
    description: str = None,
    color: int = 0x5865F2,
    fields: list[tuple[str, str, bool]] | None = None,
    footer: str = None,
    thumbnail_url: str = None,
    view: discord.ui.View = None,
):
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return None

    channel = await resolve_activity_log_channel(bot, guild)
    if channel is None:
        return None

    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.utcnow())
    for name, value, inline in fields or []:
        embed.add_field(name=name, value=value, inline=inline)
    if footer:
        embed.set_footer(text=footer)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    try:
        return await channel.send(embed=embed, view=view)
    except Exception:
        log.warning("Failed to post activity log in guild %s", guild_id)
        return None
