# Axonic Ticket Bot

Python `discord.py` ticket bot for Axonic.

## Setup

1. Install dependencies:
   `pip install -r requirements.txt`
2. Add a Railway environment variable named `DISCORD_TOKEN` containing your Discord bot token.
3. Run:
   `python bot.py`
5. In your Axonic server, run:
   `/ticket panel`

The bot already contains the Axonic server/category/staff/transcript IDs supplied for this build.

## Main ticket UI

The panel uses a Discord select menu styled around the supplied Axonic layout:
- Purchase
- Support
- Bug Report

Each selection opens a short modal before creating the ticket.

## Important

Keep your `DISCORD_TOKEN` secret. It is read from the Railway environment and is not stored in GitHub.
Enable the required privileged intents for the bot in the Discord Developer Portal:
- Server Members Intent
- Message Content Intent
