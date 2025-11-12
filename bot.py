#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot — HTTP Status Checker (Enhanced)
• Масове додавання URL
• Групування редіректів по цільових доменах
• Покращений вигляд виводу
"""

import os
import sys
import asyncio
import logging
from typing import List, Optional, Tuple, Dict
from urllib.parse import urlparse, urljoin
from collections import defaultdict

import aiohttp
from aiohttp import ClientTimeout, web
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

CONNECT_SEC = 3
READ_SEC = 5
TOTAL_SEC = 8
TIMEOUT = ClientTimeout(total=TOTAL_SEC, connect=CONNECT_SEC, sock_connect=CONNECT_SEC, sock_read=READ_SEC)
MAX_CONCURRENCY = 20
TG_LIMIT = 3500

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Accept-Encoding": "identity",
}

# ============== UI ==============
MENU = [["Додати URL", "Запустити перевірку"], ["Список URL", "Очистити список"]]
KB = ReplyKeyboardMarkup(MENU, resize_keyboard=True)

WAIT_URL = "WAIT_URL"

# ============== Утіліти ==============
def _get_token(argv: List[str]) -> str:
    if "--token" in argv:
        i = argv.index("--token")
        if i + 1 < len(argv):
            return argv[i + 1].strip()
    return (os.getenv("BOT_TOKEN") or "").strip()

def chunk_text(text: str, limit: int = TG_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text] if text else []
    parts, cur, size = [], [], 0
    for line in text.split("\n"):
        ln = len(line) + 1
        if cur and size + ln > limit:
            parts.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += ln
    if cur:
        parts.append("\n".join(cur))
    return parts

def normalize_to_url(s: str) -> Tuple[str, str]:
    s = (s or "").strip()
    if s.startswith(("http://", "https://")):
        return s, s
    return s, f"https://{s}"

def clean_lines(text: str) -> List[str]:
    raw = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out = []
    for ln in raw:
        if " " in ln:
            continue
        if ln.startswith(("http://", "https://")) or "." in ln:
            out.append(ln)
    seen = set()
    uniq = []
    for x in out:
        key = x[:-1] if x.endswith("/") else x
        if key not in seen:
            seen.add(key)
            uniq.append(x)
    return uniq

def load_urls_from_file() -> List[str]:
    if not os.path.exists(URLS_FILE):
        return []
    with open(URLS_FILE, "r", encoding="utf-8") as f:
        return clean_lines(f.read())

def append_urls_to_file(urls: List[str]) -> Tuple[List[str], List[str]]:
    os.makedirs(BASE_DIR, exist_ok=True)
    have = set((x[:-1] if x.endswith("/") else x) for x in load_urls_from_file())
    added, skipped = [], []
    for u in urls:
        _, url = normalize_to_url(u)
        key = url[:-1] if url.endswith("/") else url
        if key in have:
            skipped.append(u)
        else:
            added.append(url)
            have.add(key)
    if added:
        with open(URLS_FILE, "a", encoding="utf-8") as f:
            for url in added:
                f.write(url + "\n")
    return added, skipped

def clear_urls_file():
    if os.path.exists(URLS_FILE):
        os.remove(URLS_FILE)

def _host(u: str) -> str:
    try:
        h = (urlparse(u).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def _same_host(a: str, b: str) -> bool:
    return _host(a) == _host(b) and bool(_host(a))

def _same_pathish(a: str, b: str) -> bool:
    pa = urlparse(a).path or "/"
    pb = urlparse(b).path or "/"
    return pa.rstrip("/") == pb.rstrip("/")

# ============== HTTP ==============
async def fetch_status(session: aiohttp.ClientSession, url: str) -> Tuple[Optional[int], Optional[str]]:
    try:
        async with session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False) as r:
            status = r.status
            loc = r.headers.get("Location")
            if loc:
                loc = urljoin(str(r.url), loc)
            return status, loc
    except (asyncio.TimeoutError, aiohttp.ClientError, Exception):
        return None, None

async def check_one(session: aiohttp.ClientSession, line: str) -> Tuple[str, str, Optional[int], Optional[str]]:
    disp, start_url = normalize_to_url(line)
    st, loc = await fetch_status(session, start_url)
    return disp, start_url, st, loc

async def check_many(lines: List[str]) -> List[Tuple[str, str, Optional[int], Optional[str]]]:
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENCY, limit_per_host=MAX_CONCURRENCY)
    async with aiohttp.ClientSession(timeout=TIMEOUT, connector=connector) as session:
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        async def _task(x: str):
            async with sem:
                return await check_one(session, x)
        return await asyncio.gather(*[_task(x) for x in lines])

# ============== Рендер ==============
def render_three(pairs: List[Tuple[str, str, Optional[int], Optional[str]]]) -> Tuple[str, str, str]:
    problems, oks200 = [], []
    redirects_by_domain: Dict[str, List[str]] = defaultdict(list)
    
    for disp_url, start_url, st, loc in pairs:
        if st is None:
            problems.append(f"{disp_url} — ERR")
            continue
        
        if 200 <= st < 300:
            if st == 200 or (_same_host(start_url, loc) if loc else False):
                oks200.append(f"{disp_url}")
            else:
                problems.append(f"{disp_url} — {st}")
            continue
        
        if 300 <= st < 400 and loc:
            if _same_host(start_url, loc):
                oks200.append(f"{disp_url}")
            else:
                target_host = _host(loc)
                redirects_by_domain[target_host or "unknown"].append(f"{disp_url} — {st} → {loc}")
            continue
        
        if 400 <= st < 600:
            problems.append(f"{disp_url} — {st}")
            continue
        
        problems.append(f"{disp_url} — {st}")
    
    problems_text = "ПРОБЛЕМИ:\n\n" + "\n".join(problems) if problems else ""
    oks200_text = f"УСПІШНО ({len(oks200)}):\n\n" + "\n".join(oks200) if oks200 else ""
    
    redirects_parts = ["РЕДІРЕКТИ (згруповані по цільових доменах):\n"]
    if redirects_by_domain:
        for domain, items in sorted(redirects_by_domain.items(), key=lambda x: len(x[1]), reverse=True):
            redirects_parts.append(f"\n{domain} ({len(items)}):")
            for item in items:
                redirects_parts.append(f"  {item}")
    redirects_text = "\n".join(redirects_parts) if len(redirects_parts) > 1 else ""
    
    return problems_text, oks200_text, redirects_text

# ============== Handlers ==============
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    welcome_msg = (
        "<b>HTTP Status Checker Bot</b>\n\n"
        "Можливості:\n"
        "• Масове додавання URL\n"
        "• Перевірка статусів\n"
        "• Групування редіректів\n\n"
        "Використовуй кнопки нижче."
    )
    await update.message.reply_text(welcome_msg, reply_markup=KB, parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    cid = update.effective_chat.id
    
    if txt == "Додати URL":
        await update.message.reply_text(
            "Надішли URL.\n\n"
            "• Один або кілька (з нового рядка)\n"
            "• Домени без http:// — додам https://\n\n"
            "Приклад:\n"
            "example.com\n"
            "https://test.com"
        )
        return WAIT_URL
    
    if txt == "Запустити перевірку":
        await run_check_and_reply(context, [cid])
        return ConversationHandler.END
    
    if txt == "Список URL":
        urls = load_urls_from_file()
        if urls:
            body = f"<b>Список URL ({len(urls)}):</b>\n\n"
            body += "\n".join(f"{i+1}. <code>{u}</code>" for i, u in enumerate(urls))
            for ch in chunk_text(body) or ["—"]:
                await update.message.reply_text(ch, parse_mode="HTML")
        else:
            await update.message.reply_text("Список порожній.")
        return ConversationHandler.END
    
    if txt == "Очистити список":
        clear_urls_file()
        await update.message.reply_text("Список очищено.", reply_markup=KB)
        return ConversationHandler.END
    
    return ConversationHandler.END

async def save_url_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Порожньо.", reply_markup=KB)
        return ConversationHandler.END
    
    candidates = clean_lines(text)
    if not candidates:
        await update.message.reply_text("Не знайдено URL.", reply_markup=KB)
        return ConversationHandler.END
    
    added, skipped = append_urls_to_file(candidates)
    response = []
    if added:
        response.append(f"<b>Додано {len(added)}:</b>")
        for url in added[:10]:
            response.append(f"  • <code>{url}</code>")
        if len(added) > 10:
            response.append(f"  ... та ще {len(added)-10}")
    if skipped:
        response.append(f"\nПропущено: {len(skipped)}")
    
    await update.message.reply_text("\n".join(response), reply_markup=KB, parse_mode="HTML")
    return ConversationHandler.END

async def run_check_and_reply(context: ContextTypes.DEFAULT_TYPE, chat_ids: List[int]):
    urls = load_urls_from_file()
    if not urls:
        for cid in chat_ids:
            await context.bot.send_message(cid, "Список порожній.", reply_markup=KB)
        return
    
    for cid in chat_ids:
        await context.bot.send_message(cid, f"Перевіряю {len(urls)} URL...")
    
    pairs = await check_many(urls)
    msg1, msg2, msg3 = render_three(pairs)
    
    for cid in chat_ids:
        for section in (msg1 or "—", msg2 or "—", msg3 or "—"):
            for ch in chunk_text(section) or ["—"]:
                await context.bot.send_message(cid, ch)
        await context.bot.send_message(cid, "Готово!", reply_markup=KB)

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = " ".join(context.args).strip() or update.message.text.replace("/check", "", 1).strip()
    candidates = clean_lines(payload) if payload else load_urls_from_file()
    
    if not candidates:
        await update.message.reply_text("Немає URL.")
        return
    
    await update.message.reply_text(f"Перевіряю {len(candidates)}...")
    pairs = await check_many(candidates)
    msg1, msg2, msg3 = render_three(pairs)
    
    for sec in (msg1 or "—", msg2 or "—", msg3 or "—"):
        for ch in chunk_text(sec) or ["—"]:
            await update.message.reply_text(ch)
    await update.message.reply_text("Готово!", reply_markup=KB)

async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    candidates = clean_lines(update.message.text)
    if not candidates:
        return
    await cmd_check(update, context)

# ============== Webserver для Render ==============
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def run_webserver():
    app = web.Application()
    app.router.add_get("/", health_check)
    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info(f"Webserver запущено на порту {port}")

# ============== Запуск бота ==============
async def run_bot():
    load_dotenv()
    token = _get_token(sys.argv)
    if not token:
        raise RuntimeError("BOT_TOKEN не задано!")
    
    app = ApplicationBuilder().token(token).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler)],
        states={WAIT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_url_state)]},
        fallbacks=[],
    )
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback))
    
    log.info("HTTP Status Checker bot started.")
    await app.run_polling()

# ============== Основний запуск ==============
async def main_async():
    await asyncio.gather(
        run_webserver(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main_async())