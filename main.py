import telebot
import sqlite3
import requests
import re
import os
import sys
import shutil
import gzip
import hashlib
from datetime import datetime, timedelta
from threading import Thread
import time
from html import escape
from PIL import Image

# ===== CONFIGURATION =====
BOT_TOKEN = os.getenv("BOT_TOKEN") or "token"
YOUR_USER_ID = 7863700139
SUPPORTED_REGIONS = {"ind", "sg", "eu", "me", "id", "bd", "ru", "vn", "tw", "th", "pk"} #"br", "sac", "us", "cis", "na"

# ===== USER DAILY LIMITS =====
USER_DAILY_LIMITS = {
    'like': 1,
    'spam': 15,
    'visit': 20
}

# ===== BACKUP CONFIGURATION =====
BACKUP_DIR = "database_backups"
MAX_BACKUPS = 30
BACKUP_SCHEDULE_HOUR = 4
COMPRESS_BACKUPS = True
VERIFY_CHECKSUM = True
REMOTE_BACKUP_URL = None

# ===== DATABASE HELPER FUNCTIONS =====
def get_db_value(key, default=None):
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
        result = cursor.fetchone()
        return result[0] if result else default

def set_db_value(key, value):
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def get_user_usage(user_id, command_type):
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT used FROM user_daily_limits WHERE user_id=? AND command_type=? AND date=?",
            (user_id, command_type, today)
        )
        result = cursor.fetchone()
        return result[0] if result else 0

def increment_user_usage(user_id, command_type):
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_daily_limits (user_id, command_type, used, date) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(user_id, command_type, date) DO UPDATE SET used = used + 1",
            (user_id, command_type, today)
        )
        conn.commit()

def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()

    # Whitelisted groups
    cursor.execute('''CREATE TABLE IF NOT EXISTS allowed_groups (
        group_id TEXT,
        feature_type TEXT,
        requests INTEGER,
        remaining_requests INTEGER,
        days INTEGER,
        added_at TEXT,
        PRIMARY KEY (group_id, feature_type)
    )''')

    # Daily user limits
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_daily_limits (
        user_id INTEGER,
        command_type TEXT,
        used INTEGER DEFAULT 0,
        date TEXT,
        last_reset TEXT,
        reset_by INTEGER,
        PRIMARY KEY (user_id, command_type, date)
    )''')

    # Cooldowns
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_cooldowns (
        user_id INTEGER,
        command_type TEXT,
        last_used TEXT,
        date TEXT,
        PRIMARY KEY (user_id, command_type, date)
    )''')

    # ✅ Create Bot settings table before inserting
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    # Insert default settings
    cursor.execute("""
    INSERT OR IGNORE INTO settings (key, value) VALUES (
        'custom_footer',
        '🔗 <b>JOIN US</b>\n<b>Our Group¹:</b> https://t.me/VampireCheatz\n<b>Our Group²:</b> https://t.me/FFinfoChat\n<b>Our Channel:</b> https://t.me/VampirePB'
    )
""")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance_mode', '0')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('last_global_reset', '')")

    conn.commit()
    conn.close()

init_db()

# RESET
def reset_user_limits(user_id, command_type=None, reset_by=None):
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        if command_type:
            cursor.execute(
                """UPDATE user_daily_limits
                   SET used = 0, last_reset = ?, reset_by = ?
                   WHERE user_id = ? AND command_type = ? AND date = ?""",
                (now, reset_by, user_id, command_type, today)
            )
        else:
            cursor.execute(
                """UPDATE user_daily_limits
                   SET used = 0, last_reset = ?, reset_by = ?
                   WHERE user_id = ? AND date = ?""",
                (now, reset_by, user_id, today)
            )
        conn.commit()
        return cursor.rowcount

def reset_all_limits(command_type=None, reset_by=None):
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        if command_type:
            cursor.execute(
                """UPDATE user_daily_limits
                   SET used = 0, last_reset = ?, reset_by = ?
                   WHERE command_type = ? AND date = ?""",
                (now, reset_by, command_type, today)
            )
        else:
            cursor.execute(
                """UPDATE user_daily_limits
                   SET used = 0, last_reset = ?, reset_by = ?
                   WHERE date = ?""",
                (now, reset_by, today)
            )
        conn.commit()
        return cursor.rowcount

def reset_daily_limits():
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_daily_limits")
        conn.commit()


# ===== BOT INITIALIZATION =====
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

def send_html(msg, text):
    """Send message with HTML formatting"""
    try:
        bot.reply_to(msg, text)
    except Exception as e:
        print(f"Message sending error: {e}")


# ===== CORE FUNCTIONS =====
def is_maintenance():
    return get_db_value("maintenance_mode") == "1"

def check_admin(user_id):
    return user_id == YOUR_USER_ID

# ===== COMMAND HANDLERS =====
@bot.message_handler(commands=['start', 'help'], func=lambda m: m.chat.type != 'private')
def handle_help(message):
    help_text = """
<b>🎮 Free Fire Bot</b>
<code>━━━━━━━━━━━━━━━━</code>
<b>User Commands:</b>
/like <i>region uid</i> – Send likes  
/spam <i>region uid</i> – Send friend requests  
/visit <i>region uid</i> – Send visitors  
/remain – Check your remaining usage  
/id – Get group ID  
<code>Get &lt;uid&gt;</code> – View player profile with stats, banner & outfit  
<code>isbanned &lt;uid&gt;</code> – Check if a player is banned  
<code>search &lt;nickname&gt;</code> – Search players by nickname  
<code>region &lt;uid&gt;</code> – Find a player's region
"""

    if check_admin(message.from_user.id):
        help_text += """
<code>━━━━━━━━━━━━━━━━</code>
<b>🔐 Admin Commands:</b>
/addgroup <i>id requests days like|spam</i> – Allow group access  
/removegroup <i>id [like|spam]</i> – Remove group access  
/listgroups – Show allowed groups  
/stats – Bot analytics  
/info – Group usage stats  
/maintenance <i>on|off</i> – Enable/disable maintenance  
/backup – Create database backup  
/resetcooldown <i>like|spam|visit</i> – Reset cooldown for a user  
/reset <i>[all|user_id] [like|spam|visit]</i> – Reset command limits  
/restart – Restart the bot  
/setfooter &lt;text&gt; – Change the JOIN US footer
/groups – Show all cached group info with leave button
/leave <i>group_id</i> – Leave any group by ID
"""

    send_html(message, help_text)


@bot.message_handler(commands=['remain'], func=lambda m: m.chat.type != 'private')
def handle_remain(message):
    user_id = message.from_user.id
    
    response = ["<b>📊 Your Daily Usage</b>"]
    response.append("<code>━━━━━━━━━━━━━━━━</code>")
    
    for cmd_type, limit in USER_DAILY_LIMITS.items():
        used = get_user_usage(user_id, cmd_type)
        remaining = max(limit - used, 0)
        response.append(
            f"🔹 /{cmd_type}: <code>{remaining}/{limit}</code> remaining"
        )
    
    response.append("<code>━━━━━━━━━━━━━━━━</code>")
    response.append("⏳ <i>Resets daily at 4:00 AM IST</i>")
    
    send_html(message, "\n".join(response))

@bot.message_handler(commands=['like'], func=lambda m: m.chat.type != 'private')
def like_command(message):
    if is_maintenance():
        return send_html(
            message,
            "<b>🔧 Maintenance Mode Active</b>\nThe bot is currently under maintenance. Please try again later."
        )
    
    user_id = message.from_user.id

    # Check user's daily limit
    used = get_user_usage(user_id, 'like')
    if used >= USER_DAILY_LIMITS['like']:
        reset_time = datetime.now().replace(hour=4, minute=0, second=0)
        if datetime.now().hour >= 4:
            reset_time += timedelta(days=1)
        return send_html(
            message,
            f"<b>❌ Daily Limit Reached!</b>\n"
            f"You've used all {USER_DAILY_LIMITS['like']} likes today.\n"
            f"Resets at: <code>{reset_time.strftime('%Y-%m-%d %H:%M')}</code>\n\n"
            f"Use <code>/remain</code> to check your usage."
        )
        
    # Validate input format
    parts = message.text.split()
    if len(parts) != 3:
        return send_html(message, 
            "<b>❌ Incorrect Format!</b>\n\n"
            "Usage: <code>/like [REGION] [UID]</code>\n"
            "<b>Example:</b> <code>/like ind 11111111</code>"
        )

    region, uid = parts[1], parts[2].upper()
    if region not in SUPPORTED_REGIONS:
        return send_html(message, 
            f"<b>❌ Invalid Region!</b>\n\n"
            f"🌎 <b>Supported:</b> <code>{', '.join(SUPPORTED_REGIONS)}</code>\n"
            f"<b>Entered:</b> <code>{escape(region)}</code>"
        )

    # Show "Searching" message
    searching_msg = bot.reply_to(message, f"🔍 Sending like to player: <code>{escape(uid)}</code>...")

    # Send API request
    api_url = f"https://liikes-api.vercel.app/like?uid={uid}&server_name={region}"
    try:
        response = requests.get(api_url, timeout=30)
        data = response.json()
    except requests.Timeout:
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text="<b>❌ Request Timeout!</b>\nThe server took too long to respond.",
            parse_mode='HTML'
        )
    except Exception:
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text="<b>⚠️ API Error!</b>\n<i>The request failed or took too long to respond.</i>",
            parse_mode='HTML'
        )

    # Handle API response
    likes_given = data.get("LikesGivenByAPI", 0)

    if data.get("status") == 1:  # Success
        if likes_given >= 50:
            increment_user_usage(user_id, 'like')
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text=(
                f"<b>✅ Like Successful!</b>\n"
                f"👤 Player: <code>{escape(data.get('PlayerNickname', 'N/A'))}</code>\n"
                f"🆔 UID: <code>{escape(str(data.get('UID', uid)))}</code>\n"
                f"🌎 Region: <code>{escape(region)}</code>\n"
                f"❤️ Likes Before: <code>{data.get('LikesbeforeCommand', 'N/A')}</code>\n"
                f"👍 Likes Added: <code>{likes_given}</code>\n"
                f"❤️ Total Likes Now: <code>{data.get('LikesafterCommand', 'N/A')}</code>\n\n"
                f"📊 <b>Your remaining likes today:</b> <code>{USER_DAILY_LIMITS['like'] - used - 1}/{USER_DAILY_LIMITS['like']}</code>"
            ),
            parse_mode='HTML'
        )

    elif data.get("status") == 2:  # Daily limit reached
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text=(
                "❤️ <b>Daily Limit Reached!</b>\n\n"
                f"👤 Player: <code>{escape(data.get('PlayerNickname', 'N/A'))}</code>\n"
                f"🆔 UID: <code>{escape(str(data.get('UID', uid)))}</code>\n\n"
                "This Free Fire ID has received all available likes for today.\n\n"
                "✨ <b>What To Do Now?</b>\n"
                "- Try again later today\n"
                "- Use a different Free Fire ID\n"
                f"- Join {escape('@vampirePB')} for updates\n\n"
                "<i>Note: Each ID can get maximum 100 likes per day</i>"
            ),
            parse_mode='HTML'
        )

    else:  # Other errors
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text=(
                "<b>❌ Like Failed!</b>\n"
                f"Error: <code>{escape(data.get('message', 'Unknown error'))}</code>"
            ),
            parse_mode='HTML'
        )
        
        
@bot.message_handler(commands=['spam'], func=lambda m: m.chat.type != 'private')
def spam_command(message):
    if is_maintenance():
        return send_html(message, "<b>🔧 Maintenance Mode Active</b>\nThe bot is currently under maintenance. Please try again later.")
    
    user_id = message.from_user.id
    
    # Check user's daily limit
    used = get_user_usage(user_id, 'spam')
    if used >= USER_DAILY_LIMITS['spam']:
        reset_time = datetime.now().replace(hour=4, minute=0, second=0)
        if datetime.now().hour >= 4:
            reset_time += timedelta(days=1)
        return send_html(message,
            f"<b>❌ Daily Limit Reached!</b>\n"
            f"You've used all {USER_DAILY_LIMITS['spam']} spams today.\n"
            f"Resets at: <code>{reset_time.strftime('%Y-%m-%d %H:%M')}</code>\n\n"
            f"Use <code>/remain</code> to check your usage."
        )
    
    parts = message.text.split()
    if len(parts) != 3:
        return send_html(message,
            "<b>❌ Incorrect Format!</b>\n\n"
            "Usage: <code>/spam [REGION] [UID]</code>\n"
            "<b>Example:</b> <code>/spam ind 12345678</code>"
        )

    region, uid = parts[1].lower(), parts[2]

    if region not in SUPPORTED_REGIONS:
        return send_html(message,
            f"<b>❌ Invalid Region!</b>\n"
            f"🌎 Supported: <code>{', '.join(SUPPORTED_REGIONS)}</code>\n"
            f"Entered: <code>{escape(region)}</code>"
        )

    # Show "Searching" message
    searching_msg = bot.reply_to(message, f"🔍 Sending friend request to player: <code>{escape(uid)}</code>...")

    try:
        url = f"https://spaam-api.vercel.app/spam?uid={uid}&server_name={region}"
        res = requests.get(url, timeout=30)
        data = res.json()

        successful = data.get("friend_requests", {}).get("successful", 0)
        failed = data.get("friend_requests", {}).get("failed", 0)

        if data.get("status") == "fail" or successful == 0:
            return bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=searching_msg.message_id,
                text=(
                    f"<b>⛔ Friend List Full!</b>\n\n"
                    f"🆔 UID: <code>{escape(uid)}</code>\n"
                    f"🌍 Region: <code>{escape(region)}</code>\n"
                    f"✅ Successful: <code>{successful}</code>\n"
                    f"❌ Failed: <code>{failed}</code>\n\n"
                    f"<i>Player's friend list may be full. Try another UID.</i>"
                ),
                parse_mode='HTML'
            )

        increment_user_usage(user_id, 'spam')
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text=(
                f"<b>📨 Spam Sent Successfully!</b>\n\n"
                f"🆔 UID: <code>{escape(uid)}</code>\n"
                f"🌍 Region: <code>{escape(region)}</code>\n"
                f"✅ Successful: <code>{successful}</code>\n"
                f"❌ Failed: <code>{failed}</code>\n\n"
                f"📊 <b>Your remaining spams today:</b> <code>{USER_DAILY_LIMITS['spam'] - used - 1}/{USER_DAILY_LIMITS['spam']}</code>"
            ),
            parse_mode='HTML'
        )

    except Exception:
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text="<b>❌ API Error:</b> <i>The request failed or took too long to respond.</i>",
            parse_mode='HTML'
        )
        
@bot.message_handler(commands=['visit'], func=lambda m: m.chat.type != 'private')
def visit_command(message):
    if is_maintenance():
        return send_html(message, "<b>🔧 Maintenance Mode Active</b>\nThe bot is currently under maintenance. Please try again later.")
    
    user_id = message.from_user.id
    
    # Check user's daily limit
    used = get_user_usage(user_id, 'visit')
    if used >= USER_DAILY_LIMITS['visit']:
        reset_time = datetime.now().replace(hour=4, minute=0, second=0)
        if datetime.now().hour >= 4:
            reset_time += timedelta(days=1)
        return send_html(message,
            f"<b>❌ Daily Limit Reached!</b>\n"
            f"You've used all {USER_DAILY_LIMITS['visit']} visits today.\n"
            f"Resets at: <code>{reset_time.strftime('%Y-%m-%d %H:%M')}</code>\n\n"
            f"Use <code>/remain</code> to check your usage."
        )
    
    # Validate input format
    parts = message.text.split()
    if len(parts) != 3:
        return send_html(message, 
            "<b>❌ Incorrect Format!</b>\n\n"
            "Usage: <code>/visit [REGION] [UID]</code>\n"
            "<b>Example:</b> <code>/visit ind 1441772892</code>"
        )

    region, uid = parts[1], parts[2].upper()
    if region not in SUPPORTED_REGIONS:
        return send_html(message, 
            f"<b>❌ Invalid Region!</b>\n\n"
            f"🌎 <b>Supported:</b> <code>{', '.join(SUPPORTED_REGIONS)}</code>\n"
            f"<b>Entered:</b> <code>{escape(region)}</code>"
        )

    # Show "Searching" message
    searching_msg = bot.reply_to(message, f"🔍 Visiting player: <code>{escape(uid)}</code>...")

    # Send API request
    api_url = f"https://viisits-api.vercel.app/visit?uid={uid}&server_name={region}"
    try:
        response = requests.get(api_url, timeout=30)
        data = response.json()
    except requests.Timeout:
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text="<b>❌ Request Timeout!</b>\nThe server took too long to respond.",
            parse_mode='HTML'
        )
    except Exception:
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text="<b>⚠️ API Error!</b>\n<i>The request failed or took too long to respond.</i>",
            parse_mode='HTML'
        )

    # Handle API response
    if data.get("status") == "success":
        success_visits = data.get("success_visits", 0)
        increment_user_usage(user_id, 'visit')
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text=(
                f"<b>✅ Visit Successful!</b>\n"                
                f"👤 Player: <code>{escape(data.get('PlayerNickname', 'N/A'))}</code>\n"
                f"🆔 UID: <code>{escape(str(data.get('UID', uid)))}</code>\n"
                f"🌎 Region: <code>{escape(region)}</code>\n"
                f"✅ Successful Visits: <code>{success_visits}</code>\n"
                f"❌ Failed Visits: <code>{data.get('failure_visits', 0)}</code>\n\n"
                f"📊 <b>Your remaining visits today:</b> <code>{USER_DAILY_LIMITS['visit'] - used - 1}/{USER_DAILY_LIMITS['visit']}</code>\n\n"
                f"<b>Note:</b> Restart the game to see the updated visitors."
            ),
            parse_mode='HTML'
        )
    else:
        return bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text=(
                "<b>❌ Visit Failed!</b>\n"
                "Something went wrong while processing your request.\n"
                "<i>Please try again after a while.</i>"
            ),
            parse_mode='HTML'
        )

@bot.message_handler(commands=['restart'])
def restart_bot(message):
    if message.from_user.id != YOUR_USER_ID:
        return send_html(message, "<b>❌ Unauthorized!</b>\nYou do not have permission to restart the bot.")

    send_html(message, "<b>🔄 The bot has restarted successfully!</b>")
    os.system("kill 1")

@bot.message_handler(commands=['id'])
def handle_id(message):
    send_html(message, f"<b>🆔 Group ID:</b> <code>{escape(str(message.chat.id))}</code>")

@bot.message_handler(commands=['info'])
def handle_info(message):
    if not check_admin(message.from_user.id):
        return send_html(message, "<b>🚫 Only bot admins can use this command.</b>")

    chat = message.chat
    group_id = str(chat.id)
    group_title = chat.title or "Unnamed Group"

    if chat.username:
        group_link = f"https://t.me/{chat.username}"
        group_display = f'<a href="{group_link}">{escape(group_title)}</a>'
    else:
        group_display = f"<code>{escape(group_title)}</code>"

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT feature_type, requests, remaining_requests, days, added_at
        FROM allowed_groups
        WHERE group_id = ?
    """, (group_id,))
    entries = cursor.fetchall()
    conn.close()

    if not entries:
        return send_html(message,
            "<b>🔒 Access restricted!</b>\n\n"
            "This group hasn't been approved to use the bot.\n\n"
            "<b>📌 To get whitelisted:</b>\n"
            f"1. Contact @vampire_exee\n"
            f"2. Provide your Group ID: <code>{escape(group_id)}</code>\n\n"
            "<i>💡 Premium groups get priority access!</i>"
        )

    response = [
        f"<b>📋 Group Info</b>\n"
        f"<code>━━━━━━━━━━━━━━━━</code>\n"
        f"🏷️ <b>Title:</b> {group_display}\n"
        f"🆔 <b>ID:</b> <code>{group_id}</code>"
    ]

    for feature_type, requests, remaining, days, added_at in entries:
        added_date = datetime.fromisoformat(added_at)
        expiry_date = added_date + timedelta(days=days)
        days_left = (expiry_date - datetime.now()).days
        used = requests - remaining
        percent = (used / requests) * 100 if requests > 0 else 0

        response.append(
            f"<code>━━━━━━━━━━━━━━━━</code>\n"
            f"⚙️ <b>Feature:</b> <code>{feature_type}</code>\n"
            f"📅 <b>Added On:</b> <code>{added_date.strftime('%Y-%m-%d')}</code>\n"
            f"⏳ <b>Expires In:</b> <code>{max(days_left, 0)} days</code>\n"
            f"📊 <b>Daily Limit:</b> <code>{requests}</code>\n"
            f"❤️ <b>Used Today:</b> <code>{used}/{requests}</code> ({percent:.1f}%)"
        )

    send_html(message, "\n".join(response))

@bot.message_handler(commands=['addgroup'])
def handle_addgroup(message):
    if not check_admin(message.from_user.id):
        return

    conn = None
    try:
        parts = message.text.split()
        if len(parts) != 5:
            raise ValueError("Usage: /addgroup <group_id> <requests> <days> <like|spam>")

        _, group_id, requests_str, days_str, feature_type = parts

        if feature_type not in ("like", "spam", "visit"):
            raise ValueError("Feature type must be 'like', 'spam' or 'visit'.")

        if not group_id.startswith("-") or not group_id.lstrip("-").isdigit():
            raise ValueError("Group ID must be a negative number (e.g., -123456789)")

        requests = int(requests_str)
        days = int(days_str)
        group_id = str(group_id)

        if requests <= 0 or days <= 0:
            raise ValueError("Requests and days must be positive integers.")

        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM allowed_groups WHERE group_id = ? AND feature_type = ?",
            (group_id, feature_type)
        )
        if cursor.fetchone():
            return send_html(message,
                f"<b>⚠️ Group already has access to:</b> <code>{feature_type}</code>"
            )

        cursor.execute(
            "INSERT INTO allowed_groups VALUES (?, ?, ?, ?, ?, ?)",
            (group_id, feature_type, requests, requests, days, datetime.now().isoformat())
        )
        conn.commit()

        expires = datetime.now() + timedelta(days=days)
        send_html(message, (
            f"<b>✅ Group Whitelisted for {feature_type.upper()}</b>\n"
            f"<code>━━━━━━━━━━━━━━━━</code>\n"
            f"🆔 <b>Group ID:</b> <code>{group_id}</code>\n"
            f"📊 <b>Daily Limit:</b> <code>{requests}</code>\n"
            f"⏳ <b>Duration:</b> <code>{days} days</code>\n"
            f"📅 <b>Expires:</b> <code>{expires.strftime('%Y-%m-%d')}</code>"
        ))

    except Exception as e:
        send_html(message,
            f"<b>❌ Error:</b> <code>{escape(str(e))}</code>\n\n"
            "<b>Usage:</b> <code>/addgroup -123456789 50 30 like</code>"
        )
    finally:
        if conn:
            conn.close()

@bot.message_handler(commands=['removegroup'])
def handle_removegroup(message):
    if not check_admin(message.from_user.id):
        return

    try:
        parts = message.text.split()
        if len(parts) not in (2, 3):
            raise ValueError("Usage: /removegroup <group_id> [like|spam]")

        group_id = parts[1]
        feature_type = parts[2] if len(parts) == 3 else None

        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()

        if feature_type:
            cursor.execute("DELETE FROM allowed_groups WHERE group_id=? AND feature_type=?", (group_id, feature_type))
        else:
            cursor.execute("DELETE FROM allowed_groups WHERE group_id=?", (group_id,))

        affected = cursor.rowcount
        conn.commit()
        conn.close()

        if affected > 0:
            scope = f" <b>({feature_type})</b>" if feature_type else ""
            send_html(message, f"<b>✅ Group removed:</b> <code>{escape(group_id)}</code>{scope}")
        else:
            send_html(message, f"<b>ℹ️ Group not found or feature not set:</b> <code>{escape(group_id)}</code>")

    except Exception as e:
        send_html(message,
            f"<b>❌ Error:</b> <code>{escape(str(e))}</code>\n\n"
            "Usage:\n"
            " <code>/removegroup -123456789</code>\n"
            " <code>/removegroup -123456789 like</code>\n"
            " <code>/removegroup -123456789 spam</code>"
        )

@bot.message_handler(commands=['listgroups'])
def handle_listgroups(message):
    if not check_admin(message.from_user.id):
        return

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT group_id, feature_type, requests, days, added_at FROM allowed_groups")
    groups = cursor.fetchall()
    conn.close()

    if not groups:
        send_html(message, "<b>ℹ️ No whitelisted groups</b>")
        return

    response = ["<b>📋 Whitelisted Groups</b>"]
    for group in groups:
        group_id, feature_type, requests, days, added_at = group
        expiry_date = (datetime.fromisoformat(added_at) + timedelta(days=days)).strftime("%Y-%m-%d")
        response.append(
            f"<code>━━━━━━━━━━━━━━━━</code>\n"
            f"🆔 <b>ID:</b> <code>{escape(group_id)}</code>\n"
            f"⚙️ <b>Feature:</b> <code>{escape(feature_type)}</code>\n"
            f"📊 <b>Requests/Day:</b> <code>{escape(str(requests))}</code>\n"
            f"⏳ <b>Expires:</b> <code>{escape(expiry_date)}</code>"
        )

    send_html(message, "\n".join(response))

@bot.message_handler(commands=['stats'])
def handle_stats(message):
    if not check_admin(message.from_user.id):
        return

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT group_id, feature_type, requests, remaining_requests, days, added_at 
        FROM allowed_groups
        ORDER BY added_at DESC
    """)
    groups = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM user_daily_limits")
    active_users = cursor.fetchone()[0]
    
    maintenance = "🔴 ON" if is_maintenance() else "🟢 OFF"
    conn.close()

    if not groups:
        send_html(message, "<b>ℹ️ No whitelisted groups</b>")
        return

    response = ["<b>📈 Detailed Bot Statistics</b>"]
    total_used = 0
    total_max = 0

    for group in groups:
        group_id, feature_type, max_req, remaining, days, added_at = group
        used = max_req - remaining
        total_used += used
        total_max += max_req
        
        added_date = datetime.fromisoformat(added_at).strftime("%Y-%m-%d")
        expiry_date = (datetime.fromisoformat(added_at) + timedelta(days=days)).strftime("%Y-%m-%d")
        
        response.append(
            f"\n<code>━━━━━━━━━━━━━━━━</code>\n"
            f"🆔 <b>Group ID:</b> <code>{escape(group_id)}</code>\n"
            f"⚙️ <b>Feature:</b> <code>{escape(feature_type)}</code>\n"
            f"📅 <b>Added:</b> <code>{escape(added_date)}</code>\n"
            f"⏳ <b>Expires:</b> <code>{escape(expiry_date)}</code>\n"
            f"❤️ <b>Used:</b> <code>{escape(str(used))}/{escape(str(max_req))}</code>\n"
            f"📊 <b>Usage:</b> <code>{(used/max_req)*100:.1f}%</code>"
        )

    response.append(
        f"\n\n<b>📊 TOTAL ACROSS ALL GROUPS</b>\n"
        f"<code>━━━━━━━━━━━━━━━━</code>\n"
        f"👥 <b>Whitelisted Groups:</b> <code>{escape(str(len(groups)))}</code>\n"
        f"👤 <b>Active Users Today:</b> <code>{escape(str(active_users))}</code>\n"
        f"❤️ <b>Total Likes Used:</b> <code>{escape(str(total_used))}/{escape(str(total_max))}</code>\n"
        f"🛠️ <b>Maintenance Mode:</b> <code>{escape(maintenance)}</code>"
    )

    send_html(message, "\n".join(response))

@bot.message_handler(commands=['maintenance'])
def handle_maintenance(message):
    if not check_admin(message.from_user.id):
        return send_html(message, "<b>🚫 Access Denied:</b> Only bot admins can use this command.")

    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError("Invalid command format")
            
        mode = parts[1].lower()
        if mode not in ("on", "off"):
            raise ValueError("Invalid maintenance mode")
        
        # Set maintenance mode in database
        set_db_value("maintenance_mode", "1" if mode == "on" else "0")
        
        # Prepare response
        status = "🔴 ACTIVATED" if mode == "on" else "🟢 DEACTIVATED"
        notice = ("\n\n⚠️ <b>Notice:</b> All user commands will be blocked until maintenance is complete." 
                 if mode == "on" else "")
        
        send_html(message, 
            f"<b>✅ Maintenance Mode {status}</b>\n"
            f"<code>━━━━━━━━━━━━━━━━</code>\n"
            f"🛠️ <b>Status:</b> <code>{'ON' if mode == 'on' else 'OFF'}</code>\n"
            f"⏱️ <b>Changed at:</b> <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            f"{notice}"
        )
        
    except Exception as e:
        send_html(message,
            "<b>❌ Error setting maintenance mode:</b>\n"
            f"<code>{escape(str(e))}</code>\n\n"
            "<b>Usage:</b> <code>/maintenance on|off</code>\n"
            "<i>Example:</i> <code>/maintenance on</code>"
        )

@bot.message_handler(commands=['resetcooldown'])
def handle_resetcooldown(message):
    if not check_admin(message.from_user.id):
        return

    try:
        parts = message.text.strip().split()
        cmd_type = None
        target_user = None

        # Extract command type
        if len(parts) >= 2 and parts[-1].lower() in USER_DAILY_LIMITS:
            cmd_type = parts[-1].lower()
            parts = parts[:-1]
        else:
            raise ValueError("Specify a valid command type: like, spam, visit")

        # Get user ID from reply or argument
        if message.reply_to_message:
            target_user = message.reply_to_message.from_user.id
        elif len(parts) == 2 and parts[1].isdigit():
            target_user = int(parts[1])
        else:
            raise ValueError("Reply to user or provide a valid user ID")

        today = datetime.now().strftime("%Y-%m-%d")
        with sqlite3.connect("bot_data.db") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT used FROM user_daily_limits WHERE user_id=? AND command_type=? AND date=?",
                (target_user, cmd_type, today)
            )
            result = cursor.fetchone()
            if result and result[0] == USER_DAILY_LIMITS[cmd_type]:
                # Reset used to 0 (full refill)
                cursor.execute(
                    """UPDATE user_daily_limits 
                       SET used = 0, last_reset = ?, reset_by = ? 
                       WHERE user_id = ? AND command_type = ? AND date = ?""",
                    (datetime.now().isoformat(), message.from_user.id, target_user, cmd_type, today)
                )
                conn.commit()
                send_html(message, f"<b>✅ Cooldown reset:</b> <code>{cmd_type}</code> for user <code>{target_user}</code>")
            else:
                current_used = result[0] if result else 0
                total_limit = USER_DAILY_LIMITS[cmd_type]
                send_html(
                    message,
                    f"<b>ℹ️ {cmd_type.title()} not fully used:</b>\n"
                    f"Currently used: <code>{current_used}</code> / <code>{total_limit}</code>\n"
                    f"Reset allowed only if usage is <code>{total_limit}</code>."
                )

    except Exception as e:
        send_html(message,
            f"<b>❌ Error:</b> <code>{escape(str(e))}</code>\n\n"
            "<b>Usage:</b>\n"
            "Reply: <code>/resetcooldown visit</code>\n"
            "User ID: <code>/resetcooldown 123456789 like</code>"
        )
    
@bot.message_handler(commands=['reset'])
def handle_reset(message):
    if not check_admin(message.from_user.id):
        return

    try:
        parts = message.text.strip().split()
        cmd_type = None
        target = None

        # Extract command_type if present
        if len(parts) >= 2 and parts[-1].lower() in ("like", "spam", "visit"):
            cmd_type = parts[-1].lower()
            parts = parts[:-1]

        # Handle /reset all [...]
        if len(parts) >= 2 and parts[1].lower() == "all":
            count = reset_all_limits(cmd_type, reset_by=message.from_user.id)
            action = cmd_type or "all"
            return send_html(message, f"<b>✅ Reset {action} usage for all users.</b>\nAffected records: <code>{count}</code>")

        # Handle reply-based resets
        if message.reply_to_message:
            target = message.reply_to_message.from_user.id

        # Handle /reset <user_id> [...]
        elif len(parts) == 2 and parts[1].isdigit():
            target = int(parts[1])
        elif not message.reply_to_message:
            raise ValueError("Please reply to a user or provide a user ID.")

        if target is None:
            raise ValueError("Could not resolve target user.")

        count = reset_user_limits(target, cmd_type, reset_by=message.from_user.id)
        action = cmd_type or "all"
        if count > 0:
            send_html(message, f"<b>✅ Reset {action} usage for:</b> <code>{target}</code>")
        else:
            send_html(message, f"<b>ℹ️ Nothing to reset for:</b> <code>{target}</code>")

    except Exception as e:
        send_html(message,
            f"<b>❌ Error:</b> <code>{escape(str(e))}</code>\n\n"
            "<b>Usage:</b>\n"
            "Reply: <code>/reset [like|spam|visit]</code>\n"
            "User ID: <code>/reset 123456789 [like|spam|visit]</code>\n"
            "Everyone: <code>/reset all [like|spam|visit]</code>"
        )
        
@bot.message_handler(func=lambda m: m.text.lower().startswith('isbanned') and m.chat.type != 'private')
def handle_isbanned(message):
    try:
        uid = message.text.split(' ')[1]
    except IndexError:
        bot.reply_to(message, "<b>Error:</b> Please provide a UID. Example: <code>isbanned 123456789</code>", parse_mode='HTML')
        return

    searching = bot.reply_to(message, "🔍 Searching player information...")

    result = check_player_info(uid)
    if "error" in result:
        bot.edit_message_text(chat_id=message.chat.id, message_id=searching.message_id, 
                            text="<b>❌ Player Not Found</b>\nThe provided UID doesn't exist or couldn't be fetched.", 
                            parse_mode='HTML')
    else:
        if result['ban_status'] == 'Not banned':
            response = f"""<b>🎮 Player Information</b>

🆔 <b>UID:</b> {uid}
👤 <b>Nickname:</b> {result['nickname']}
🌍 <b>Region:</b> {result['region']}
✅ <b>Status:</b> Not banned"""
        else:
            if result['ban_period']:
                ban_details = f"⚠️ Banned from the past {result['ban_period']}"
            else:
                ban_details = "⛔ Banned indefinitely"
            
            response = f"""<b>🎮 Player Information</b>

🆔 <b>UID:</b> {uid}
👤 <b>Nickname:</b> {result['nickname']}
🌍 <b>Region:</b> {result['region']}
❌ <b>Status:</b> {ban_details}"""

        bot.edit_message_text(chat_id=message.chat.id, message_id=searching.message_id, 
                            text=response, parse_mode='HTML')

@bot.message_handler(func=lambda m: m.text.lower().startswith('region') and m.chat.type != 'private')
def handle_region(message):
    try:
        uid = message.text.split(' ')[1]
    except IndexError:
        bot.reply_to(message, "<b>Error:</b> Please provide a UID. Example: <code>region 123456789</code>", parse_mode='HTML')
        return

    searching = bot.reply_to(message, "🔍 Searching player region...")

    result = check_player_info(uid)
    if "error" in result:
        bot.edit_message_text(chat_id=message.chat.id, message_id=searching.message_id, 
                            text="<b>❌ Player Not Found</b>\nThe provided UID doesn't exist or couldn't be fetched.", 
                            parse_mode='HTML')
    else:
        response = f"""<b>🌍 Region Information</b>

🆔 <b>UID:</b> {uid}
👤 <b>Nickname:</b> {result['nickname']}
🌐 <b>Region:</b> {result['region']}"""

        bot.edit_message_text(chat_id=message.chat.id, message_id=searching.message_id, 
                            text=response, parse_mode='HTML')

def check_player_info(target_id):
    url = 'https://topup.pk/api/auth/player_id_login'
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
        'Content-Type': 'application/json',
        'Origin': 'https://topup.pk',
        'Referer': 'https://topup.pk/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36',
        'sec-ch-ua': '"Not A(Brand";v="8", "Chromium";v="132"',
        'sec-ch-ua-mobile': '?1',
        'sec-ch-ua-platform': '"Android"',
    }
    cookies = {
        'source': 'mb',
        'region': 'PK',
        'mspid2': 'd77ad4216f816ffdebec3091592e10dc',
        'language': 'en',
        '_ga': 'GA1.1.872969922.1743955565',
        'session_key': 'cjbrcpxku9m3k89ke0jods82i9a7tqu4',
        'datadome': '0EJgFJwsleqTVDQuHKyCd~zRUPjlXXiZzf_Z1FsrTBArVca5B3Ue4G5KWkq3~rdYxb6FLnTX5XRFOF7iCsVtVxCZHCutjT9w6_mZmVoFpTf_CFCpo0cNo6r_uHwdX_5z',
        '_ga_C956TFJLD0': 'GS1.1.1743955565.1.0.1743955579.0.0.0'
    }
    json_data = {
        "app_id": 100067,
        "login_id": target_id
    }

    try:
        res = requests.post(url, headers=headers, cookies=cookies, json=json_data)
        if res.status_code != 200 or not res.json().get('nickname'):
            return {"error": "ID NOT FOUND"}

        player_data = res.json()
        nickname = player_data.get('nickname', 'N/A')
        region = player_data.get('region', 'N/A')

        ban_url = f'https://ff.garena.com/api/antihack/check_banned?lang=en&uid={target_id}'
        ban_headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'sec-ch-ua': '"Not A(Brand";v="8", "Chromium";v="132"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'x-requested-with': 'B6FksShzIgjfrYImLpTsadjS86sddhFH',
            'referer': 'https://ff.garena.com/en/support/',
        }

        ban_response = requests.get(ban_url, headers=ban_headers)
        ban_data = ban_response.json()

        if ban_data.get("status") == "success" and "data" in ban_data:
            is_banned = ban_data["data"].get("is_banned", 0)
            period = ban_data["data"].get("period", 0)
            if is_banned:
                ban_message = f"Banned from last {period} months" if period > 0 else "Banned indefinitely"
            else:
                ban_message = "Not banned"
        else:
            ban_message = "Ban status unknown"

        return {
            "nickname": nickname,
            "region": region,
            "ban_status": ban_message,
            "ban_period": f"{period} months" if is_banned and period > 0 else None
        }

    except requests.exceptions.RequestException as e:
        return {"error": "Request failed or timed out"}

# GET COMMAND
@bot.message_handler(func=lambda m: m.text.lower().startswith('search') and m.chat.type != 'private')
def handle_search(message):
    try:
        # Extract nickname from command
        parts = message.text.split()
        if len(parts) < 2:
            return send_html(message, 
                "<b>❌ Error:</b> Please provide a nickname to search.\n"
                "<b>Usage:</b> <code>search nickname</code>\n"
                "<b>Example:</b> <code>search ProPlayer123</code>"
            )
            
        nickname = ' '.join(parts[1:])
        searching_msg = bot.reply_to(message, f"🔍 Searching for player: <code>{escape(nickname)}</code>...")
        
        # Make API request
        api_url = f"https://name-search-api.vercel.app/search?nickname={nickname}"
        response = requests.get(api_url, timeout=30)
        data = response.json()
        
        # Handle response
        if "message" in data and data["message"] == "No players found in any region.":
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=searching_msg.message_id,
                text=f"<b>❌ No players found</b>\nNo matches found for nickname: <code>{escape(nickname)}</code>",
                parse_mode='HTML'
            )
            return
            
        if "players" not in data or not data["players"]:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=searching_msg.message_id,
                text=f"<b>⚠️ Unexpected response</b>\nCould not process search results for: <code>{escape(nickname)}</code>",
                parse_mode='HTML'
            )
            return
            
        # Format the response
        count = data.get("count", len(data["players"]))
        response_text = [
            f"<b>🔎 Search Results for:</b> <code>{escape(nickname)}</code>",
            f"<b>📊 Total Found:</b> <code>{count}</code>",
            "<code>━━━━━━━━━━━━━━━━</code>"
        ]
        
        for i, player in enumerate(data["players"], 1):
            last_login = datetime.fromtimestamp(player["last_login"]).strftime('%Y-%m-%d') if "last_login" in player else "Unknown"
            response_text.append(
                f"<b>👤 Player {i}</b>\n"
                f"🆔 <b>Account UID:</b> <code>{player.get('account_id', 'N/A')}</code>\n"
                f"🏷️ <b>Nickname:</b> <code>{escape(player.get('nickname', 'N/A'))}</code>\n"
                f"🌍 <b>Region:</b> <code>{player.get('region', 'N/A')}</code>\n"
                f"📅 <b>Last Login:</b> <code>{last_login}</code>\n"
                f"🆙 <b>Level:</b> <code>{player.get('level', 'N/A')}</code>"
            )
            if i < len(data["players"]):
                response_text.append("<code>━━━━━━━━━━━━━━━━</code>")
                
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text="\n".join(response_text),
            parse_mode='HTML'
        )
        
    except requests.Timeout:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            text="<b>❌ Request Timeout!</b>\nThe search took too long to complete.",
            parse_mode='HTML'
        )
    except Exception:
            send_html(
        message,
        "<b>⚠️ API Error!</b>\n<i>The request failed or took too long to respond.</i>"
    )

def get_br_rank(points):
    br_rank_scores = [
        {"min": 1000,  "max": 1299,  "rank": "Bronze I - III"},
        {"min": 1300,  "max": 1399,  "rank": "Bronze III - Silver I"},
        {"min": 1400,  "max": 1499,  "rank": "Silver I - II"},
        {"min": 1500,  "max": 1599,  "rank": "Silver II - III"},
        {"min": 1600,  "max": 1724,  "rank": "Silver III - Gold I"},
        {"min": 1725,  "max": 1849,  "rank": "Gold I - II"},
        {"min": 1850,  "max": 1974,  "rank": "Gold II - III"},
        {"min": 1975,  "max": 2099,  "rank": "Gold III - IV"},
        {"min": 2100,  "max": 2224,  "rank": "Gold IV - Platinum I"},
        {"min": 2225,  "max": 2349,  "rank": "Platinum I - II"},
        {"min": 2350,  "max": 2474,  "rank": "Platinum II - III"},
        {"min": 2475,  "max": 2599,  "rank": "Platinum III - IV"},
        {"min": 2600,  "max": 2749,  "rank": "Platinum IV - V"},
        {"min": 2750,  "max": 2899,  "rank": "Platinum V - Diamond I"},
        {"min": 2900,  "max": 3049,  "rank": "Diamond I - II"},
        {"min": 3050,  "max": 3199,  "rank": "Diamond II - III"},
        {"min": 3200,  "max": 3349,  "rank": "Diamond III - IV"},
        {"min": 3350,  "max": 3499,  "rank": "Diamond IV - V"},
        {"min": 3500,  "max": 3799,  "rank": "Heroic"},
        {"min": 3800,  "max": 4299,  "rank": "Heroic"},
        {"min": 4300,  "max": 4899,  "rank": "Heroic - Elite Heroic"},
        {"min": 4900,  "max": 5499,  "rank": "Elite Heroic"},
        {"min": 5500,  "max": 6299,  "rank": "Elite Heroic"},
        {"min": 6300,  "max": 7099,  "rank": "Elite Heroic - Master"},
        {"min": 7100,  "max": 7999,  "rank": "Master"},
        {"min": 8000,  "max": 8999,  "rank": "Master - Elite Master"},
        {"min": 9000,  "max": 9999,  "rank": "Elite Master"},
        {"min": 10000, "max": 11999, "rank": "Elite Master"},
        {"min": 12000, "max": 999999, "rank": "Grand Master"}
    ]
    
    try:
        points = int(points)
        for rank in br_rank_scores:
            if rank["min"] <= points <= rank["max"]:
                return rank["rank"]
        return "Unranked"
    except (ValueError, TypeError):
        return "Not Found"

def format_player_response(data, uid):
    pi = data.get("player_info", {})
    info = pi.get("basicInfo", {})
    captain = pi.get("captainBasicInfo", {})
    clan = pi.get("clanBasicInfo", {})
    pet = pi.get("petInfo", {})
    social = pi.get("socialInfo", {})
    profile = pi.get("profileInfo", {})
    credit = pi.get("creditScoreInfo", {})
    maps = data.get("workshop_maps", [])
    
    def ts(t): 
        if not t or not str(t).isdigit():
            return "Not Found"
        dt = datetime.fromtimestamp(int(t))
        return dt.strftime('%d %B %Y at %H:%M:%S')
    
    def j(val): 
        return ', '.join(str(i) for i in val) if isinstance(val, list) and val else "Not Found"
    
    custom_footer = get_db_value("custom_footer", 
        "JOIN US\nOur Group: https://t.me/vampire\nOur Channel: https://t.me/gaane")
    
    # Account Info
    br_points = info.get("rankingPoints", "Not Found")
    br_rank = get_br_rank(br_points) if br_points != "Not Found" else "Not Found"
    
    account_info = f"""
<b>ACCOUNT INFO:</b>
┌ 👤 ACCOUNT BASIC INFO
├─ Total Diamonds Topped Up & Claimed: Inactive
├─ Prime Level: {info.get("primeLevel", {}).get("level", "Not Found")}
├─ Name: {escape(info.get("nickname", "Not Found"))}
├─ UID: {uid}
├─ Level: {info.get("level", "Not Found")} (Exp: {info.get("exp", "Not Found")})
├─ Region: {info.get("region", "Not Found")}
├─ Likes: {info.get("liked", "Not Found")}
├─ Honor Score: {credit.get("creditScore", "Not Found")}
├─ Celebrity Status: False
├─ Evo Access Badge: inactive
├─ Title: {info.get("title", "Not Found")}
└─ Signature: "{escape(social.get("signature", "Not Found"))}"
"""
    
    # Account Activity
    account_activity = f"""
<b>ACCOUNT ACTIVITY:</b>
┌ 🎮 ACCOUNT ACTIVITY
├─ Most Recent OB: {info.get("releaseVersion", "Not Found").replace("VERSION_", "")}
├─ Fire Pass: {"Elite" if info.get("hasElitePass") else "Basic"}
├─ Current BP Badges: {info.get("badgeCnt", "Not Found")}
├─ BR Rank: {br_rank} ({br_points})
├─ CS Rank: {info.get("csRank", "Not Found")} ({info.get("csRankingPoints", "Not Found")})
├─ Created At: {ts(info.get("createAt"))}
└─ Last Login: {ts(info.get("lastLoginAt"))}
"""
    
    # Account Overview
    account_overview = f"""
<b>ACCOUNT OVERVIEW:</b>
┌ 👕 ACCOUNT OVERVIEW
├─ Avatar ID: {profile.get("avatarId", "Not Found")}
├─ Banner ID: {info.get("bannerId", "Not Found")}
├─ Pin ID: {info.get("pinId", "Not Found")}
├─ Equipped Skills: {j(profile.get("equipedSkills", []))}
├─ Equipped Gun ID: Not Found
├─ Equipped Animation ID: Not Equipped
├─ Transform Animation ID: Not Equipped
└─ Outfits: Graphically Presented Below! 😉
"""
    
    # Pet Details
    pet_details = f"""
<b>PET DETAILS:</b>
┌ 🐾 PET DETAILS
├─ Equipped?: {"Yes" if pet.get("isSelected") else "No"}
├─ Pet Name: {pet.get("name", "Not Found")}
├─ Pet Type: {pet.get("type", "Not Found")}
├─ Pet Exp: {pet.get("exp", "Not Found")}
└─ Pet Level: {pet.get("level", "Not Found")}
"""
    
    # Guild Info
    guild_info = f"""
<b>GUILD INFO:</b>
┌ 🛡️ GUILD INFO
├─ Guild Name: {clan.get("clanName", "Not Found")}
├─ Guild ID: {clan.get("clanId", "Not Found")}
├─ Guild Level: {clan.get("clanLevel", "Not Found")}
├─ Live Members: {clan.get("memberNum", "Not Found")}
└─ Leader Info:
    ├─ Leader Name: {escape(captain.get("nickname", "Not Found"))}
    ├─ Leader UID: {captain.get("accountId", "Not Found")}
    ├─ Leader Level: {captain.get("level", "Not Found")} (Exp: {captain.get("exp", "Not Found")})
    ├─ Leader Created At: {ts(captain.get("createAt", "Not Found"))}
    ├─ Leader Last Login: {ts(captain.get("lastLoginAt", "Not Found"))}
    ├─ Leader Title: {captain.get("title", "Not Found")}
    ├─ Leader Current BP Badges: {captain.get("badgeCnt", "Not Found")}
    ├─ Leader BR: {get_br_rank(captain.get("rankingPoints", "Not Found"))} ({captain.get("rankingPoints", "Not Found")})
    └─ Leader CS: {captain.get("csRank", "Not Found")} ({captain.get("csRankingPoints", "Not Found")})
"""
    
    # Public Craftland Maps
    craftland_maps = ["<b>PUBLIC CRAFTLAND MAPS:</b>", "┌ 🗺️ PUBLIC CRAFTLAND MAPS"]
    if maps:
        for m in maps:
            craftland_maps.append(f"#FREEFIRE{m.get('Code', '')}")
    else:
        craftland_maps.append("Not Found")
    craftland_maps = "\n".join(craftland_maps)
    
    # Combine all sections
    full_response = (
        account_info + "\n" +
        account_activity + "\n" +
        account_overview + "\n" +
        pet_details + "\n" +
        guild_info + "\n" +
        craftland_maps + "\n\n" +
        custom_footer
    )
    
    return full_response
    
# only info without banner and image
# @bot.message_handler(func=lambda msg: msg.text.lower().startswith("get "))
# def handle_get(message):
    parts = message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        return send_html(message,
            "<b>❌ Usage:</b>\n<code>get [UID]</code>\nExample: <code>get 12345678</code>",
            parse_mode="HTML"
        )

    uid = parts[1]
    region_info = check_player_info(uid)
    if not region_info or "region" not in region_info:
        return send_html(message, "<b>❌ Invalid UID or region not found.</b>", parse_mode="HTML")

    region = region_info["region"]

    msg = bot.reply_to(
        message,
        f"<b>🔍 Retrieving player info...</b>\n🆔 UID: <code>{uid}</code>\n🌍 Region: <code>{region}</code>",
        parse_mode="HTML"
    )

    try:
        url = f"https://aditya-info-v9op.onrender.com/player-info?uid={uid}&region={region}"
        res = requests.get(url, timeout=30)
        data = res.json()
        
        if "error" in data or not data.get("player_info"):
            return bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg.message_id,
                text="<b>❌ Invalid UID. API returned no data.</b>",
                parse_mode="HTML"
            )

        response = format_player_response(data, uid)
        
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text=response,
            parse_mode="HTML"
        )

    except Exception as e:
        # Optional: Log the full error for your own debugging
        print(f"Error fetching data for UID {uid}: {e}")

        # Show a clean message to the user
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text="<b>⚠️ An unexpected error occurred while fetching player info. Please try again later.</b>",
            parse_mode="HTML"
        )


@bot.message_handler(func=lambda m: m.text.lower().startswith('get') and m.chat.type != 'private')
def handle_prefixless_get(message):
    parts = message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        return bot.reply_to(message,
            "❌ Error: Please provide a UID.\nUsage: get [UID]\nExample: get 12345678",
            parse_mode="HTML"
        )

    uid = parts[1]
    region_info = check_player_info(uid)
    if not region_info or "region" not in region_info:
        return bot.reply_to(message, "<b>❌ Invalid UID or region not found.</b>", parse_mode="HTML")

    region = region_info["region"]

    msg = bot.reply_to(
        message,
        f"<b>🔍 Retrieving player info...</b>\n🆔 UID: <code>{uid}</code>\n🌍 Region: <code>{region}</code>",
        parse_mode="HTML"
    )

    try:
        info_url = f"https://ff-player-info.vercel.app/player-info?uid={uid}&region={region}"
        info_res = requests.get(info_url, timeout=30)
        info_data = info_res.json()
        
        if "error" in info_data or not info_data.get("player_info"):
            return bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg.message_id,
                text="<b>❌ Invalid UID. API returned no data.</b>",
                parse_mode="HTML"
            )

        text_response = format_player_response(info_data, uid)
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text=text_response,
            parse_mode="HTML"
        )

        # 🖼️ Send Banner as Sticker
        banner_url = f"https://ff-banner-image.vercel.app/banner-image?uid={uid}&region={region}"
        banner_res = requests.get(banner_url, stream=True, timeout=30)
        if banner_res.status_code == 200:
            with open('temp_banner.jpg', 'wb') as f:
                banner_res.raw.decode_content = True
                shutil.copyfileobj(banner_res.raw, f)

            im = Image.open('temp_banner.jpg').convert("RGBA")
            im.save("temp_banner.webp", "WEBP")

            with open('temp_banner.webp', 'rb') as sticker:
                bot.send_sticker(
                    chat_id=message.chat.id,
                    sticker=sticker,
                    reply_to_message_id=message.message_id
                )

            os.remove('temp_banner.jpg')
            os.remove('temp_banner.webp')

        # 👕 Send Outfit Image (no caption)
        outfit_url = f"https://ff-outfit-image.vercel.app/outfit-image?uid={uid}&region={region}"
        outfit_res = requests.get(outfit_url, stream=True, timeout=30)
        if outfit_res.status_code == 200:
            with open('temp_outfit.jpg', 'wb') as f:
                outfit_res.raw.decode_content = True
                shutil.copyfileobj(outfit_res.raw, f)
            
            with open('temp_outfit.jpg', 'rb') as photo:
                bot.send_photo(
                    chat_id=message.chat.id,
                    photo=photo,
                    reply_to_message_id=message.message_id  # ✅ reply to user, no caption
                )

            os.remove('temp_outfit.jpg')

    except requests.exceptions.Timeout:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text="<b>❌ Request Timeout!</b>\nOne or more APIs took too long to respond.",
            parse_mode="HTML"
        )
    except Exception as e:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text=f"<b>⚠️ Error:</b>\n<code>{escape(str(e))}</code>",
            parse_mode="HTML"
    )

@bot.message_handler(commands=['setfooter'])
def handle_setfooter(message):
    if not check_admin(message.from_user.id):
        return send_html(message, "<b>🚫 Only bot admins can use this command.</b>")
    
    try:
        new_footer = message.text.split(' ', 1)[1]
        set_db_value("custom_footer", new_footer)
        send_html(message, "<b>✅ Footer updated successfully!</b>")
    except IndexError:
        send_html(message, "<b>❌ Please provide footer text.</b>\nUsage: <code>/setfooter Your footer text here</code>")



# ===== START BOT =====
if __name__ == "__main__":
    print("🤖 Bot is now running...")
    bot.polling(none_stop=True)
