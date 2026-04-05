# Discord HR Bot 🤖

A fully-featured, self-hosted Discord HR bot with time tracking, leave requests, overtime, recurring standups, and CSV exports.

---

## ✅ Features

| Feature | Command |
|---------|---------|
| Clock In / Out | `/clockin`, `/clockout`, button panel |
| View Status | `/status` |
| Auto Clock-Out | Background job (configurable hours) |
| Overtime Tracking | Automatic per-session + reports |
| CSV Export (daily/weekly/monthly) | `/report timesheet`, `/report summary` |
| Leave Requests | `/leave request` → approval buttons |
| Leave Management | `/leave list`, `/leave pending` |
| Recurring Standups | `/standup add` (multiple times/day) |
| Per-employee Reports | `/report mine` |
| Break Tracking | `/break start`, `/break end`, `/break status` |
| Company Holidays | `/holiday add`, `/holiday list`, `/holiday upcoming` |
| EOD Clock-Out Reminders | `/reminder set`, `/reminder test` |

---

## 🚀 Setup (5 minutes)

### 1. Create Discord Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application** → name it → go to **Bot** tab
3. Click **Reset Token** → copy it
4. Under **Privileged Gateway Intents**, enable:
   - `SERVER MEMBERS INTENT`
   - `MESSAGE CONTENT INTENT`
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`, `Use Slash Commands`, `Manage Messages`
6. Copy the generated URL and invite the bot to your server

### 2. Run the Bot

**Option A: Python directly**
```bash
git clone <your-repo>
cd discord-hr-bot
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your DISCORD_TOKEN
python bot.py
```

**Option B: Docker (recommended for hosting)**
```bash
cp .env.example .env
# Edit .env and add your DISCORD_TOKEN
docker-compose up -d
```

---

## ⚙️ First-Time Configuration

Run these in Discord once the bot is online:

```
/hrconfig setup leave_channel:#leave-requests admin_role:@Managers timezone:Asia/Kathmandu
/overtime config daily_hours:8 weekly_hours:40 auto_out_hours:12
```

Post a clock-in panel in your work channel:
```
/clockpanel
```

---

## 📅 Standup Examples

```
# Single daily standup at 9 AM UTC
/standup add name:"Morning Standup" channel:#general times:"09:00" message:"Good morning team! Time for standup 🌅"

# Two standups per day
/standup add name:"Daily Sync" channel:#standups times:"09:00,14:00" message:"📢 Standup time! Share your updates."

# With role ping
/standup add name:"Dev Standup" channel:#dev times:"10:00" message:"🧑‍💻 Dev standup!" ping_role:@Developers
```

---

## 📊 Reports

| Command | Description |
|---------|-------------|
| `/report timesheet week` | All time entries this week as CSV |
| `/report timesheet month member:@John` | One person's month as CSV |
| `/report summary last_month` | Per-employee summary + overtime CSV |
| `/report overtime week` | Overtime in Discord embed |
| `/report leave` | All leave requests as CSV |
| `/report mine week` | Your own hours summary |

**Available periods:** `today`, `yesterday`, `week`, `last_week`, `month`, `last_month`

---

## 📂 File Structure

```
discord-hr-bot/
├── bot.py              # Main entry point
├── database.py         # SQLite database layer
├── csv_exporter.py     # CSV export logic
├── scheduler.py        # Standup scheduler
├── cogs/
│   ├── timeclock.py    # /clockin /clockout /status
│   ├── leave.py        # /leave commands
│   ├── standup.py      # /standup commands
│   ├── admin.py        # /hrconfig /overtime
│   ├── reports.py      # /report commands
│   ├── breaks.py       # /break commands
│   ├── holidays.py     # /holiday commands
│   └── reminders.py    # /reminder commands (EOD alerts)
├── data/               # SQLite database (auto-created)
├── exports/            # CSV exports (auto-created)
├── logs/               # Bot logs (auto-created)
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## 🔧 Auto Clock-Out

If someone forgets to clock out, the bot automatically clocks them out after the configured limit (default: 12 hours) and sends them a DM notification.

Configure with:
```
/overtime config auto_out_hours:10
```

Set to `0` to disable.

---

## 💡 Tips

- All times are stored and displayed in **UTC**. Use `timezone:` in `/hrconfig setup` to note your local timezone for reference.
- CSV exports are saved to the `exports/` folder on the host machine AND attached to the Discord reply.
- Standups fire within a 1-minute window of the configured time. The bot checks every minute.
- Leave approval buttons persist across bot restarts.

---

## 📦 Free Hosting Options

| Platform | Notes |
|----------|-------|
| **Railway** | $5/mo hobby plan, easy deploy |
| **Render** | Free tier (may sleep), paid for always-on |
| **Oracle Cloud Always Free** | VM with 1GB RAM, truly free |
| **Your own VPS** | DigitalOcean/Hetzner ~$5/mo |

For Railway/Render: add `DISCORD_TOKEN` as an environment variable in their dashboard.
