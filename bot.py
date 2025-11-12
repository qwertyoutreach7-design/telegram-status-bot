#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP Status Checker Bot v2.1
• Додає/видаляє URL по одному
• Автоперевірка кожні 6 годин
• Кеш + оптимізація
• Render Free Tier
"""

import os
import sys
import asyncio
import logging
import json
from typing import List, Tuple, Dict, Optional
from urllib.parse import urlparse, urljoin
from collections import defaultdict
from datetime import datetime, timedelta

import aiohttp
from aiohttp import ClientTimeout
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    filters
)

# ============== Налаштування ==============
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("status-bot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
URLS_FILE = os.path.join(BASE_DIR, "urls.txt")
CACHE_FILE = os.path.join(BASE_DIR, "cache.json")

TIMEOUT = ClientTimeout(total=8, connect=3)
MAX_CONCURRENCY = 30
TG_LIMIT = 3500
CHECK_INTERVAL = 6 * 3600  # 6 годин

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive",
}

# ============== UI ==============
MENU = [["Додати URL", "Запустити перевірку"], ["Список URL", "Видалити URL"], ["Очистити список"]]
KB = ReplyKeyboardMarkup(MENU, resize_keyboard=True)

WAIT_URL_ADD, WAIT_URL_DELETE = range(2)

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

def remove_url(target: str) -> bool:
    urls = load_urls()
    normalized = [normalize_url(u)[1] for u in urls]
    if target not in normalized:
        return False
    urls = [u for u in urls if normalize_url(u)[1] != target]
    with open(URLS_FILE, "w", encoding="utf-8") as f:
        for u in urls:
            f.write(u + "\n")
    return True

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

# ============== Кеш ==============
def load_cache() -> Dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            cutoff = (datetime.now() - timedelta(days=1)).isoformat()
            return {k: v for k, v in data.items() if v.get("time", "") > cutoff}
    except:
        return {}

def save_cache(cache: Dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def get_cached(url: str, cache: Dict) -> Optional[Tuple[int, Optional[str]]]:
    entry = cache.get(url)
    if entry and entry["time"] > (datetime.now() - timedelta(hours=1)).isoformat():
        return entry["status"], entry.get("location")
    return None

def set_cached(url: str, status: int, location: Optional[str], cache: Dict):
    cache[url] = {
        "status": status,
        "location": location,
        "time": datetime.now().isoformat()
    }

# ============== HTTP ==============
async def fetch(session: aiohttp.ClientSession, url: str, cache: Dict) -> Tuple[Optional[int], Optional[str]]:
    cached = get_cached(url, cache)
    if cached:
        return cached
    try:
        async with session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False) as resp:
            loc = resp.headers.get("Location")
            if loc:
                loc = urljoin(str(resp.url), loc)
            result = resp.status, loc
            set_cached(url, resp.status, loc, cache)
            return result
    except:
        set_cached(url, -1, None, cache)
        return None, None

async def check_urls(urls: List[str]) -> List[Tuple[str, str, Optional[int], Optional[str]]]:
    cache = load_cache()
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector, timeout=TIMEOUT) as session:
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        async def task(raw):
            async with sem:
                disp, url = normalize_url(raw)
                status, location = await fetch(session, url, cache)
                return disp, url, status, location
        results = await asyncio.gather(*[task(u) for u in urls])
    save_cache(cache)
    return results

# ============== Форматування ==============
def format_results(results: List[Tuple[str, str, Optional[int], Optional[str]]]) -> Tuple[str, str, str]:
    problems, success, redirects = [], [], defaultdict(list)

    for disp, url, status, loc in results:
        if status is None or status == -1:
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
    r_text = "\n".join(r_parts) if len(r_parts) > 1 else ""

    return p_text, s_text, r_text

# ============== Автоперевірка ==============
async def run_auto_check(context: ContextTypes.DEFAULT_TYPE):
    urls = load_urls()
    if not urls:
        return
    log.info(f"Автоперевірка: {len(urls)} URL...")
    results = await check_urls(urls)
    p, s, r = format_results(results)
    text = "\n\n".join(sec for sec in (p, s, r) if sec and sec != "—")
    if not text.strip():
        text = "Усе гаразд!"
    else:
        text = f"<b>Автоперевірка</b>\n\n{text}"
    log.info(f"Автоперевірка завершена: {len(urls)} URL")

# ============== Обробники ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>HTTP Status Checker v2.1</b>\n\n"
        "• Додавай/видаляй URL\n"
        "• Перевірка за запитом\n"
        "• Автоперевірка кожні 6 годин\n"
        "• Кеш результатів\n\n"
        "Керуй кнопками."
    )
    await update.message.reply_text(msg, reply_markup=KB, parse_mode="HTML")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    cid = update.effective_chat.id

    if text == "Додати URL":
        await update.message.reply_text("Надішли URL (по одному в рядок).")
        return WAIT_URL_ADD
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
    if text == "Видалити URL":
        urls = load_urls()
        if not urls:
            await update.message.reply_text("Список порожній.")
            return ConversationHandler.END
        keyboard = [
            [InlineKeyboardButton(f"{i+1}. {u}", callback_data=f"del:{u}")]
            for i, u in enumerate(urls)
        ]
        keyboard.append([InlineKeyboardButton("Скасувати", callback_data="cancel")])
        await update.message.reply_text(
            "Обери URL для видалення:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAIT_URL_DELETE
    if text == "Очистити список":
        clear_urls()
        await update.message.reply_text("Очищено.", reply_markup=KB)
        return ConversationHandler.END
    return ConversationHandler.END

async def add_url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        await query.edit_message_text("Видалення скасовано.", reply_markup=None)
        return ConversationHandler.END

    if data.startswith("del:"):
        url = data[4:]
        if remove_url(url):
            await query.edit_message_text(f"Видалено: <code>{url}</code>", parse_mode="HTML")
        else:
            await query.edit_message_text("Не знайдено.", reply_markup=None)
        return ConversationHandler.END

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

# ============== ЗАПУСК ==============
def main():
    load_dotenv()
    token = get_token()
    if not token:
        log.error("BOT_TOKEN не задано!")
        sys.exit(1)

    # ПРАВИЛЬНО: без job_queue_enabled — воно вмикається автоматично через [job-queue]
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, button)],
        states={
            WAIT_URL_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url_handler)],
            WAIT_URL_DELETE: [CallbackQueryHandler(delete_callback)]
        },
        fallbacks=[],
        per_message=False,  # ← Без warning
        per_chat=True,
        per_user=False
    )
    app.add_handler(conv)

    port = int(os.environ.get("PORT", 10000))
    app_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME") or "telegram-status-bot-zx0t.onrender.com"
    webhook_url = f"https://{app_host}/{token}"

    log.info(f"Встановлюю webhook: {webhook_url}")

    # Автоперевірка — працює!
    app.job_queue.run_repeating(
        callback=run_auto_check,
        interval=CHECK_INTERVAL,
        first=10
    )

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=token,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()