"""
Work Updates Cog
/update config, /update status, /update submit
Periodically prompts clocked-in users for work updates and stores responses.
"""

import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.tasks import loop

log = logging.getLogger("Updates")

update_group = app_commands.Group(name="update", description="Periodic work update prompts")
DEFAULT_WORK_UPDATE_QUESTION = "Share a short work update for this check-in."


def _sanitize_update_value(value: str) -> str:
    text = (value or "").strip()
    return text or "—"


def _compose_work_update_content(current_work: str, next_work: str, blockers: str) -> str:
    return (
        f"What are you working on?\n{_sanitize_update_value(current_work)}\n\n"
        f"What will you work on now?\n{_sanitize_update_value(next_work)}\n\n"
        f"Any blockers or notes?\n{_sanitize_update_value(blockers)}"
    )


def _split_work_update_content(content: str) -> tuple[str, str, str]:
    current_work = "—"
    next_work = "—"
    blockers = "—"
    if not content:
        return current_work, next_work, blockers

    parts = content.split("\n\n")
    for part in parts:
        lines = part.split("\n", 1)
        header = lines[0].strip().lower()
        body = lines[1].strip() if len(lines) > 1 else "—"
        if header.startswith("what are you working on?"):
            current_work = body or "—"
        elif header.startswith("what will you work on now?"):
            next_work = body or "—"
        elif header.startswith("any blockers or notes?"):
            blockers = body or "—"
    return current_work, next_work, blockers


def _format_interval(hours: float) -> str:
    whole = int(hours)
    if abs(hours - whole) < 1e-9:
        return f"{whole}h"
    return f"{hours:g}h"


class WorkUpdateModal(discord.ui.Modal):
    def __init__(
        self,
        bot,
        guild_id: str,
        user_id: str,
        username: str,
        time_entry_id: int,
        prompt_slot: int,
        question_text: str,
    ):
        super().__init__(title="Work Update")
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.username = username
        self.time_entry_id = time_entry_id
        self.prompt_slot = prompt_slot
        self.question_text = question_text

        self.current_work = discord.ui.TextInput(
            label="What are you working on?",
            placeholder="What have you been working on in this period?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=800,
        )
        self.next_work = discord.ui.TextInput(
            label="What will you work on now?",
            placeholder="What are you doing next?",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=800,
        )
        self.blockers = discord.ui.TextInput(
            label="Any blockers or notes?",
            placeholder="Any blockers or k thyo dai le vannu vako?",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.current_work)
        self.add_item(self.next_work)
        self.add_item(self.blockers)

    async def on_submit(self, interaction: discord.Interaction):
        content = _compose_work_update_content(
            self.current_work.value,
            self.next_work.value,
            self.blockers.value,
        )
        await self.bot.db.submit_work_update(
            self.guild_id,
            self.user_id,
            self.username,
            self.time_entry_id,
            self.prompt_slot,
            self.question_text,
            content,
        )
        await _post_work_update_archive(
            self.bot,
            self.guild_id,
            self.username,
            self.prompt_slot,
            self.question_text,
            content,
        )
        await interaction.response.send_message(
            "✅ Update recorded.",
            ephemeral=True,
        )


class WorkUpdatePromptView(discord.ui.View):
    def __init__(
        self,
        bot,
        guild_id: str,
        user_id: str,
        username: str,
        time_entry_id: int,
        prompt_slot: int,
        question_text: str,
    ):
        super().__init__(timeout=86400)
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.username = username
        self.time_entry_id = time_entry_id
        self.prompt_slot = prompt_slot
        self.question_text = question_text

        button = discord.ui.Button(
            label="Submit Update",
            style=discord.ButtonStyle.primary,
        )
        button.callback = self.submit_callback
        self.add_item(button)

    async def submit_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "⚠️ This prompt belongs to someone else.",
                ephemeral=True,
            )
            return

        modal = WorkUpdateModal(
            self.bot,
            self.guild_id,
            self.user_id,
            self.username,
            self.time_entry_id,
            self.prompt_slot,
            self.question_text,
        )
        await interaction.response.send_modal(modal)


async def _resolve_archive_channel(guild: discord.Guild, channel_id: str):
    if not channel_id:
        return None

    channel = None
    if hasattr(guild, "get_channel_or_thread"):
        channel = guild.get_channel_or_thread(int(channel_id))
    if channel is None:
        channel = guild.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await guild.fetch_channel(int(channel_id))
        except Exception:
            channel = None
    return channel


async def _post_work_update_archive(
    bot,
    guild_id: str,
    username: str,
    prompt_slot: int,
    question_text: str,
    content: str,
):
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return

    config = await bot.db.get_work_update_config(guild_id)
    archive_channel_id = config.get("archive_channel_id")
    if not archive_channel_id:
        return

    channel = await _resolve_archive_channel(guild, archive_channel_id)
    if channel is None:
        return

    embed = discord.Embed(
        title="📝 Work Update Submitted",
        color=0x57F287,
        timestamp=datetime.utcnow(),
    )
    current_work, next_work, blockers = _split_work_update_content(content)
    embed.add_field(name="Employee", value=username, inline=True)
    embed.add_field(name="Prompt", value=f"`#{prompt_slot}`", inline=True)
    embed.add_field(name="Instruction", value=(question_text or DEFAULT_WORK_UPDATE_QUESTION)[:1024], inline=False)
    embed.add_field(name="What are you working on?", value=current_work[:1024], inline=False)
    embed.add_field(name="What will you work on now?", value=next_work[:1024], inline=False)
    embed.add_field(name="Any blockers or notes?", value=blockers[:1024], inline=False)
    try:
        await channel.send(embed=embed)
    except Exception:
        log.warning("Failed to post work update archive entry in guild %s", guild_id)


async def _deliver_work_update_prompt(bot, guild: discord.Guild, row: dict, config: dict):
    interval_hours = float(config.get("interval_hours") or 0)
    question_text = config.get("question_text") or DEFAULT_WORK_UPDATE_QUESTION
    prompt_slot = row["prompt_slot"]
    member = guild.get_member(int(row["user_id"]))
    if member is None:
        try:
            member = await guild.fetch_member(int(row["user_id"]))
        except Exception:
            return

    created = await bot.db.ensure_work_update_prompt(
        row["guild_id"],
        row["user_id"],
        row["username"],
        row["id"],
        prompt_slot,
        question_text,
    )
    if not created:
        return

    view = WorkUpdatePromptView(
        bot,
        row["guild_id"],
        row["user_id"],
        row["username"],
        row["id"],
        prompt_slot,
        question_text,
    )
    embed = discord.Embed(
        title="📝 Work Update Needed",
        description=(
            f"You've been clocked in long enough for update #{prompt_slot}.\n"
            f"Please submit your structured update for the last {_format_interval(interval_hours)}."
        ),
        color=0x5865F2,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Clocked In", value=f"`{row['clock_in'].replace('T', ' ')[:16]} UTC`", inline=True)
    embed.add_field(name="Prompt", value=f"`#{prompt_slot}`", inline=True)
    embed.add_field(name="Instruction", value=question_text, inline=False)
    embed.add_field(
        name="Form",
        value=(
            "1. What are you working on?\n"
            "2. What will you work on now?\n"
            "3. Any blockers or notes?"
        ),
        inline=False,
    )
    embed.set_footer(text="HR Bot • Work Updates")

    try:
        await member.send(embed=embed, view=view)
        log.info(
            "Sent work update prompt #%s to %s in guild %s",
            prompt_slot,
            row["username"],
            row["guild_id"],
        )
    except Exception:
        log.warning(
            "Failed to DM work update prompt #%s to %s in guild %s",
            prompt_slot,
            row["username"],
            row["guild_id"],
        )


class Updates(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.work_update_loop.start()

    async def cog_unload(self):
        self.work_update_loop.cancel()

    @loop(minutes=1)
    async def work_update_loop(self):
        configs = await self.bot.db.get_enabled_work_update_configs()
        now = datetime.utcnow()

        for config in configs:
            guild_id = str(config["guild_id"])
            interval_hours = float(config.get("interval_hours") or 0)
            if interval_hours <= 0:
                continue

            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue

            active_entries = await self.bot.db.get_active_entries_for_guild(guild_id)
            for row in active_entries:
                clock_in = datetime.fromisoformat(row["clock_in"])
                elapsed_hours = (now - clock_in).total_seconds() / 3600
                prompt_slot = int(elapsed_hours // interval_hours)
                if prompt_slot < 1:
                    continue
                await _deliver_work_update_prompt(
                    self.bot,
                    guild,
                    {**row, "prompt_slot": prompt_slot},
                    config,
                )


@update_group.command(name="config", description="Configure periodic work-update prompts")
@app_commands.describe(
    enabled="Turn periodic work updates on or off",
    interval_hours="How many hours between prompts while clocked in",
    question="Instruction shown above the 3-part update form",
    archive_channel="Channel or thread where submitted answers should be mirrored",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def update_config(
    interaction: discord.Interaction,
    enabled: bool = True,
    interval_hours: float = 2.0,
    question: str = DEFAULT_WORK_UPDATE_QUESTION,
    archive_channel: discord.app_commands.AppCommandChannel = None,
):
    if interval_hours <= 0:
        await interaction.response.send_message("❌ Interval must be greater than 0 hours.", ephemeral=True)
        return
    if len(question.strip()) < 5:
        await interaction.response.send_message("❌ Instruction is too short.", ephemeral=True)
        return

    archive_obj = None
    if archive_channel is not None:
        archive_obj = archive_channel.resolve() if hasattr(archive_channel, "resolve") else None
        if archive_obj is None and hasattr(archive_channel, "fetch"):
            try:
                archive_obj = await archive_channel.fetch()
            except Exception:
                archive_obj = None
        if archive_obj is None or not isinstance(archive_obj, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "❌ Archive answers need a text channel or thread the bot can access.",
                ephemeral=True,
            )
            return

    await interaction.client.db.set_work_update_config(
        str(interaction.guild_id),
        enabled=enabled,
        interval_hours=interval_hours,
        question_text=question.strip(),
        archive_channel_id=str(archive_obj.id) if archive_obj else None,
    )

    embed = discord.Embed(title="📝 Work Update Prompts", color=0x57F287)
    embed.add_field(name="Status", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
    embed.add_field(name="Interval", value=f"`{_format_interval(interval_hours)}`", inline=True)
    embed.add_field(name="Archive Channel", value=archive_obj.mention if archive_obj else "Not set", inline=True)
    embed.add_field(name="Instruction", value=question.strip(), inline=False)
    embed.add_field(
        name="Form Fields",
        value=(
            "What are you working on?\n"
            "What will you work on now?\n"
            "Any blockers or notes?"
        ),
        inline=False,
    )
    embed.set_footer(text="Clocked-in staff will be asked for updates at this interval.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@update_group.command(name="status", description="View current work-update prompt settings")
async def update_status(interaction: discord.Interaction):
    config = await interaction.client.db.get_work_update_config(str(interaction.guild_id))
    embed = discord.Embed(title="📝 Work Update Settings", color=0x5865F2)
    embed.add_field(
        name="Status",
        value="🟢 Enabled" if config.get("enabled") else "🔴 Disabled",
        inline=True,
    )
    embed.add_field(
        name="Interval",
        value=f"`{_format_interval(float(config.get('interval_hours') or 2.0))}`",
        inline=True,
    )
    archive_channel_id = config.get("archive_channel_id")
    embed.add_field(
        name="Archive Channel",
        value=f"<#{archive_channel_id}>" if archive_channel_id else "Not set",
        inline=True,
    )
    embed.add_field(
        name="Instruction",
        value=config.get("question_text") or DEFAULT_WORK_UPDATE_QUESTION,
        inline=False,
    )
    embed.add_field(
        name="Form Fields",
        value=(
            "What are you working on?\n"
            "What will you work on now?\n"
            "Any blockers or notes?"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@update_group.command(name="submit", description="Submit your pending work update")
async def update_submit(interaction: discord.Interaction):
    db = interaction.client.db
    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)
    active_entry = await db.get_active_entry(guild_id, user_id)
    if not active_entry:
        await interaction.response.send_message(
            "⚠️ You're not currently clocked in.",
            ephemeral=True,
        )
        return

    pending = await db.get_pending_work_update(guild_id, user_id, active_entry["id"])
    if pending is None:
        config = await db.get_work_update_config(guild_id)
        interval_hours = float(config.get("interval_hours") or 0)
        question_text = config.get("question_text") or DEFAULT_WORK_UPDATE_QUESTION
        if not config.get("enabled") or interval_hours <= 0:
            await interaction.response.send_message(
                "⚠️ Periodic work updates are not enabled for this server.",
                ephemeral=True,
            )
            return

        elapsed_hours = (datetime.utcnow() - datetime.fromisoformat(active_entry["clock_in"])).total_seconds() / 3600
        prompt_slot = int(elapsed_hours // interval_hours)
        if prompt_slot < 1:
            await interaction.response.send_message(
                f"⚠️ No work update is due yet. The current interval is {_format_interval(interval_hours)}.",
                ephemeral=True,
            )
            return

        await db.ensure_work_update_prompt(
            guild_id,
            user_id,
            interaction.user.display_name,
            active_entry["id"],
            prompt_slot,
            question_text,
        )
        pending = await db.get_pending_work_update(guild_id, user_id, active_entry["id"])

    modal = WorkUpdateModal(
        interaction.client,
        guild_id,
        user_id,
        interaction.user.display_name,
        active_entry["id"],
        int(pending["prompt_slot"]),
        pending.get("question_text") or DEFAULT_WORK_UPDATE_QUESTION,
    )
    await interaction.response.send_modal(modal)


async def setup(bot):
    cog = Updates(bot)
    bot.tree.add_command(update_group)
    await bot.add_cog(cog)
