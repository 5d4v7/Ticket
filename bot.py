import asyncio
import html
import io
import json
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config


DATA_FILE = "tickets.json"

TICKET_TYPES = {
    "purchase": {
        "label": "Purchase",
        "description": "Buy Axonic products or services",
        "emoji": "🛒",
        "category_id": config.PREMIUM_CATEGORY_ID,
        "prefix": "purchase",
    },
    "support": {
        "label": "Support",
        "description": "Get help with an issue or question",
        "emoji": "🛠️",
        "category_id": config.SUPPORT_CATEGORY_ID,
        "prefix": "support",
    },
    "bug": {
        "label": "Bug Report",
        "description": "Report a problem with an Axonic script",
        "emoji": "🐛",
        "category_id": config.BUG_CATEGORY_ID,
        "prefix": "bug",
    },
}


def load_data():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tickets": {}}


def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


data = load_data()


def is_staff(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator
        or any(role.id == config.STAFF_ROLE_ID for role in member.roles)
    )


def get_ticket(channel):
    return data["tickets"].get(str(channel.id))


def get_open_ticket_count(user_id):
    return sum(
        1 for t in data["tickets"].values()
        if str(t.get("creator_id")) == str(user_id) and not t.get("closed", False)
    )


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def safe_name(text):
    text = "".join(c.lower() if c.isalnum() else "-" for c in text)
    return text.strip("-")[:45] or "ticket"


async def make_transcript(channel, ticket):
    messages = []
    async for message in channel.history(limit=None, oldest_first=True):
        stamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = html.escape(f"{message.author} ({message.author.id})")
        content = html.escape(message.content or "").replace("\n", "<br>")

        attachments = ""
        for attachment in message.attachments:
            url = html.escape(attachment.url, quote=True)
            name = html.escape(attachment.filename)
            attachments += f'<br><a href="{url}">{name}</a>'

        messages.append(
            f"""
            <div class="message">
              <div class="meta"><b>{author}</b> · {stamp}</div>
              <div class="content">{content}{attachments}</div>
            </div>
            """
        )

    ticket_type = html.escape(ticket.get("type", "Unknown"))
    creator = html.escape(str(ticket.get("creator_id", "Unknown")))
    claimed = html.escape(str(ticket.get("claimed_by", "Unclaimed")))

    document = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Axonic Ticket Transcript - {html.escape(channel.name)}</title>
<style>
body {{
  background:#0b0b10;color:#eee;font-family:Arial,sans-serif;
  margin:0;padding:30px;
}}
.container {{
  max-width:1000px;margin:auto;background:#12121a;
  border:1px solid #292936;border-radius:16px;padding:25px;
}}
h1 {{ color:#8b5cf6;margin-bottom:6px; }}
.meta-box {{
  background:#191922;border-radius:12px;padding:15px;margin:20px 0;
  color:#bbb;
}}
.message {{
  padding:14px 0;border-bottom:1px solid #272733;
}}
.meta {{ color:#8b5cf6;font-size:14px;margin-bottom:7px; }}
.content {{ color:#e5e5e5;line-height:1.5;word-wrap:break-word; }}
a {{ color:#a78bfa; }}
.footer {{ margin-top:25px;color:#777;font-size:12px; }}
</style>
</head>
<body>
<div class="container">
<h1>Axonic Ticket Transcript</h1>
<div class="meta-box">
<b>Ticket:</b> {html.escape(channel.name)}<br>
<b>Type:</b> {ticket_type}<br>
<b>Creator ID:</b> {creator}<br>
<b>Claimed By:</b> {claimed}<br>
<b>Created:</b> {html.escape(str(ticket.get("created_at", "Unknown")))}
</div>
{"".join(messages)}
<div class="footer">Axonic Support • Automated transcript</div>
</div>
</body>
</html>"""

    return document.encode("utf-8")


async def send_transcript(channel, ticket, delete_after=False):
    raw = await make_transcript(channel, ticket)
    filename = f"{channel.name}-transcript.html"
    file_for_log = discord.File(io.BytesIO(raw), filename=filename)

    transcript_channel = channel.guild.get_channel(config.TRANSCRIPT_CHANNEL_ID)
    embed = discord.Embed(
        title="📄 Ticket Transcript",
        description=f"Transcript for **{channel.name}**",
        color=config.AXONIC_PURPLE,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Type", value=ticket.get("type", "Unknown"), inline=True)
    embed.add_field(name="Creator", value=f"<@{ticket.get('creator_id')}>", inline=True)
    embed.add_field(
        name="Claimed By",
        value=f"<@{ticket['claimed_by']}>" if ticket.get("claimed_by") else "Unclaimed",
        inline=True,
    )
    embed.set_thumbnail(url=config.LOGO_URL)

    if transcript_channel:
        await transcript_channel.send(embed=embed, file=file_for_log)

    creator = channel.guild.get_member(int(ticket["creator_id"]))
    claimed = (
        channel.guild.get_member(int(ticket["claimed_by"]))
        if ticket.get("claimed_by")
        else None
    )

    # Send the transcript to both the ticket creator and the staff member who claimed it.
    recipients = []
    if creator:
        recipients.append(creator)
    if claimed and claimed.id != creator.id if creator else True:
        recipients.append(claimed)

    for recipient in recipients:
        try:
            await recipient.send(
                embed=embed,
                file=discord.File(io.BytesIO(raw), filename=filename),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    return raw


class TicketModal(discord.ui.Modal):
    def __init__(self, ticket_key):
        info = TICKET_TYPES[ticket_key]
        super().__init__(title=f"{info['label']} Ticket")
        self.ticket_key = ticket_key

        self.subject = discord.ui.TextInput(
            label="Subject",
            placeholder="Briefly describe what you need help with...",
            max_length=100,
            required=True,
        )
        self.details = discord.ui.TextInput(
            label="Details",
            placeholder="Give us as much information as possible...",
            style=discord.TextStyle.paragraph,
            max_length=1500,
            required=True,
        )
        self.add_item(self.subject)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        if get_open_ticket_count(member.id) >= config.MAX_OPEN_TICKETS_PER_USER:
            return await interaction.response.send_message(
                f"❌ You already have {config.MAX_OPEN_TICKETS_PER_USER} open tickets.",
                ephemeral=True,
            )

        info = TICKET_TYPES[self.ticket_key]
        category = guild.get_channel(info["category_id"])

        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message(
                "❌ The ticket category is not configured correctly.",
                ephemeral=True,
            )

        staff_role = guild.get_role(config.STAFF_ROLE_ID)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
        }

        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_messages=True
            )

        channel_name = f"{info['prefix']}-{safe_name(member.display_name)}"
        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Axonic {info['label']} | Creator: {member.id}",
            reason="Axonic ticket created",
        )

        ticket = {
            "creator_id": member.id,
            "type": info["label"],
            "type_key": self.ticket_key,
            "created_at": now_iso(),
            "claimed_by": None,
            "closed": False,
            "priority": False,
        }
        data["tickets"][str(channel.id)] = ticket
        save_data()

        embed = discord.Embed(
            title=f"{info['emoji']} {info['label']}",
            description=(
                f"Welcome {member.mention}!\n\n"
                f"**Subject:** {discord.utils.escape_markdown(self.subject.value)}\n"
                f"**Details:** {discord.utils.escape_markdown(self.details.value)}\n\n"
                "A member of the Axonic team will be with you shortly."
            ),
            color=config.AXONIC_PURPLE,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=config.LOGO_URL)

        view = TicketControls()
        await channel.send(
            content=f"{member.mention} <@&{config.STAFF_ROLE_ID}>",
            embed=embed,
            view=view,
        )

        await interaction.response.send_message(
            f"✅ Your ticket has been created: {channel.mention}",
            ephemeral=True,
        )


class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Purchase",
                description="Buy Axonic products or services",
                emoji="🛒",
                value="purchase",
            ),
            discord.SelectOption(
                label="Support",
                description="Get help with an issue or question",
                emoji="🛠️",
                value="support",
            ),
            discord.SelectOption(
                label="Bug Report",
                description="Report bugs in Axonic scripts",
                emoji="🐛",
                value="bug",
            ),
        ]
        super().__init__(
            placeholder="Select a ticket type...",
            options=options,
            custom_id="axonic_ticket_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketModal(self.values[0]))


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.primary, custom_id="axonic_claim")
    async def claim(self, interaction, button):
        await claim_ticket(interaction)

    @discord.ui.button(label="Unclaim", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="axonic_unclaim")
    async def unclaim(self, interaction, button):
        await unclaim_ticket(interaction)

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="axonic_close")
    async def close(self, interaction, button):
        await close_ticket(interaction)

    @discord.ui.button(label="Transcript", emoji="📄", style=discord.ButtonStyle.secondary, custom_id="axonic_transcript")
    async def transcript(self, interaction, button):
        ticket = get_ticket(interaction.channel)
        if not ticket:
            return await interaction.response.send_message("❌ This is not a ticket.", ephemeral=True)
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await send_transcript(interaction.channel, ticket)
        await interaction.followup.send("✅ Transcript generated.", ephemeral=True)


async def claim_ticket(interaction):
    ticket = get_ticket(interaction.channel)
    if not ticket:
        return await interaction.response.send_message("❌ This is not a ticket.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
    if ticket.get("claimed_by"):
        return await interaction.response.send_message(
            f"❌ Already claimed by <@{ticket['claimed_by']}>.", ephemeral=True
        )

    ticket["claimed_by"] = interaction.user.id
    save_data()

    await interaction.response.send_message(
        f"🙋 Ticket claimed by {interaction.user.mention}."
    )


async def unclaim_ticket(interaction):
    ticket = get_ticket(interaction.channel)
    if not ticket:
        return await interaction.response.send_message("❌ This is not a ticket.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
    if not ticket.get("claimed_by"):
        return await interaction.response.send_message("❌ This ticket isn't claimed.", ephemeral=True)

    ticket["claimed_by"] = None
    save_data()
    await interaction.response.send_message("↩️ Ticket unclaimed.")


async def close_ticket(interaction):
    ticket = get_ticket(interaction.channel)
    if not ticket:
        return await interaction.response.send_message("❌ This is not a ticket.", ephemeral=True)
    if not is_staff(interaction.user) and interaction.user.id != ticket["creator_id"]:
        return await interaction.response.send_message("❌ You cannot close this ticket.", ephemeral=True)

    await interaction.response.defer()
    ticket["closed"] = True
    ticket["closed_at"] = now_iso()
    save_data()

    await send_transcript(interaction.channel, ticket)

    for target, perms in list(interaction.channel.overwrites.items()):
        if isinstance(target, discord.Member) and target.id == ticket["creator_id"]:
            await interaction.channel.set_permissions(target, send_messages=False)

    await interaction.followup.send("🔒 Ticket closed. Transcript saved and sent.")
    await asyncio.sleep(config.DELETE_DELAY_SECONDS)

    try:
        await interaction.channel.delete(reason="Axonic ticket closed")
    except discord.HTTPException:
        pass


class TicketGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="ticket", description="Axonic ticket system")

    async def interaction_check(self, interaction):
        return True

    @app_commands.command(name="panel", description="Send the Axonic ticket panel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction):
        embed = discord.Embed(
            title="Axonic Support",
            description=(
                "Need help, or want to buy something from us? Pick a ticket type below "
                "and fill out the short form — our team will take it from there."
            ),
            color=config.AXONIC_PURPLE,
        )
        embed.set_thumbnail(url=config.LOGO_URL)
        embed.add_field(
            name="Before you open a ticket",
            value=(
                "We're not online 24/7, so please be patient after submitting — "
                "a staff member will respond as soon as they're available."
            ),
            inline=False,
        )
        embed.set_image(url=config.BANNER_URL)
        embed.set_footer(text="Axonic • Support")

        await interaction.channel.send(embed=embed, view=TicketPanelView())
        await interaction.response.send_message("✅ Axonic ticket panel sent.", ephemeral=True)

    @app_commands.command(name="close", description="Close the current ticket")
    async def close(self, interaction):
        await close_ticket(interaction)

    @app_commands.command(name="delete", description="Delete the current ticket")
    async def delete(self, interaction):
        ticket = get_ticket(interaction.channel)
        if not ticket:
            return await interaction.response.send_message("❌ This is not a ticket.", ephemeral=True)
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.defer()
        await send_transcript(interaction.channel, ticket)
        data["tickets"].pop(str(interaction.channel.id), None)
        save_data()
        await interaction.channel.delete(reason="Axonic ticket deleted")

    @app_commands.command(name="claim", description="Claim the current ticket")
    async def claim(self, interaction):
        await claim_ticket(interaction)

    @app_commands.command(name="unclaim", description="Unclaim the current ticket")
    async def unclaim(self, interaction):
        await unclaim_ticket(interaction)

    @app_commands.command(name="rename", description="Rename the current ticket")
    @app_commands.describe(name="New ticket name")
    async def rename(self, interaction, name: str):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if not get_ticket(interaction.channel):
            return await interaction.response.send_message("❌ This is not a ticket.", ephemeral=True)
        await interaction.channel.edit(name=safe_name(name))
        await interaction.response.send_message(f"✏️ Renamed to `{safe_name(name)}`.")

    @app_commands.command(name="add", description="Add a user to the current ticket")
    @app_commands.describe(user="User to add")
    async def add(self, interaction, user: discord.Member):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.channel.set_permissions(
            user, view_channel=True, send_messages=True, read_message_history=True
        )
        await interaction.response.send_message(f"➕ Added {user.mention}.")

    @app_commands.command(name="remove", description="Remove a user from the current ticket")
    @app_commands.describe(user="User to remove")
    async def remove(self, interaction, user: discord.Member):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.channel.set_permissions(user, overwrite=None)
        await interaction.response.send_message(f"➖ Removed {user.mention}.")

    @app_commands.command(name="transcript", description="Generate the current ticket transcript")
    async def transcript(self, interaction):
        ticket = get_ticket(interaction.channel)
        if not ticket or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only / not a ticket.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await send_transcript(interaction.channel, ticket)
        await interaction.followup.send("📄 Transcript sent to the transcript channel and relevant DMs.", ephemeral=True)

    @app_commands.command(name="setup", description="Show the configured Axonic ticket setup")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction):
        await interaction.response.send_message(
            f"**Axonic Ticket Setup**\n"
            f"Server: `{config.SERVER_ID}`\n"
            f"Support category: `{config.SUPPORT_CATEGORY_ID}`\n"
            f"Premium category: `{config.PREMIUM_CATEGORY_ID}`\n"
            f"Bug category: `{config.BUG_CATEGORY_ID}`\n"
            f"Transcript channel: `{config.TRANSCRIPT_CHANNEL_ID}`\n"
            f"Staff role: `{config.STAFF_ROLE_ID}`",
            ephemeral=True,
        )

    @app_commands.command(name="settings", description="View ticket settings")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings(self, interaction):
        await interaction.response.send_message(
            f"Max open tickets per user: `{config.MAX_OPEN_TICKETS_PER_USER}`\n"
            f"Delete delay: `{config.DELETE_DELAY_SECONDS}s`",
            ephemeral=True,
        )

    @app_commands.command(name="categories", description="Show ticket category IDs")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def categories(self, interaction):
        await interaction.response.send_message(
            f"🛠️ Support: `{config.SUPPORT_CATEGORY_ID}`\n"
            f"💎 Premium: `{config.PREMIUM_CATEGORY_ID}`\n"
            f"🐛 Bugs: `{config.BUG_CATEGORY_ID}`",
            ephemeral=True,
        )

    @app_commands.command(name="staffrole", description="Show the configured staff role")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def staffrole(self, interaction):
        await interaction.response.send_message(
            f"Staff role: <@&{config.STAFF_ROLE_ID}>",
            ephemeral=True,
        )

    @app_commands.command(name="logs", description="Show the transcript/log channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def logs(self, interaction):
        await interaction.response.send_message(
            f"Transcript channel: <#{config.TRANSCRIPT_CHANNEL_ID}>",
            ephemeral=True,
        )

    @app_commands.command(name="reset", description="Clear stored ticket data")
    @app_commands.checks.has_permissions(administrator=True)
    async def reset(self, interaction):
        data["tickets"] = {}
        save_data()
        await interaction.response.send_message("⚠️ Stored ticket data has been reset.", ephemeral=True)

    @app_commands.command(name="stats", description="Show ticket statistics")
    async def stats(self, interaction):
        tickets = list(data["tickets"].values())
        total = len(tickets)
        open_count = sum(not t.get("closed", False) for t in tickets)
        claimed = sum(bool(t.get("claimed_by")) for t in tickets)
        support = sum(t.get("type") == "Support" for t in tickets)
        purchase = sum(t.get("type") == "Purchase" for t in tickets)
        bugs = sum(t.get("type") == "Bug Report" for t in tickets)

        embed = discord.Embed(title="📊 Axonic Ticket Statistics", color=config.AXONIC_PURPLE)
        embed.add_field(name="Total", value=str(total), inline=True)
        embed.add_field(name="Open", value=str(open_count), inline=True)
        embed.add_field(name="Claimed", value=str(claimed), inline=True)
        embed.add_field(name="Support", value=str(support), inline=True)
        embed.add_field(name="Purchase", value=str(purchase), inline=True)
        embed.add_field(name="Bug Reports", value=str(bugs), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="list", description="List currently open tickets")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction):
        lines = []
        for cid, ticket in data["tickets"].items():
            if ticket.get("closed"):
                continue
            channel = interaction.guild.get_channel(int(cid))
            if channel:
                lines.append(
                    f"{channel.mention} — {ticket.get('type')} — "
                    f"<@{ticket.get('creator_id')}>"
                )

        await interaction.response.send_message(
            "\n".join(lines[:50]) if lines else "No open tickets.",
            ephemeral=True,
        )

    @app_commands.command(name="info", description="Show information about the current ticket")
    async def info(self, interaction):
        ticket = get_ticket(interaction.channel)
        if not ticket:
            return await interaction.response.send_message("❌ This is not a ticket.", ephemeral=True)

        embed = discord.Embed(title="🎫 Ticket Information", color=config.AXONIC_PURPLE)
        embed.add_field(name="Type", value=ticket.get("type", "Unknown"))
        embed.add_field(name="Creator", value=f"<@{ticket.get('creator_id')}>")
        embed.add_field(
            name="Claimed By",
            value=f"<@{ticket['claimed_by']}>" if ticket.get("claimed_by") else "Unclaimed",
        )
        embed.add_field(name="Priority", value="⭐ Yes" if ticket.get("priority") else "No")
        embed.add_field(name="Created", value=ticket.get("created_at", "Unknown"), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="forceclose", description="Force-close a ticket")
    @app_commands.describe(ticket="Ticket channel")
    async def forceclose(self, interaction, ticket: discord.TextChannel):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if str(ticket.id) not in data["tickets"]:
            return await interaction.response.send_message("❌ That is not a registered ticket.", ephemeral=True)
        await interaction.response.send_message(f"🔒 Force-closing {ticket.mention}.")
        fake = interaction
        # Re-use close flow by acting directly on the selected channel.
        t = data["tickets"][str(ticket.id)]
        t["closed"] = True
        t["closed_at"] = now_iso()
        save_data()
        await send_transcript(ticket, t)
        await ticket.delete(reason=f"Force-closed by {interaction.user}")

    @app_commands.command(name="force-delete", description="Force-delete a ticket")
    @app_commands.describe(ticket="Ticket channel")
    async def force_delete(self, interaction, ticket: discord.TextChannel):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if str(ticket.id) not in data["tickets"]:
            return await interaction.response.send_message("❌ That is not a registered ticket.", ephemeral=True)
        await send_transcript(ticket, data["tickets"][str(ticket.id)])
        data["tickets"].pop(str(ticket.id), None)
        save_data()
        await interaction.response.send_message(f"🗑️ Deleting {ticket.mention}.")
        await ticket.delete(reason=f"Force-deleted by {interaction.user}")

    @app_commands.command(name="transfer", description="Transfer a ticket to another staff member")
    @app_commands.describe(user="Staff member to transfer the ticket to")
    async def transfer(self, interaction, user: discord.Member):
        ticket = get_ticket(interaction.channel)
        if not ticket or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only / not a ticket.", ephemeral=True)
        if not is_staff(user):
            return await interaction.response.send_message("❌ That user is not in the configured staff role.", ephemeral=True)

        ticket["claimed_by"] = user.id
        save_data()
        await interaction.response.send_message(f"🔄 Ticket transferred to {user.mention}.")

    @app_commands.command(name="priority", description="Toggle ticket priority")
    async def priority(self, interaction):
        ticket = get_ticket(interaction.channel)
        if not ticket or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only / not a ticket.", ephemeral=True)

        ticket["priority"] = not ticket.get("priority", False)
        save_data()
        await interaction.response.send_message(
            "⭐ Ticket marked as priority." if ticket["priority"] else "Ticket priority removed."
        )

    @app_commands.command(name="note", description="Add an internal staff note")
    @app_commands.describe(text="Internal note")
    async def note(self, interaction, text: str):
        ticket = get_ticket(interaction.channel)
        if not ticket or not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Staff only / not a ticket.", ephemeral=True)

        await interaction.response.send_message(
            f"📝 **Staff Note — {interaction.user.display_name}**\n{text}"
        )


class AxonicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self):
        self.add_view(TicketPanelView())
        self.add_view(TicketControls())
        self.tree.add_command(TicketGroup())
        guild = discord.Object(id=config.SERVER_ID)
        await self.tree.sync(guild=guild)
        print("Axonic ticket commands synced.")

    async def on_ready(self):
        print(f"Logged in as {self.user} ({self.user.id})")
        print(f"Connected to {len(self.guilds)} server(s).")


bot = AxonicBot()

if __name__ == "__main__":
    token = config.BOT_TOKEN
    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN is not set. Add it as a Railway environment variable."
        )
    bot.run(token)
