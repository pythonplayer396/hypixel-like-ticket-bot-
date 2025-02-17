import discord
from discord import app_commands, ui
from discord.ext import commands
from config import TICKET_CATEGORY_ID
from utils.db import db
import logging
from datetime import datetime
import asyncio
import io

# Ticket categories with emojis
TICKET_CATEGORIES = {
    "support": {"name": "Support Tickets", "emoji": "🎫", "description": "Get help with general issues"},
    "rank": {"name": "Rank Purchases", "emoji": "💎", "description": "Buy server ranks"},
    "staff": {"name": "Staff Applications", "emoji": "👥", "description": "Apply for staff position"},
    "bug": {"name": "Bug Reports", "emoji": "🐛", "description": "Report bugs or technical issues"},
    "appeal": {"name": "Ban Appeals", "emoji": "⚖️", "description": "Appeal a punishment"},
    "report": {"name": "Player Reports", "emoji": "🚫", "description": "Report a player"}
}

# Payment configuration and payment methods list
UPI_ID = "your_upi_id_here"
QR_CODE_PATH = "path_to_your_qr_code_image.png"
PAYMENT_METHODS = ["UPI", "PayPal", "Credit Card"]

# Permissions checks
def is_admin():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)

def is_ticket_creator(ticket_creator_id):
    async def predicate(interaction: discord.Interaction):
        return interaction.user.id == ticket_creator_id
    return app_commands.check(predicate)

# ───────────── UI Components ─────────────

# Priority selection dropdown – available only to admins/staff; one-time use
class PrioritySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Low 🟢", value="low"),
            discord.SelectOption(label="Medium 🟡", value="medium"),
            discord.SelectOption(label="High 🔴", value="high"),
            discord.SelectOption(label="Urgent ⚡", value="urgent")
        ]
        super().__init__(placeholder="Set ticket priority...", options=options, custom_id="priority_select")

    async def callback(self, interaction: discord.Interaction):
        # Only allow admins or users with Staff role to set priority
        if not (interaction.user.guild_permissions.administrator or discord.utils.get(interaction.user.roles, name="Staff")):
            return await interaction.response.send_message("You are not allowed to set priority.", ephemeral=True)

        await interaction.response.defer()
        if interaction.channel.topic:
            ticket_id = int(interaction.channel.name.split('-')[1])
            db.update_ticket_priority(ticket_id, self.values[0])
        else:
            await interaction.response.send_message("Channel topic is None. Cannot update priority.", ephemeral=True)
        
        embed = discord.Embed(
            title="Priority Updated",
            description=f"Ticket priority set to: {self.values[0].upper()}",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed)

        priority_channel = discord.utils.get(interaction.guild.text_channels, name="priority")
        if not priority_channel:
            priority_channel = await interaction.guild.create_text_channel(name="priority")

        if self.values[0] in ["high", "urgent"]:
            staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
            admin_role = discord.utils.get(interaction.guild.roles, name="Admin")
            msg = f"🔴 High Priority Ticket #{ticket_id}" if self.values[0] == "high" else f"⚡ Urgent Ticket #{ticket_id}"
            if staff_role:
                msg += f" {staff_role.mention}"
            if self.values[0] == "urgent" and admin_role:
                msg += f" {admin_role.mention}"
            if interaction.guild.owner:
                msg += f" {interaction.guild.owner.mention}"
            await priority_channel.send(msg)

        # Disable after use (one-time use)
        self.disabled = True
        await interaction.message.edit(view=self.view)

# Call Staff button (only ticket creator can use)
class CallStaffButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Call Staff", style=discord.ButtonStyle.primary, emoji="📢", custom_id="call_staff")

        
    async def callback(self, interaction: discord.Interaction):
        # Check if the user is the ticket creator
        if interaction.channel.topic:
            creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
            if interaction.user.id != creator_id:
                return await interaction.response.send_message("Only the ticket creator can call staff.", ephemeral=True)
        else:
            # Handle the case where the channel topic is None
            print("Channel topic is None")

        confirm_view = discord.ui.View()
        confirm_view.add_item(ConfirmCallStaffButton())
        await interaction.response.send_message(
            "Are you sure you want to ping staff members?",
            view=confirm_view,
            ephemeral=True
        )

# Confirmation button for Call Staff – pings staff and pins the message
class ConfirmCallStaffButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Confirm", style=discord.ButtonStyle.danger, custom_id="confirm_call_staff")

    async def callback(self, interaction: discord.Interaction):
        creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
        if interaction.user.id != creator_id:
            return await interaction.response.send_message("Only the ticket creator can confirm.", ephemeral=True)

        staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
        if staff_role:
            msg = f"{staff_role.mention} {interaction.user.mention} needs assistance!"
            message = await interaction.channel.send(msg)
            try:
                await message.pin()
            except Exception as e:
                logging.error(f"Failed to pin message: {e}")
            await interaction.response.edit_message(content="Staff has been notified!", view=None)
            # Disable the Call Staff button after confirmation
            call_staff_button = discord.utils.get(self.view.children, custom_id="call_staff")
            if call_staff_button:
                call_staff_button.disabled = True
                await interaction.message.edit(view=self.view)
        else:
            await interaction.response.edit_message(content="Staff role not found!", view=None)

# Payment Method dropdown – shown in rank-purchase tickets after rank selection
class PaymentMethodSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=method, value=method.lower()) for method in db.get_payment_methods()]
        super().__init__(placeholder="Select Payment Method...", options=options, custom_id="payment_method_select")

    async def callback(self, interaction: discord.Interaction):
        if interaction.channel.topic:
            creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
            if interaction.user.id != creator_id:
                return await interaction.response.send_message("Only the ticket creator can select a payment method.", ephemeral=True)

            selected_method = self.values[0]
            await interaction.response.send_message(f"You selected {selected_method} as your payment method.", ephemeral=True)

            # Update the ticket with the selected payment method
            ticket_id = int(interaction.channel.name.split('-')[1])
            db.store_transaction_info(ticket_id, f"Selected Payment Method: {selected_method}")
        else:
            await interaction.response.send_message("Channel topic is None. Cannot select payment method.", ephemeral=True)

# UPI ID button – only visible to ticket creator
class UPIButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="UPI ID", style=discord.ButtonStyle.blurple, custom_id="upi_id")

    async def callback(self, interaction: discord.Interaction):
        creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
        if interaction.user.id != creator_id:
            return await interaction.response.send_message("Only the ticket creator can view this.", ephemeral=True)

        await interaction.response.send_message(f"**UPI ID:** `{UPI_ID}`", ephemeral=True)

# QR Code button – only visible to ticket creator
class QRCodeButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="QR CODE", style=discord.ButtonStyle.blurple, custom_id="qr_code")

    async def callback(self, interaction: discord.Interaction):
        creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
        if interaction.user.id != creator_id:
            return await interaction.response.send_message("Only the ticket creator can view this.", ephemeral=True)

        file = discord.File(QR_CODE_PATH, filename="payment_qr.png")
        await interaction.response.send_message("Scan QR Code to complete payment:", file=file, ephemeral=True)

# Complete Transaction button – only visible to ticket creator; opens a modal
class TransactionButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Complete Transaction", style=discord.ButtonStyle.green, custom_id="complete_transaction")

    async def callback(self, interaction: discord.Interaction):
        creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
        if interaction.user.id != creator_id:
            return await interaction.response.send_message("Only the ticket creator can complete the transaction.", ephemeral=True)

        await interaction.response.send_modal(CompleteTransactionModal())

# Modal for completing transaction – updates ticket embed and removes payment buttons
class CompleteTransactionModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Complete Transaction")
        self.app_used = ui.TextInput(label="App Used for Payment", required=True)
        self.user_id = ui.TextInput(label="Your ID", required=True)
        self.utr_number = ui.TextInput(label="UTR Number", required=True)
        self.date = ui.TextInput(label="Date (DD/MM/YYYY)", required=True)
        self.time = ui.TextInput(label="Time (HH:MM)", required=True)
        self.add_item(self.app_used)
        self.add_item(self.user_id)
        self.add_item(self.utr_number)
        self.add_item(self.date)
        self.add_item(self.time)

    async def on_submit(self, interaction: discord.Interaction):
        ticket_id = int(interaction.channel.name.split('-')[1])
        transaction_info = (
            f"App Used: {self.app_used.value}\n"
            f"User  ID: {self.user_id.value}\n"
            f"UTR Number: {self.utr_number.value}\n"
            f"Date: {self.date.value}\n"
            f"Time: {self.time.value}"
        )
        db.store_transaction_info(ticket_id, transaction_info)

        # Update the ticket’s initial embed to show transaction info at the top and remove payment buttons
        async for msg in interaction.channel.history(limit=10, oldest_first=True):
            if msg.author == interaction.guild.me and msg.embeds:
                embed = msg.embeds[0]
                new_description = f"{transaction_info}\n\n" + embed.description
                embed.description = new_description
                await msg.edit(embed=embed, view=None)
                break

        # Log the transaction information in the ticket-logs channel
        logs_channel = discord.utils.get(interaction.guild.text_channels, name="ticket-logs")
        if not logs_channel:
            logs_channel = await interaction.guild.create_text_channel(name="ticket-logs", topic="Ticket transcripts and logs")

        embed = discord.Embed(
            title=f"Transaction Details for Ticket #{ticket_id}",
            description=transaction_info,
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        await logs_channel.send(embed=embed)

        await interaction.response.send_message("Transaction details recorded. PLEASE SHARE THE RECEIPT OR SCREENSHOT OF THE PAYMENT IN THE CHAT.", ephemeral=True)

        # Remove the payment buttons
        view: TicketManageView = self.view
        view.clear_items()
        await interaction.message.edit(view=view)

# Feedback modal – shown to ticket creator on closing the ticket
class FeedbackModal(discord.ui.Modal):
    def __init__(self, ticket_id: int):
        super().__init__(title="Ticket Feedback")
        self.ticket_id = ticket_id
        self.rating = discord.ui.TextInput(
            label="Rate your experience (1-5 stars)",
            placeholder="Enter a number between 1 and 5",
            required=True,
            style=discord.TextStyle.short
        )
        self.feedback = ui.TextInput(label="Feedback", placeholder="Please provide your feedback.", required=True, style=discord.TextStyle.paragraph)
        self.add_item(self.rating)
        self.add_item(self.feedback)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.rating.value.isdigit() or not (1 <= int(self.rating.value) <= 5):
            return await interaction.response.send_message("Rating must be a number between 1 and 5.", ephemeral=True)

        rating_value = self.rating.value
        db.store_ticket_feedback(self.ticket_id, f"{self.feedback.value}\nRating: {rating_value} stars")

        feedback_channel = discord.utils.get(interaction.guild.text_channels, name="feedback")
        if not feedback_channel:
            feedback_channel = await interaction.guild.create_text_channel(name="feedback")

        embed = discord.Embed(
            title=f"Feedback for Ticket #{self.ticket_id}",
            description=f"Rating: {rating_value} stars\nFeedback: {self.feedback.value}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        await feedback_channel.send(embed=embed)

        await interaction.response.send_message("Thank you for your feedback. Ticket will be closed in 15 seconds.", ephemeral=True)
        await asyncio.sleep(15)
        await interaction.channel.delete()

# Ticket category selection dropdown
class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=data["name"], value=category, emoji=data["emoji"], description=data["description"])
            for category, data in TICKET_CATEGORIES.items()
        ]
        super().__init__(placeholder="Select ticket category...", min_values=1, max_values=1, options=options, custom_id="category_select")

    async def callback(self, interaction: discord.Interaction):
        category = self.values[0]
        if category == "rank":
            view = RankSelectView()
            await interaction.response.send_message(
                "Select your desired rank and payment method:",
                view=view,
                ephemeral=True
            )
        elif category == "staff":
            await interaction.response.send_modal(StaffApplicationModal())
        elif category == "report":
            await interaction.response.send_modal(ReportPlayerModal())
        elif category == "appeal":
            await interaction.response.send_modal(BanAppealModal())
        elif category == "bug":
            await interaction.response.send_modal(BugReportModal())
        else:
            await interaction.response.send_modal(TicketModal(category))

# Rank selection view – now also adds a Payment Method dropdown
class RankSelectView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(RankSelect())

class RankSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=rank, value=rank.lower()) for rank in db.get_ranks()]
        super().__init__(placeholder="Select your rank...", options=options, custom_id="rank_select")

    async def callback(self, interaction: discord.Interaction):
        if interaction.channel.topic:
            creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
            if interaction.user.id != creator_id:
                return await interaction.response.send_message("Only the ticket creator can select a rank.", ephemeral=True)

            selected_rank = self.values[0]
            await interaction.response.send_message(f"You selected {selected_rank} as your rank.", ephemeral=True)

            # Show the payment method dropdown after rank selection
            payment_view = discord.ui.View()
            payment_view.add_item(PaymentMethodSelect())
            await interaction.followup.send("Now, select your payment method:", view=payment_view, ephemeral=True)
        else:
            # Set the channel topic if it is None
            await interaction.channel.edit(topic=f"Ticket for {interaction.user.name} ({interaction.user.id})")
            await interaction.response.send_message("Channel topic was None. It has been set now. Please try selecting the rank again.", ephemeral=True)

# Main ticket panel view – now only the dropdown is shown
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())

# Ticket management view – holds all buttons
class TicketManageView(discord.ui.View):
    def __init__(self, ticket_number: int):
        super().__init__(timeout=None)
        self.ticket_number = ticket_number
        self.claimed_by = None
        self.add_item(PrioritySelect())
        self.add_item(CallStaffButton())
        self.add_item(ClaimTicketButton(ticket_number))
        self.add_item(CloseTicketButton(ticket_number))

    def add_payment_buttons(self):
        self.add_item(QRCodeButton())
        self.add_item(UPIButton())
        self.add_item(TransactionButton())

    async def update_permissions(self, channel, interaction, claimed: bool):
        try:
            creator_id = int(channel.topic.split('(')[-1].split(')')[0])
            creator = interaction.guild.get_member(creator_id)
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                creator: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
            }
            admin_role = discord.utils.get(interaction.guild.roles, permissions=discord.Permissions(administrator=True))
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            if claimed:
                overwrites[interaction.user] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
                if staff_role:
                    overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)
            else:
                staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
                if staff_role:
                    overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            await channel.edit(overwrites=overwrites)
            return True
        except Exception as e:
            logging.error(f"Error updating permissions: {e}")
            return False

# Claim/Unclaim Ticket button – for admins or staff
class ClaimTicketButton(discord.ui.Button):
    def __init__(self, ticket_number: int):
        super().__init__(label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="claim_ticket")
        self.ticket_number = ticket_number

    async def callback(self, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or discord.utils.get(interaction.user.roles, name="Staff")):
            return await interaction.response.send_message("You don't have permission to claim tickets.", ephemeral=True)

        view: TicketManageView = self.view
        if view.claimed_by is None:
            success = await view.update_permissions(interaction.channel, interaction, True)
            if success:
                view.claimed_by = interaction.user.id
                self.label = "Unclaim Ticket"
                self.style = discord.ButtonStyle.danger
                db.assign_ticket(self.ticket_number, interaction.user.id)
                embed = discord.Embed(
                    title="Ticket Claimed",
                    description=f"This ticket has been claimed by {interaction.user.mention}",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed)
            else:
                await interaction.response.send_message("Failed to claim ticket. Please try again.", ephemeral=True)
        else:
            if view.claimed_by == interaction.user.id or interaction.user.guild_permissions.administrator:
                success = await view.update_permissions(interaction.channel, interaction, False)
                if success:
                    view.claimed_by = None
                    self.label = "Claim Ticket"
                    self.style = discord.ButtonStyle.primary
                    db.assign_ticket(self.ticket_number, 0)
                    embed = discord.Embed(
                        title="Ticket Unclaimed",
                        description=f"This ticket has been unclaimed by {interaction.user.mention}",
                        color=discord.Color.orange()
                    )
                    await interaction.response.send_message(embed=embed)
                else:
                    await interaction.response.send_message("Failed to unclaim ticket. Please try again.", ephemeral=True)
            else:
                claimer = interaction.guild.get_member(view.claimed_by)
                await interaction.response.send_message(
                    f"This ticket is claimed by {claimer.mention}. Only they or an administrator can unclaim it.",
                    ephemeral=True
                )
        await interaction.message.edit(view=view)

# Close Ticket button – only the ticket creator may provide feedback and close the ticket
class CloseTicketButton(discord.ui.Button):
    def __init__(self, ticket_number: int):
        super().__init__(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
        self.ticket_number = ticket_number

    async def callback(self, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or discord.utils.get(interaction.user.roles, name="Staff")):
            return await interaction.response.send_message("You don't have permission to close tickets.", ephemeral=True)

        ticket_id = int(interaction.channel.name.split('-')[1])
        creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
        if interaction.user.id != creator_id:
            return await interaction.response.send_message("Only the ticket creator can provide feedback and close the ticket.", ephemeral=True)

        # Lock the channel so no one can send messages
        overwrites = interaction.channel.overwrites
        for role in interaction.guild.roles:
            overwrites[role] = discord.PermissionOverwrite(send_messages=False)
        await interaction.channel.edit(overwrites=overwrites)

        await interaction.response.send_modal(FeedbackModal(ticket_id))

# ───────────── Other Modals for Ticket Creation ─────────────

class StaffApplicationModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Staff Application")
        self.name = ui.TextInput(label="Name", placeholder="Your name", required=True)
        self.age = ui.TextInput(label="Age", placeholder="Your age (numbers only)", required=True, max_length=2, min_length=1)
        self.ign = ui.TextInput(label="In-game Name", placeholder="Your Minecraft username", required=True, max_length=16)
        self.country = ui.TextInput(label="Country", placeholder="Your country", required=True)
        self.experience = ui.TextInput(label="Experience & Languages", placeholder="Tell us about your experience and languages you speak", required=True, style=discord.TextStyle.paragraph)
        self.add_item(self.name)
        self.add_item(self.age)
        self.add_item(self.ign)
        self.add_item(self.country)
        self.add_item(self.experience)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.age.value.isdigit():
            await interaction.response.send_message("Age must contain only numbers!", ephemeral=True)
            return
        additional_info = (
            f"Name: {self.name.value}\n"
            f"Age: {self.age.value}\n"
            f"IGN: {self.ign.value}\n"
            f"Country: {self.country.value}\n"
            f"Experience & Languages: {self.experience.value}"
        )
        await _create_ticket(interaction, "staff", additional_info)

class BanAppealModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Ban Appeal")
        self.ign = ui.TextInput(label="In-game Name", placeholder="Your Minecraft username", required=True, max_length=16)
        self.ban_reason = ui.TextInput(label="Reason for Ban", placeholder="What were you banned for?", required=True, style=discord.TextStyle.paragraph)
        self.appeal_description = ui.TextInput(label="Appeal Description", placeholder="Why should your ban be lifted?", required=True, style=discord.TextStyle.paragraph)
        self.add_item(self.ign)
        self.add_item(self.ban_reason)
        self.add_item(self.appeal_description)

    async def on_submit(self, interaction: discord.Interaction):
        additional_info = (
            f"IGN: {self.ign.value}\n"
            f"Ban Reason: {self.ban_reason.value}\n"
            f"Appeal Description: {self.appeal_description.value}"
        )
        await _create_ticket(interaction, "appeal", additional_info)

class BugReportModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Bug Report")
        self.bug_found = ui.TextInput(label="Bug Found", placeholder="What bug did you find?", required=True)
        self.description = ui.TextInput(label="Description", placeholder="Provide detailed information about the bug", required=True, style=discord.TextStyle.paragraph)
        self.add_item(self.bug_found)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        additional_info = (
            f"Bug Found: {self.bug_found.value}\n"
            f"Description: {self.description.value}"
        )
        await _create_ticket(interaction, "bug", additional_info)

class ReportPlayerModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Report Player")
        self.player_name = ui.TextInput(label="Player Name", placeholder="Enter the player's username", required=True)
        self.reason = ui.TextInput(label="Reason", placeholder="Explain why you're reporting this player", required=True, style=discord.TextStyle.paragraph)
        self.add_item(self.player_name)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        additional_info = f"Reported Player: {self.player_name.value}\nReason: {self.reason.value}"
        await _create_ticket(interaction, "report", additional_info)

class MinecraftIGNModal(discord.ui.Modal):
    def __init__(self, rank: str):
        super().__init__(title=f"Purchase {rank} Rank")
        self.rank = rank
        self.ign = ui.TextInput(label="Minecraft IGN", placeholder="Enter your Minecraft username", required=True, max_length=16)
        self.add_item(self.ign)

    async def on_submit(self, interaction: discord.Interaction):
        additional_info = f"Rank: {self.rank}\nIGN: {self.ign.value}"
        await _create_ticket(interaction, "rank", additional_info, add_buttons=True)

class TicketModal(discord.ui.Modal):
    def __init__(self, ticket_type: str):
        super().__init__(title=f"Create {ticket_type.title()} Ticket")
        self.ticket_type = ticket_type
        self.title_input = ui.TextInput(label="Title", placeholder="Brief description of your issue", max_length=100, required=True, style=discord.TextStyle.short)
        self.add_item(self.title_input)
        self.description_input = ui.TextInput(label="Description", placeholder="Provide detailed information about your request", style=discord.TextStyle.paragraph, max_length=1000, required=True)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        await _create_ticket(interaction, self.ticket_type, self.description_input.value)

# ───────────── Ticket Creation Logic ─────────────

async def _create_ticket(interaction: discord.Interaction, ticket_type: str, additional_info: str = None, add_buttons: bool = False):
    try:
        await interaction.response.defer(ephemeral=True)
        category_data = TICKET_CATEGORIES.get(ticket_type, TICKET_CATEGORIES["support"])
        category_name = category_data["name"]
        category = discord.utils.get(interaction.guild.categories, name=category_name)

        if not category:
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
            }
            admin_role = discord.utils.get(interaction.guild.roles, permissions=discord.Permissions(administrator=True))
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            category = await interaction.guild.create_category(name=category_name, overwrites=overwrites)

        ticket_number = db.get_next_ticket_number()
        channel_name = f"ticket-{ticket_number:04d}"
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        channel = await category.create_text_channel(name=channel_name, overwrites=overwrites, topic=f"Ticket for {interaction.user.name} ({interaction.user.id})")

        db.create_ticket(
            channel.id,
            interaction.user.id,
            ticket_type,
            f"Ticket #{ticket_number:04d}",
            additional_info if additional_info else "No additional information provided.",
            category_name=category_name
        )

        embed = discord.Embed(
            title=f"{category_data['emoji']} Ticket #{ticket_number:04d}",
            description="A staff member will be with you shortly.",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Created by", value=interaction.user.mention, inline=True)
        embed.add_field(name="Type", value=category_data["name"], inline=True)
        embed.add_field(name="Category", value=category_name, inline=True)
        embed.add_field(name="Additional Information", value=additional_info if additional_info else "No additional information provided.", inline=False)

        view = TicketManageView(ticket_number)
        if add_buttons and ticket_type == "rank":
            view.add_payment_buttons()

        await channel.send(embed=embed, view=view)
        await interaction.followup.send(f"Ticket created! Check {channel.mention}", ephemeral=True)

    except Exception as e:
        logging.error(f"Error in ticket creation: {e}")
        await interaction.followup.send("An error occurred. Please try again.", ephemeral=True)
        if 'channel' in locals():
            try:
                await channel.delete()
            except Exception as err:
                logging.error(err)

# ───────────── Cog Implementation and Admin Commands ─────────────

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @is_admin()
    @app_commands.command(name="ticket_setup", description="Set up the ticket system")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_setup(self, interaction: discord.Interaction):
        try:
            embed = discord.Embed(
                title="🎫 Support Tickets",
                description="Need help? Choose your preferred method below to create a ticket!",
                color=discord.Color.blue()
            )
            categories_desc = "\n".join([
                f"{data['emoji']} **{data['name']}** - {data['description']}"
                for data in TICKET_CATEGORIES.values()
            ])
            embed.add_field(
                name="Available Categories",
                value=categories_desc,
                inline=False
            )
            view = TicketView()  # Only the dropdown is shown
            await interaction.response.send_message(embed=embed, view=view)
        except Exception as e:
            logging.error(f"Error in ticket setup: {e}")
            await interaction.response.send_message("Failed to set up the ticket system. Please try again later.", ephemeral=True)

    @app_commands.command(name="closeticket", description="Close a ticket (admins/staff only)")
    async def closeticket(self, interaction: discord.Interaction):
        if not (interaction.user.guild_permissions.administrator or discord.utils.get(interaction.user.roles, name="Staff")):
            await interaction.response.send_message("You don't have permission to close tickets.", ephemeral=True)
            return
        if interaction.channel.category_id != TICKET_CATEGORY_ID:
            await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
            return

        await interaction.response.defer()
        ticket_id = int(interaction.channel.name.split('-')[1])
        creator_id = int(interaction.channel.topic.split('(')[-1].split(')')[0])
        creator = interaction.guild.get_member(creator_id)
        category_name = interaction.channel.category.name
        claimed_by = db.tickets[ticket_id].get("assigned_to", "Unclaimed")
        messages = []
        async for message in interaction.channel.history(limit=None, oldest_first=True):
            messages.append(f"{message.created_at} - {message.author}: {message.content}")
        transcript = "\n".join(messages)
        logs_channel = discord.utils.get(interaction.guild.text_channels, name="ticket-logs")
        if not logs_channel:
            logs_channel = await interaction.guild.create_text_channel(name="ticket-logs", topic="Ticket transcripts and logs")

        embed = discord.Embed(
            title=f"Ticket #{ticket_id} Transcript",
            description="Ticket has been closed and archived",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Category", value=category_name)
        embed.add_field(name="Created by", value=creator.name)
        embed.add_field(name="Claimed by", value=claimed_by)
        embed.add_field(name="Closed by", value=interaction.user.name)
        embed.add_field(name="Transcript", value=transcript)

        db.close_ticket(ticket_id)
        await logs_channel.send(embed=embed, file=discord.File(fp=io.StringIO(transcript), filename=f"ticket-{ticket_id}-transcript.txt"))
        await interaction.followup.send("Ticket will be closed in 15 seconds...")
        await asyncio.sleep(15)
        await interaction.channel.delete()

    @app_commands.command(name="ticket", description="Create a support ticket")
    async def ticket(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=TicketView(), ephemeral=True)

    # Admin-only commands below

    @app_commands.command(name="pannelmsg", description="Edit the panel message from /ticket_setup command")
    @app_commands.checks.has_permissions(administrator=True)
    async def pannelmsg(self, interaction: discord.Interaction, new_message: str):
        async for msg in interaction.channel.history(limit=50):
            if msg.author == interaction.guild.me and msg.embeds:
                embed = msg.embeds[0]
                embed.description = new_message
                await msg.edit(embed=embed)
                await interaction.response.send_message("Panel message updated.", ephemeral=True)
                return
        await interaction.response.send_message("Panel message not found.", ephemeral=True)

    @app_commands.command(name="setprices", description="Set price for a rank and method")
    @app_commands.checks.has_permissions(administrator=True)
    async def setprices(self, interaction: discord.Interaction, rank: str, method: str, price: float):
        db.set_price(rank, method, price)
        await interaction.response.send_message(f"Price for {rank} via {method} set to {price}.", ephemeral=True)

    @app_commands.command(name="addrank", description="Add a rank")
    @app_commands.checks.has_permissions(administrator=True)
    async def addrank(self, interaction: discord.Interaction, rank: str):
        db.add_rank(rank)
        await interaction.response.send_message(f"Rank {rank} added.", ephemeral=True)

    @app_commands.command(name="removerank", description="Remove a rank")
    @app_commands.checks.has_permissions(administrator=True)
    async def removerank(self, interaction: discord.Interaction, rank: str):
        db.remove_rank(rank)
        await interaction.response.send_message(f"Rank {rank} removed.", ephemeral=True)

    @app_commands.command(name="addmethod", description="Add a payment method")
    @app_commands.checks.has_permissions(administrator=True)
    async def addmethod(self, interaction: discord.Interaction, method_name: str):
        db.add_payment_method(method_name)
        await interaction.response.send_message(f"Payment method {method_name} added.", ephemeral=True)

    @app_commands.command(name="setpaymet", description="Set payment details for a method")
    @app_commands.checks.has_permissions(administrator=True)
    async def setpaymet(self, interaction: discord.Interaction, method: str, id_value: str = None, qr: str = None):
        id_value = id_value if id_value else "not set yet"
        qr = qr if qr else "not set yet"
        db.set_payment(method, id_value, qr)
        await interaction.response.send_message(f"Payment details for {method} set. ID: {id_value}, QR: {qr}.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Tickets(bot))
    print("Tickets cog loaded")
