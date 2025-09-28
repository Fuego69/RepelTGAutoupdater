# -------------------------------------------
# BOT MADE BY WINTER
# -------------------------------------------

import os
import json
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from github import Github
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# -------------------------------
# BOT CONFIG
# -------------------------------
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
BOT_OWNER_GITHUB_TOKEN = "YOUR_GITHUB_PAT"
BOT_OWNER_GITHUB_REPO = "yourusername/your-repo"
OWNER_TARGET_FOLDER = "saved_files"
OWNER_CHAT_ID = 123456789  # Only owner can use the bot

MAX_RETRIES = 5
MAX_WORKERS = 15
API_URL_TEMPLATE = "https://jnl-gen-jwt.vercel.app/token?uid={uid}&password={password}"

USER_DATA_FILE = "user_data.json"
UPDATE_INTERVAL_HOURS = 8

user_data = {}
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
    for _ in range(MAX_RETRIES):
        try:
            url = API_URL_TEMPLATE.format(uid=uid, password=password)
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                if token:
                    return {"uid": uid, "token": token}
        except:
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
    filename = user.get("filename", "token_ind.json")
    os.makedirs("generated", exist_ok=True)
    local_path = os.path.join("generated", f"{user_id}_{filename}")
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
def upload_file_to_owner_github(user_id_str, local_filepath, target_filename=None):
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
        elif isinstance(parsed, list):
            for obj in parsed:
                if isinstance(obj, dict):
                    obj.pop("github_pat", None)
        content = json.dumps(parsed, indent=4)
    except:
        pass
    target_filename = target_filename or os.path.basename(local_filepath)
    target_path = f"{OWNER_TARGET_FOLDER}/{user_id_str}/{target_filename}"
    try:
        try:
            existing = repo.get_contents(target_path)
            repo.update_file(existing.path, f"Update {target_filename}", content, existing.sha)
        except:
            repo.create_file(target_path, f"Create {target_filename}", content)
        return True, f"Uploaded to {BOT_OWNER_GITHUB_REPO}:{target_path}"
    except Exception as e:
        return False, f"GitHub upload failed: {e}"

# -------------------------------
# Telegram handlers
# -------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    await update.message.reply_text(
        "👋 Welcome to the Owner-only Token Bot!\n\n"
        "Commands:\n"
        "/newuser - Add guest accounts\n"
        "/token - Generate JWT tokens\n"
        "/updatetoken - Upload generated tokens\n"
        "/status - Check last token info\n"
        "/delete - Remove all data"
    )

async def newuser_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    await update.message.reply_text(
        "📤 Send your guest accounts JSON:\n"
        '[{"uid":"123","password":"..."},{"uid":"456","password":"..."}]'
    )
    return NEWUSER_GUESTS

async def newuser_guests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    txt = update.message.text.strip()
    try:
        guest_accounts = json.loads(txt)
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Invalid JSON. Send valid list of accounts.")
        return NEWUSER_GUESTS
    if not isinstance(guest_accounts, list) or not all(isinstance(i, dict) for i in guest_accounts):
        await update.message.reply_text("❌ JSON must be list of dicts with uid & password.")
        return NEWUSER_GUESTS
    for idx, obj in enumerate(guest_accounts):
        if "uid" not in obj or "password" not in obj:
            await update.message.reply_text(f"❌ Entry {idx} missing uid or password.")
            return NEWUSER_GUESTS
    user_data[user_id] = {"guest_accounts": guest_accounts, "last_tokens_count": 0}
    save_user_data()
    await update.message.reply_text("✅ Guest accounts saved!\nUse /token to generate JWTs.")
    return ConversationHandler.END

async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    user_id = str(update.effective_user.id)
    result = generate_tokens_for_user(user_id)
    if result:
        await update.message.reply_text(f"✅ Tokens generated for {result['count']} accounts.\nUse /updatetoken to upload them.")
    else:
        await update.message.reply_text("❌ No guest accounts found. Use /newuser first.")

async def updatetoken_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    user_id = str(update.effective_user.id)
    user = user_data.get(user_id)
    if not user or "last_local_path" not in user:
        await update.message.reply_text("❌ No generated tokens found. Run /token first.")
        return
    success, msg = upload_file_to_owner_github(user_id, user["last_local_path"])
    if success:
        await update.message.reply_text("✅ Tokens uploaded successfully!")
    else:
        await update.message.reply_text(f"❌ Upload failed: {msg}")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    user_id = str(update.effective_user.id)
    user = user_data.get(user_id)
    if not user:
        await update.message.reply_text("❌ No data found.")
        return
    msg = (
        f"📊 Status for owner:\n"
        f"Guest accounts: {len(user.get('guest_accounts', []))}\n"
        f"Last tokens generated: {user.get('last_tokens_count', 0)}\n"
        f"Last generation time: {user.get('last_generated_at', 'Never')}\n"
        f"Last token file: {user.get('last_local_path', 'N/A')}"
    )
    await update.message.reply_text(msg)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    user_id = str(update.effective_user.id)
    if user_id in user_data:
        del user_data[user_id]
        save_user_data()
    await update.message.reply_text("✅ All owner data removed.")

# -------------------------------
# Auto-generation background loop
# -------------------------------
def auto_generate_tokens_loop(app):
    import asyncio
    async def loop():
        while True:
            try:
                for user_id in user_data.keys():
                    if "guest_accounts" in user_data[user_id]:
                        result = generate_tokens_for_user(user_id)
                        if result:
                            upload_file_to_owner_github(user_id, result["local_path"])
                            await app.bot.send_message(
                                chat_id=OWNER_CHAT_ID,
                                text=f"🕒 [AUTO] Tokens generated and uploaded for user {user_id}\nTotal accounts: {result['count']}"
                            )
            except Exception as e:
                await app.bot.send_message(chat_id=OWNER_CHAT_ID, text=f"⚠️ Auto-generation error: {e}")
            await asyncio.sleep(UPDATE_INTERVAL_HOURS * 3600)
    asyncio.create_task(loop())

# -------------------------------
# Main
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
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("delete", delete_command))

    # Start the auto-generation loop
    threading.Thread(target=lambda: auto_generate_tokens_loop(app), daemon=True).start()

    app.run_polling()

if __name__ == "__main__":
    main()