#-------------------------------------------
# BOT MADE BY WINTER
#-------------------------------------------
import os
import json
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import requests
from github import Github

# -------------------------------
# ENV VARIABLES (set in Replit Secrets)
# -------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
BOT_OWNER_GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
BOT_OWNER_GITHUB_REPO = os.environ.get("GITHUB_REPO")
ADMIN_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID"))

# JWT generator API
MAX_RETRIES = 5
MAX_WORKERS = 15
API_URL_TEMPLATE = "https://jwttokengenerator-orpin.vercel.app/token?uid={uid}&password={password}"

# Local user data
USER_DATA_FILE = "saveduid.json"

# Auto-generate interval in hours
UPDATE_INTERVAL_HOURS = 8

# In-memory user data
user_data = {}

# Conversation states
(NEWUSER_GUESTS,) = range(1)

# -------------------------------
# Persistence helpers
# -------------------------------
def load_user_data():
    global user_data
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, "r") as f:
                user_data = json.load(f)
        except:
            user_data = {}
    else:
        user_data = {}

def save_user_data():
    with open(USER_DATA_FILE, "w") as f:
        json.dump(user_data, f, indent=4)

# -------------------------------
# JWT generator
# -------------------------------
def fetch_token(account):
    uid = account.get("uid")
    password = account.get("password")
    if not uid or not password:
        return None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            url = API_URL_TEMPLATE.format(uid=uid, password=password)
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                if token:
                    return {"uid": uid, "token": token}
        except Exception:
            time.sleep(0.5)
    return None

def generate_tokens_for_user(user_id):
    user = user_data.get(str(user_id))
    if not user or "guest_accounts" not in user:
        return None
    accounts = user["guest_accounts"]
    tokens = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_token, acc) for acc in accounts]
        for future in as_completed(futures):
            res = future.result()
            if res:
                tokens.append(res)
    # Save local token.json
    os.makedirs("generated", exist_ok=True)
    local_path = os.path.join("generated", f"{user_id}_token.json")
    with open(local_path, "w") as f:
        json.dump(tokens, f, indent=4)
    user["last_tokens_count"] = len(tokens)
    user["last_local_path"] = local_path
    user["last_generated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    save_user_data()
    return {"count": len(tokens), "local_path": local_path, "tokens": tokens}

# -------------------------------
# GitHub helpers
# -------------------------------
def upload_file_to_owner_github(local_filepath, target_filename="token.json"):
    if not os.path.exists(local_filepath):
        return False, "Local file not found"
    gh = Github(BOT_OWNER_GITHUB_TOKEN)
    repo = gh.get_repo(BOT_OWNER_GITHUB_REPO)
    with open(local_filepath, "r") as f:
        content = f.read()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            parsed.pop("github_pat", None)
        if isinstance(parsed, list):
            for obj in parsed:
                if isinstance(obj, dict):
                    obj.pop("github_pat", None)
        content = json.dumps(parsed, indent=4)
    except Exception:
        pass
    try:
        try:
            existing = repo.get_contents(target_filename)
            repo.update_file(existing.path, f"Update {target_filename}", content, existing.sha)
        except Exception:
            repo.create_file(target_filename, f"Create {target_filename}", content)
        return True, f"Uploaded to {BOT_OWNER_GITHUB_REPO}:{target_filename}"
    except Exception as e:
        return False, f"GitHub upload failed: {e}"

# -------------------------------
# Telegram handlers
# -------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to the Token Manager Bot!\n\n"
        "🔹 Use /newuser to set up your account\n"
        "🔹 Use /token to generate tokens\n"
        "🔹 Use /updatetoken to upload to GitHub\n"
        "🔹 Use /status to see last generation info\n"
        "🔹 Use /delete to remove your data\n\n"
        "Owner: @winterxff"
    )

async def newuser_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📤 Send your guest accounts JSON:\n"
        '[{"uid":"123","password":"..."},{"uid":"456","password":"..."}]'
    )
    return NEWUSER_GUESTS

async def newuser_guests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != str(ADMIN_CHAT_ID):
        await update.message.reply_text("❌ Only owner can use this bot.")
        return ConversationHandler.END

    txt = update.message.text.strip()
    try:
        guest_accounts = json.loads(txt)
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Invalid JSON. Please send a valid JSON list of accounts.")
        return NEWUSER_GUESTS
    if not isinstance(guest_accounts, list) or not all(isinstance(i, dict) for i in guest_accounts):
        await update.message.reply_text("❌ JSON must be a list of objects, e.g. [{\"uid\":\"123\",\"password\":\"...\"}, ...].")
        return NEWUSER_GUESTS
    for idx, obj in enumerate(guest_accounts):
        if "uid" not in obj or "password" not in obj:
            await update.message.reply_text(f"❌ Entry {idx} is missing 'uid' or 'password'.")
            return NEWUSER_GUESTS

    user_data.setdefault(user_id, {})
    user_data[user_id]["guest_accounts"] = guest_accounts
    user_data[user_id]["last_tokens_count"] = 0
    save_user_data()

    # Save guest accounts persistently
    os.makedirs("uploaded_guest_jsons", exist_ok=True)
    local_path = os.path.join("uploaded_guest_jsons", f"{user_id}_guest_accounts.json")
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(guest_accounts, f, indent=4, ensure_ascii=False)
    user_data[user_id]["last_guest_local_path"] = local_path
    save_user_data()

    await update.message.reply_text(
        "✅ Guest accounts saved!\n\nUse:\n• /token to generate\n• /updatetoken to upload generated tokens"
    )
    return ConversationHandler.END

async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != str(ADMIN_CHAT_ID):
        await update.message.reply_text("❌ Only owner can use this bot.")
        return

    result = generate_tokens_for_user(user_id)
    if result:
        await update.message.reply_text(f"✅ Tokens generated for {result['count']} accounts.\nUse /updatetoken to upload them.")
    else:
        await update.message.reply_text("❌ No guest accounts found. Use /newuser first.")

async def updatetoken_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != str(ADMIN_CHAT_ID):
        await update.message.reply_text("❌ Only owner can use this bot.")
        return

    user = user_data.get(user_id)
    if not user or "last_local_path" not in user:
        await update.message.reply_text("❌ No generated tokens found. Run /token first.")
        return
    success, msg = upload_file_to_owner_github(user["last_local_path"])
    if success:
        await update.message.reply_text("✅ Tokens uploaded successfully!")
    else:
        await update.message.reply_text(f"❌ Upload failed: {msg}")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != str(ADMIN_CHAT_ID):
        await update.message.reply_text("❌ Only owner can use this bot.")
        return
    if user_id in user_data:
        del user_data[user_id]
        save_user_data()
    await update.message.reply_text("✅ Your data has been removed locally.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user = user_data.get(user_id)
    if not user:
        await update.message.reply_text("❌ No data found. Use /newuser first.")
        return
    msg = f"📊 Status for user {user_id}:\n"
    msg += f"Guest accounts: {len(user.get('guest_accounts', []))}\n"
    msg += f"Last tokens generated: {user.get('last_tokens_count', 0)}\n"
    msg += f"Last generation time: {user.get('last_generated_at', 'Never')}\n"
    msg += f"Last token file: {user.get('last_local_path', 'N/A')}"
    await update.message.reply_text(msg)

# -------------------------------
# Auto-generation thread
# -------------------------------
def auto_generate_tokens_loop(app):
    async def task():
        while True:
            try:
                for user_id in user_data.keys():
                    if "guest_accounts" in user_data[user_id]:
                        result = generate_tokens_for_user(user_id)
                        if result:
                            upload_file_to_owner_github(result["local_path"])
                            app.bot.send_message(chat_id=ADMIN_CHAT_ID,
                                                 text=f"🕒 [AUTO] Tokens generated and uploaded for user {user_id}\nTotal accounts: {result['count']}")
            except Exception as e:
                app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"⚠️ Auto-generation error: {e}")
            time.sleep(UPDATE_INTERVAL_HOURS * 3600)

    threading.Thread(target=lambda: app.create_task(task()), daemon=True).start()

# -------------------------------
# Main bot
# -------------------------------
def main():
    load_user_data()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newuser", newuser_start)],
        states={NEWUSER_GUESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, newuser_guests)]},
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("token", token_command))
    app.add_handler(CommandHandler("updatetoken", updatetoken_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("status", status_command))

    auto_generate_tokens_loop(app)

    app.run_polling()

if __name__ == "__main__":
    main()
