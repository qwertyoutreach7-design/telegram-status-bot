#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP Status Checker Bot
• Додає URL
• Перевіряє статуси
• Групує редіректи
• Працює на Render через webhook
"""

import os
import sys
import asyncio  # ← ДОДАНО!
import logging
from typing import List, Tuple, Dict, Optional
from urllib.parse import urlparse, urljoin
from collections import defaultdict

import aiohttp
from aiohttp import ClientTimeout
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)

# ============== Налаштування ==============
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("status-bot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
URLS_FILE = os.path.join(BASE_DIR, "urls.txt")

TIMEOUT = ClientTimeout(total=8, connect=3)
MAX_CONCURRENCY = 20
TG_LIMIT = 3500

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive",
}

# ============== UI ==============
MENU = [["Додати URL", "Запустити перевірку"], ["Список URL", "Очистити список"]]
KB = ReplyKeyboardMarkup(MENU, resize_keyboard=True)
WAIT_URL = 1

# ============== Утіліти ==============
def get_token() -> str:
    if "--token" in sys.argv:
        idx = sys.argv.index("--token")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1].strip()
    return os.getenv("BOT_TOKEN", "").strip()

def chunk_text(text: str, limit: int = TG_LIMIT) -> List[str]:
    if not text or len(text) <= limit:
        return [text] if text else []
    lines = text.split("\n")
    parts, cur, size = [], [], 0
    for line in lines:
        ln = len(line) + 1
        if cur and size + ln > limit:
            parts.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += ln
    if cur:
        parts.append("\n".join(cur))
    return parts

def normalize_url(s: str) -> Tuple[str, str]:
    s = s.strip()
    if s.startswith(("http://", "https://")):
        return s, s
    return s, f"https://{s}"

def clean_urls(text: str) -> List[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and " " not in ln]
    urls = [ln for ln in lines if ln.startswith(("http://", "https://")) or "." in ln]
    seen = set()
    uniq = []
    for url in urls:
        key = url[:-1] if url.endswith("/") else url
        if key not in seen:
            seen.add(key)
            uniq.append(url)
    return uniq

def load_urls() -> List[str]:
    if not os.path.exists(URLS_FILE):
        return []
    with open(URLS_FILE, "r", encoding="utf-8") as f:
        return clean_urls(f.read())

def save_urls(new_urls: List[str]) -> Tuple[List[str], List[str]]:
    os.makedirs(BASE_DIR, exist_ok=True)
    existing = {(u[:-1] if u.endswith("/") else u) for u in load_urls()}
    added, skipped = [], []
    for raw in new_urls:
        _, url = normalize_url(raw)
        key = url[:-1] if url.endswith("/") else url
        if key in existing:
            skipped.append(raw)
        else:
            added.append(url)
            existing.add(key)
    if added:
        with open(URLS_FILE, "a", encoding="utf-8") as f:
            for url in added:
                f.write(url + "\n")
    return added, skipped

def clear_urls():
    if os.path.exists(URLS_FILE):
        os.remove(URLS_FILE)

def get_host(url: str) -> str:
    try:
        h = urlparse(url).hostname or ""
        return h.lower().removeprefix("www.")
    except:
        return ""

def same_host(a: str, b: str) -> bool:
    return get_host(a) == get_host(b) and get_host(a) != ""

# ============== HTTP ==============
async def fetch(session: aiohttp.ClientSession, url: str) -> Tuple[Optional[int], Optional[str]]:
    try:
        async with session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False) as resp:
            loc = resp.headers.get("Location")
            if loc:
                loc = urljoin(str(resp.url), loc)
            return resp.status, loc
    except:
        return None, None

async def check_urls(urls: List[str]) -> List[Tuple[str, str, Optional[int], Optional[str]]]:
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector, timeout=TIMEOUT) as session:
        sem = asyncio.Semaphore(MAX_CONCURRENCY)  # ← Тепер працює!
        async def task(raw):
            async with sem:
                disp, url = normalize_url(raw)
                status, location = await fetch(session, url)
                return disp, url, status, location
        return await asyncio.gather(*[task(u) for u in urls])

# ============== Форматування ==============
def format_results(results: List[Tuple[str, str, Optional[int], Optional[str]]]) -> Tuple[str, str, str]:
    problems, success, redirects = [], [], defaultdict(list)

    for disp, url, status, loc in results:
        if status is None:
            problems.append(f"{disp} — ERR")
            continue
        if 200 <= status < 300:
            if status == 200 or (loc and same_host(url, loc)):
                success.append(f"{disp}")
            else:
                problems.append(f"{disp} — {status}")
            continue
        if 300 <= status < 400 and loc:
            if same_host(url, loc):
                success.append(f"{disp}")
            else:
                target = get_host(loc) or "unknown"
                redirects[target].append(f"{disp} — {status} → {loc}")
            continue
        problems.append(f"{disp} — {status}")

    p_text = "ПРОБЛЕМИ:\n\n" + "\n".join(problems) if problems else ""
    s_text = f"УСПІШНО ({len(success)}):\n\n" + "\n".join(success) if success else ""
    r_parts = ["РЕДІРЕКТИ:\n"]
    if redirects:
        for domain, items in sorted(redirects.items(), key=lambda x: len(x[1]), reverse=True):
            r_parts.append(f"\n{domain} ({len(items)}):")
            r_parts.extend(f"  {it}" for it in items)
    r_text = = "\n".join(r_parts) if len(r_parts) > 1 else ""

    return p_text, s_text, r_text

# ============== Обробники ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>HTTP Status Checker</b>\n\n"
        "• Додавай URL\n"
        "• Перевіряй статуси\n"
        "• Групуй редіректи\n\n"
        "Керуй кнопками."
    )
    await update.message.reply_text(msg, reply_markup=KB, parse_mode="HTML")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    cid = update.effective_chat.id

    if text == "Додати URL":
        await update.message.reply_text("Надішли URL (по одному на рядок).")
        return WAIT_URL
    if text == "Запустити перевірку":
        await run_check(context, [cid])
        return ConversationHandler.END
    if text == "Список URL":
        urls = load_urls()
        if urls:
            body = f"<b>Список ({len(urls)}):</b>\n\n" + "\n".join(f"{i+1}. <code>{u}</code>" for i, u in enumerate(urls))
            for part in chunk_text(body):
                await update.message.reply_text(part, parse_mode="HTML")
        else:
            await update.message.reply_text("Список порожній.")
        return ConversationHandler.END
    if text == "Очистити список":
        clear_urls()
        await update.message.reply_text("Очищено.", reply_markup=KB)
        return ConversationHandler.END
    return ConversationHandler.END

async def save_urls_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    urls = clean_urls(update.message.text)
    if not urls:
        await update.message.reply_text("Немає URL.", reply_markup=KB)
        return ConversationHandler.END
    added, skipped = save_urls(urls)
    msg = []
    if added:
        msg.append(f"<b>Додано {len(added)}:</b>")
        for u in added[:10]:
            msg.append(f"  • <code>{u}</code>")
        if len(added) > 10:
            msg.append(f"  ...ще {len(added)-10}")
    if skipped:
        msg.append(f"\nПропущено: {len(skipped)}")
    await update.message.reply_text("\n".join(msg), reply_markup=KB, parse_mode="HTML")
    return ConversationHandler.END

async def run_check(context: ContextTypes.DEFAULT_TYPE, chat_ids: List[int]):
    urls = load_urls()
    if not urls:
        for cid in chat_ids:
            await context.bot.send_message(cid, "Список порожній.", reply_markup=KB)
        return
    for cid in chat_ids:
        await context.bot.send_message(cid, f"Перевіряю {len(urls)}...")
    results = await check_urls(urls)
    p, s, r = format_results(results)
    for cid in chat_ids:
        for section in (p or "—", s or "—", r or "—"):
            for part in chunk_text(section):
                await context.bot.send_message(cid, part)
        await context.bot.send_message(cid, "Готово!", reply_markup=KB)

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip() or update.message.text.replace("/check", "", 1).strip()
    urls = clean_urls(text) if text else load_urls()
    if not urls:
        await update.message.reply_text("Немає URL.")
        return
    await update.message.reply_text(f"Перевіряю {len(urls)}...")
    results = await check_urls(urls)
    p, s, r = format_results(results)
    for sec in (p or "—", s or "—", r or "—"):
        for part in chunk_text(sec):
            await update.message.reply_text(part)
    await update.message.reply_text("Готово!", reply_markup=KB)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if clean_urls(update.message.text):
        await check_cmd(update, context)

# ============== ЗАПУСК ==============
def main():
    load_dotenv()
    token = get_token()
    if not token:
        log.error("BOT_TOKEN не задано!")
        sys.exit(1)

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check_cmd))
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, button)],
        states={WAIT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_urls_handler)]},
        fallbacks=[],
    )
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    port = int(os.environ.get("PORT", 10000))
    app_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME") or "telegram-status-bot-zx0t.onrender.com"
    webhook_url = f"https://{app_host}/{token}"

    log.info(f"Встановлюю webhook: {webhook_url}")

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=token,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()