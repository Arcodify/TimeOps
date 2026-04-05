"""
Leave Requests Cog
/leave request, /leave list, /leave approve, /leave deny
Posts approval embed to configured leave channel
"""

import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime

log = logging.getLogger("Leave")

LEAVE_TYPES = ["Annual Leave", "Sick Leave", "Unpaid Leave", "Maternity/Paternity", "Compassionate", "Other"]

STATUS_COLOR = {
    "pending": 0xFEE75C,
    "approved": 0x57F287,
    "denied": 0xED4245
}


class LeaveApprovalView(discord.ui.View):
    def __init__(self, db, request_id: int):
        super().__init__(timeout=None)
        self.db = db
        self.request_id = request_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="hr:leave_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ You don't have permission to approve leave.", ephemeral=True)
            return
        
        await self.db.update_leave_status(
            self.request_id, "approved",
            str(interaction.user.id), interaction.user.display_name
        )
        
        embed = interaction.message.embeds[0]
        embed.color = STATUS_COLOR["approved"]
        embed.set_field_at(
            len(embed.fields) - 1,
            name="Status",
            value=f"✅ **Approved** by {interaction.user.mention}",
            inline=False
        )
        
        self.clear_items()
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message("✅ Leave approved.", ephemeral=True)
        
        # Notify employee
        requests = await self.db.get_leave_requests(str(interaction.guild_id))
        req = next((r for r in requests if r["id"] == self.request_id), None)
        if req:
            try:
                user = await interaction.guild.fetch_member(int(req["user_id"]))
                await user.send(
                    f"✅ **Leave Approved**\n"
                    f"Your {req['leave_type']} request ({req['start_date']} → {req['end_date']}) "
                    f"has been **approved** by {interaction.user.display_name}."
                )
            except Exception:
                pass

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger, custom_id="hr:leave_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild and not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("❌ You don't have permission to deny leave.", ephemeral=True)
            return
        
        await self.db.update_leave_status(
            self.request_id, "denied",
            str(interaction.user.id), interaction.user.display_name
        )
        
        embed = interaction.message.embeds[0]
        embed.color = STATUS_COLOR["denied"]
        embed.set_field_at(
            len(embed.fields) - 1,
            name="Status",
            value=f"❌ **Denied** by {interaction.user.mention}",
            inline=False
        )
        
        self.clear_items()
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message("Leave denied.", ephemeral=True)
        
        # Notify employee
        requests = await self.db.get_leave_requests(str(interaction.guild_id))
        req = next((r for r in requests if r["id"] == self.request_id), None)
        if req:
            try:
                user = await interaction.guild.fetch_member(int(req["user_id"]))
                await user.send(
                    f"❌ **Leave Denied**\n"
                    f"Your {req['leave_type']} request ({req['start_date']} → {req['end_date']}) "
                    f"has been **denied** by {interaction.user.display_name}."
                )
            except Exception:
                pass


class LeaveRequestModal(discord.ui.Modal, title="Leave Request"):
    leave_type = discord.ui.TextInput(
        label="Leave Type",
        placeholder="Annual Leave / Sick Leave / Unpaid Leave / Other",
        required=True,
        max_length=50
    )
    start_date = discord.ui.TextInput(
        label="Start Date",
        placeholder="YYYY-MM-DD",
        required=True,
        max_length=10,
        min_length=10
    )
    end_date = discord.ui.TextInput(
        label="End Date",
        placeholder="YYYY-MM-DD",
        required=True,
        max_length=10,
        min_length=10
    )
    reason = discord.ui.TextInput(
        label="Reason (optional)",
        placeholder="Brief explanation...",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500
    )

    def __init__(self, db, leave_channel_id: str = None):
        super().__init__()
        self.db = db
        self.leave_channel_id = leave_channel_id

    async def _resolve_leave_channel(self, guild: discord.Guild):
        if not self.leave_channel_id:
            return None

        channel = None
        if hasattr(guild, "get_channel_or_thread"):
            channel = guild.get_channel_or_thread(int(self.leave_channel_id))
        else:
            channel = guild.get_channel(int(self.leave_channel_id))

        if channel is None:
            try:
                channel = await guild.fetch_channel(int(self.leave_channel_id))
            except Exception:
                channel = None

        return channel

    async def on_submit(self, interaction: discord.Interaction):
        # Validate dates
        try:
            start = datetime.strptime(str(self.start_date), "%Y-%m-%d")
            end = datetime.strptime(str(self.end_date), "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date format. Use YYYY-MM-DD.", ephemeral=True
            )
            return
        
        if end < start:
            await interaction.response.send_message("❌ End date must be after start date.", ephemeral=True)
            return
        
        duration = (end - start).days + 1
        
        request_id = await self.db.create_leave_request(
            guild_id=str(interaction.guild_id),
            user_id=str(interaction.user.id),
            username=interaction.user.display_name,
            leave_type=str(self.leave_type),
            start_date=str(self.start_date),
            end_date=str(self.end_date),
            reason=str(self.reason) if self.reason.value else None
        )
        
        embed = discord.Embed(
            title=f"📋 Leave Request #{request_id}",
            color=STATUS_COLOR["pending"],
            timestamp=datetime.utcnow()
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Employee", value=interaction.user.mention, inline=True)
        embed.add_field(name="Leave Type", value=str(self.leave_type), inline=True)
        embed.add_field(name="Duration", value=f"{duration} day{'s' if duration != 1 else ''}", inline=True)
        embed.add_field(name="Start Date", value=str(self.start_date), inline=True)
        embed.add_field(name="End Date", value=str(self.end_date), inline=True)
        if self.reason.value:
            embed.add_field(name="Reason", value=str(self.reason), inline=False)
        embed.add_field(name="Status", value="⏳ **Pending Review**", inline=False)
        embed.set_footer(text=f"Request ID: {request_id}")
        
        view = LeaveApprovalView(self.db, request_id)
        
        # Send to leave channel
        msg = None
        channel_warning = ""
        if self.leave_channel_id:
            try:
                channel = await self._resolve_leave_channel(interaction.guild)
                if channel:
                    msg = await channel.send(embed=embed, view=view)
                    await self.db.set_leave_message_id(request_id, str(msg.id))
            except discord.Forbidden:
                channel_warning = "\n\n⚠️ I could not post this request to the leave channel because the bot lacks access."
            except Exception as e:
                log.error(f"Failed to post to leave channel: {e}")
        
        # Check for holiday overlaps
        holiday_warning = ""
        try:
            import aiosqlite as _aio
            async with _aio.connect(self.db.path) as _db:
                _db.row_factory = _aio.Row
                async with _db.execute(
                    "SELECT name FROM holidays WHERE guild_id=? AND date >= ? AND date <= ? ORDER BY date",
                    (str(interaction.guild_id), str(self.start_date), str(self.end_date))
                ) as cur:
                    hrows = await cur.fetchall()
            if hrows:
                names = ", ".join(dict(r)["name"] for r in hrows)
                holiday_warning = f"\n\n⚠️ Your leave overlaps with company holiday(s): **{names}**"
        except Exception:
            pass

        await interaction.response.send_message(
            f"✅ Your leave request (#{request_id}) has been submitted successfully!\n"
            f"You'll be notified when it's reviewed.{holiday_warning}{channel_warning}",
            ephemeral=True
        )


leave_group = app_commands.Group(name="leave", description="Leave request management")


class Leave(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    @leave_group.command(name="request", description="Submit a leave request")
    async def leave_request(self, interaction: discord.Interaction):
        config = await self.db.get_guild_config(str(interaction.guild_id))
        modal = LeaveRequestModal(self.db, config.get("leave_channel_id"))
        await interaction.response.send_modal(modal)

    @leave_group.command(name="list", description="View your leave requests")
    async def leave_list(self, interaction: discord.Interaction):
        requests = await self.db.get_leave_requests(
            str(interaction.guild_id), user_id=str(interaction.user.id)
        )
        
        if not requests:
            await interaction.response.send_message("You have no leave requests.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"📋 Leave Requests — {interaction.user.display_name}",
            color=0x5865F2,
            timestamp=datetime.utcnow()
        )
        
        for r in requests[:10]:
            status_icon = {"pending": "⏳", "approved": "✅", "denied": "❌"}.get(r["status"], "❓")
            embed.add_field(
                name=f"#{r['id']} — {r['leave_type']} {status_icon}",
                value=f"`{r['start_date']}` → `{r['end_date']}`\n{r['status'].upper()}",
                inline=True
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leave_group.command(name="pending", description="View all pending leave requests (Admin)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leave_pending(self, interaction: discord.Interaction):
        requests = await self.db.get_leave_requests(str(interaction.guild_id), status="pending")
        
        if not requests:
            await interaction.response.send_message("No pending leave requests! 🎉", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"⏳ Pending Leave Requests ({len(requests)})",
            color=0xFEE75C,
            timestamp=datetime.utcnow()
        )
        for r in requests[:15]:
            embed.add_field(
                name=f"#{r['id']} — {r['username']}",
                value=f"**{r['leave_type']}**\n{r['start_date']} → {r['end_date']}",
                inline=True
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    cog = Leave(bot)
    bot.tree.add_command(leave_group)
    await bot.add_cog(cog)
    # Re-register persistent leave approval views (load from DB on startup)
