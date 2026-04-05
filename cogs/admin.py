"""
Admin Cog
/hrsetup — configure leave channel, admin role, timezone
/overtime config — set daily/weekly hours and auto-out limit
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
import re

log = logging.getLogger("Admin")

admin_group = app_commands.Group(name="hrconfig", description="HR Bot server configuration")
overtime_group = app_commands.Group(name="overtime", description="Overtime configuration")


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    def _parse_snowflake(self, value):
        if value is None:
            return None

        if isinstance(value, int):
            return value

        text = str(value).strip()
        match = (
            re.fullmatch(r"<#(\d+)>", text)
            or re.fullmatch(r"<@&(\d+)>", text)
            or re.fullmatch(r"(\d+)", text)
        )
        return int(match.group(1)) if match else None

    async def _resolve_channel_input(self, guild: discord.Guild, value):
        channel_id = self._parse_snowflake(value)
        if channel_id is None:
            return None

        if hasattr(guild, "get_channel_or_thread"):
            channel = guild.get_channel_or_thread(channel_id)
            if channel:
                return channel

        channel = guild.get_channel(channel_id)
        if channel:
            return channel

        try:
            return await guild.fetch_channel(channel_id)
        except Exception:
            return None

    def _resolve_role_input(self, guild: discord.Guild, value):
        role_id = self._parse_snowflake(value)
        if role_id is None:
            return None
        return guild.get_role(role_id)

    async def _resolve_leave_channel(self, interaction: discord.Interaction, leave_channel):
        if leave_channel is None:
            return None

        if isinstance(leave_channel, (discord.TextChannel, discord.Thread)):
            return leave_channel

        channel = None
        if hasattr(leave_channel, "resolve"):
            channel = leave_channel.resolve()
        if channel is None and hasattr(leave_channel, "fetch"):
            try:
                channel = await leave_channel.fetch()
            except Exception:
                channel = None

        if channel is None:
            await interaction.response.send_message(
                "❌ I could not resolve that channel. Please pick a channel the bot can access.",
                ephemeral=True,
            )
            return None

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "❌ Leave notifications need a text channel or thread. Forum, voice, stage, and category channels are not supported.",
                ephemeral=True,
            )
            return None

        return channel

    @admin_group.command(name="configure", description="Configure HR bot for this server")
    @app_commands.describe(
        leave_channel="Text channel or thread for leave request notifications",
        admin_role="Role that can approve leave and view admin reports",
        timezone="Server timezone for display (e.g. UTC, US/Eastern, Asia/Kathmandu)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def hrconfigure(
        self,
        interaction: discord.Interaction,
        leave_channel: discord.app_commands.AppCommandChannel = None,
        admin_role: discord.Role = None,
        timezone: str = "UTC"
    ):
        leave_channel = await self._resolve_leave_channel(interaction, leave_channel)
        if leave_channel is None and interaction.response.is_done():
            return

        await self.db.set_guild_config(
            str(interaction.guild_id),
            leave_channel_id=str(leave_channel.id) if leave_channel else None,
            admin_role_id=str(admin_role.id) if admin_role else None,
            timezone=timezone
        )
        
        embed = discord.Embed(title="⚙️ HR Bot Configured", color=0x57F287)
        embed.add_field(name="Leave Channel", value=leave_channel.mention if leave_channel else "Not set", inline=True)
        embed.add_field(name="Admin Role", value=admin_role.mention if admin_role else "Not set", inline=True)
        embed.add_field(name="Timezone", value=f"`{timezone}`", inline=True)
        embed.set_footer(text="Use /hrconfig overtime to set work hour policies")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(name="hrsetup")
    @commands.has_permissions(administrator=True)
    async def hrsetup_prefix(
        self,
        ctx: commands.Context,
        leave_channel: str = None,
        admin_role: str = None,
        timezone: str = "UTC"
    ):
        leave_obj = await self._resolve_channel_input(ctx.guild, leave_channel) if leave_channel else None
        role_obj = self._resolve_role_input(ctx.guild, admin_role) if admin_role else None

        if leave_channel and leave_obj is None:
            await ctx.send("❌ I could not resolve that leave channel. Use a channel mention like `#leave-requests` or a channel ID.")
            return

        if admin_role and role_obj is None:
            await ctx.send("❌ I could not resolve that admin role. Use a role mention like `@Managers` or a role ID.")
            return

        await self.db.set_guild_config(
            str(ctx.guild.id),
            leave_channel_id=str(leave_obj.id) if leave_obj else None,
            admin_role_id=str(role_obj.id) if role_obj else None,
            timezone=timezone
        )

        embed = discord.Embed(title="⚙️ HR Bot Configured", color=0x57F287)
        embed.add_field(name="Leave Channel", value=leave_obj.mention if leave_obj else "Not set", inline=True)
        embed.add_field(name="Admin Role", value=role_obj.mention if role_obj else "Not set", inline=True)
        embed.add_field(name="Timezone", value=f"`{timezone}`", inline=True)
        await ctx.send(embed=embed)

    @admin_group.command(name="view", description="View current HR bot configuration")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hrview(self, interaction: discord.Interaction):
        config = await self.db.get_guild_config(str(interaction.guild_id))
        ot_config = await self.db.get_overtime_config(str(interaction.guild_id))
        
        embed = discord.Embed(title="⚙️ HR Bot Configuration", color=0x5865F2)
        
        leave_ch = config.get("leave_channel_id")
        embed.add_field(
            name="Leave Channel",
            value=f"<#{leave_ch}>" if leave_ch else "Not configured",
            inline=True
        )
        
        admin_r = config.get("admin_role_id")
        embed.add_field(
            name="Admin Role",
            value=f"<@&{admin_r}>" if admin_r else "Not configured",
            inline=True
        )
        
        embed.add_field(name="Timezone", value=f"`{config.get('timezone', 'UTC')}`", inline=True)
        embed.add_field(name="Daily Work Hours", value=f"`{ot_config['daily_hours']}h`", inline=True)
        embed.add_field(name="Weekly Work Hours", value=f"`{ot_config['weekly_hours']}h`", inline=True)
        embed.add_field(name="Auto Clock-Out After", value=f"`{ot_config['auto_out_hours']}h`", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @overtime_group.command(name="config", description="Configure overtime and auto clock-out rules")
    @app_commands.describe(
        daily_hours="Standard work hours per day (default 8)",
        weekly_hours="Standard work hours per week (default 40)",
        auto_out_hours="Auto clock-out after this many hours if user forgets (default 12, 0 to disable)"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def overtime_config(
        self,
        interaction: discord.Interaction,
        daily_hours: float = 8.0,
        weekly_hours: float = 40.0,
        auto_out_hours: float = 12.0
    ):
        if daily_hours <= 0 or weekly_hours <= 0:
            await interaction.response.send_message("❌ Hours must be positive.", ephemeral=True)
            return
        
        await self.db.set_overtime_config(
            str(interaction.guild_id), daily_hours, weekly_hours, auto_out_hours
        )
        
        embed = discord.Embed(title="⚡ Overtime Config Updated", color=0x57F287)
        embed.add_field(name="Daily Hours", value=f"`{daily_hours}h`", inline=True)
        embed.add_field(name="Weekly Hours", value=f"`{weekly_hours}h`", inline=True)
        embed.add_field(
            name="Auto Clock-Out",
            value=f"`{auto_out_hours}h`" if auto_out_hours > 0 else "Disabled",
            inline=True
        )
        embed.set_footer(text="Overtime = worked hours − daily_hours × days_worked")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = Admin(bot)
    bot.tree.add_command(admin_group)
    bot.tree.add_command(overtime_group)
    await bot.add_cog(cog)
