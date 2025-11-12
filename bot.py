File "/opt/render/project/src/bot.py", line 308, in main
web_app = web.Application()
          ^^^
NameError: name 'web' is not defined
==> Deploying...
==> Running 'python bot.py'
2025-11-12 11:47:50,277 | INFO | Встановлюю webhook: https://telegram-status-bot-zx0t.onrender.com/8367658201:AAE36fqYVIBr_65NNw0N8fn-3AltljyqYeQ
Traceback (most recent call last):
File "/opt/render/project/src/bot.py", line 319, in <module>
main()
~~~~^^
File "/opt/render/project/src/bot.py", line 310, in main
app.run_webhook(
~~~~~~~~~~~~~~~^
    listen="0.0.0.0",
    ^^^^^^^^^^^^^^^^^
...<3 lines>...
    web_app=web_app
    ^^^^^^^^^^^^^^^
)
^
TypeError: Application.run_webhook() got an unexpected keyword argument 'web_app'
==> Exited with status 1
==> Common ways to troubleshoot your deploy: https://render.com/docs/troubleshooting-deploys
==> Running 'python bot.py'
2025-11-12 11:48:03,251 | INFO | Встановлюю webhook: https://telegram-status-bot-zx0t.onrender.com/8367658201:AAE36fqYVIBr_65NNw0N8fn-3AltljyqYeQ
Traceback (most recent call last):
File "/opt/render/project/src/bot.py", line 319, in <module>
main()
~~~~^^
File "/opt/render/project/src/bot.py", line 310, in main
app.run_webhook(
~~~~~~~~~~~~~~~^
    listen="0.0.0.0",
    ^^^^^^^^^^^^^^^^^
...<3 lines>...
    web_app=web_app
    ^^^^^^^^^^^^^^^
)
^
TypeError: Application.run_webhook() got an unexpected keyword argument 'web_app'

Тут дві основні помилки:

1. **NameError: name 'web' is not defined** — це через відсутність імпорту `web` з `aiohttp`. Потрібно додати `from aiohttp import web`.

2. **TypeError: Application.run_webhook() got an unexpected keyword argument 'web_app'** — параметр `web_app` не підтримується в `run_webhook` у версії python-telegram-bot 21.6. Для додавання кастомних маршрутів (як health check) потрібно використовувати кастомний сервер, але для простоти ми видалимо цей параметр і кастомний health check, оскільки Render приймає сервіс навіть з 404 на `/` (порт відкритий).

Виправлений код нижче працює без health check тексту на `/`, але бот працюватиме на webhook. Якщо потрібно health check, можна додати окремий сервер, але для Render це не обов'язково.

### Виправлений `bot.py`

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP Status Checker Bot
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
MENU = [["➕ Додати URL", "🚀 Запустити перевірку"], ["📋 Список URL", "🗑 Очистити список"]]
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
"""Повертає (display_input, start_url). Гола доменна назва → https://..."""
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

# унікалізація
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
"""Додає список URL. Повертає (added, skipped)"""
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
"""Очищає файл з URL"""
if os.path.exists(URLS_FILE):
    os.remove(URLS_FILE)

def _host(u: str) -> str:
"""Хост у нижньому регістрі без префікса www."""
try:
    h = (urlparse(u).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h
except Exception:
    return ""

def _same_host(a: str, b: str) -> bool:
ha, hb = _host(a), _host(b)
return bool(ha) and ha == hb

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
except asyncio.TimeoutError:
    return None, None
except aiohttp.ClientError:
    return None, None
except Exception:
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
"""
1) Проблеми (ERR/4xx/5xx)
2) 200 — чисті 200 + внутрішні редиректи
3) Редиректи — групуються по цільових доменах
"""
problems, oks200 = [], []
redirects_by_domain: Dict[str, List[str]] = defaultdict(list)

for disp_url, start_url, st, loc in pairs:
    if st is None:
        problems.append(f"❌ {disp_url} — ERR")
        continue
    
    # 2xx
    if 200 <= st < 300:
        if st == 200:
            oks200.append(f"✅ {disp_url}")
        else:
            problems.append(f"⚠️ {disp_url} — {st}")
        continue
    
    # 3xx
    if 300 <= st < 400:
        if loc:
            # Внутрішній редирект → як 200
            if _same_host(start_url, loc) and _same_pathish(start_url, loc):
                oks200.append(f"✅ {disp_url}")
            elif _same_host(start_url, loc):
                oks200.append(f"✅ {disp_url}")
            else:
                # Зовнішній редирект — групуємо по цільовому домену
                target_host = _host(loc)
                if target_host:
                    redirects_by_domain[target_host].append(f"🔄 {disp_url} — {st} → {loc}")
                else:
                    redirects_by_domain["unknown"].append(f"🔄 {disp_url} — {st} → {loc}")
        else:
            problems.append(f"⚠️ {disp_url} — {st}")
        continue
    
    # 4xx/5xx
    if 400 <= st < 600:
        problems.append(f"🚫 {disp_url} — {st}")
        continue
    
    # інші випадки
    problems.append(f"⚠️ {disp_url} — {st}")

# Форматування проблем
problems_text = ""
if problems:
    problems_text = "🔴 ПРОБЛЕМИ:\n\n" + "\n".join(problems)

# Форматування 200
oks200_text = ""
if oks200:
    oks200_text = f"🟢 УСПІШНО ({len(oks200)}):\n\n" + "\n".join(oks200)

# Форматування редіректів з групуванням
redirects_text = ""
if redirects_by_domain:
    redirects_parts = ["🔵 РЕДІРЕКТИ (згруповані по цільових доменах):\n"]
    
    # Сортуємо по кількості редіректів на домен
    sorted_domains = sorted(redirects_by_domain.items(), key=lambda x: len(x[1]), reverse=True)
    
    for domain, items in sorted_domains:
        redirects_parts.append(f"\n📍 {domain} ({len(items)}):")
        for item in items:
            redirects_parts.append(f"  {item}")
    
    redirects_text = "\n".join(redirects_parts)

return problems_text, oks200_text, redirects_text

# ============== Handlers ==============
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
welcome_msg = (
    "🤖 <b>HTTP Status Checker Bot</b>\n\n"
    "Можливості:\n"
    "• Масове додавання URL (одне повідомлення)\n"
    "• Перевірка статусів сайтів\n"
    "• Групування редіректів по доменах\n\n"
    "Використовуй кнопки нижче для роботи."
)
await update.message.reply_text(welcome_msg, reply_markup=KB, parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
txt = (update.message.text or "").strip()
cid = update.effective_chat.id

if txt == "➕ Додати URL":
    await update.message.reply_text(
        "📝 Надішли URL для перевірки.\n\n"
        "Можеш надіслати:\n"
        "• Один URL\n"
        "• Кілька URL (кожен з нового рядка)\n"
        "• Домени без http:// (додам автоматично)\n\n"
        "Приклад:\n"
        "example.com\n"
        "https://test.com\n"
        "another-site.org"
    )
    return WAIT_URL

if txt == "🚀 Запустити перевірку":
    await run_check_and_reply(context, [cid])
    return ConversationHandler.END

if txt == "📋 Список URL":
    urls = load_urls_from_file()
    if urls:
        body = f"📋 <b>Список URL ({len(urls)}):</b>\n\n"
        body += "\n".join(f"{i+1}. <code>{u}</code>" for i, u in enumerate(urls))
        for ch in chunk_text(body, TG_LIMIT) or ["—"]:
            await update.message.reply_text(ch, parse_mode="HTML")
    else:
        await update.message.reply_text("📝 Список порожній. Додай URL через кнопку «➕ Додати URL».")
    return ConversationHandler.END

if txt == "🗑 Очистити список":
    clear_urls_file()
    await update.message.reply_text("🗑 Список URL очищено.", reply_markup=KB)
    return ConversationHandler.END

return ConversationHandler.END

async def save_url_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
text = (update.message.text or "").strip()
if not text:
    await update.message.reply_text("⚠️ Порожній рядок. Спробуй ще раз.", reply_markup=KB)
    return ConversationHandler.END

# Парсимо всі URL з повідомлення
candidates = clean_lines(text)
if not candidates:
    await update.message.reply_text("⚠️ Не знайдено валідних URL.", reply_markup=KB)
    return ConversationHandler.END

added, skipped = append_urls_to_file(candidates)

response_parts = []
if added:
    response_parts.append(f"✅ <b>Додано {len(added)} URL:</b>")
    for url in added[:10]:  # показуємо перші 10
        response_parts.append(f"  • <code>{url}</code>")
    if len(added) > 10:
        response_parts.append(f"  ... та ще {len(added) - 10}")

if skipped:
    response_parts.append(f"\nℹ️ Пропущено (вже є): {len(skipped)}")

await update.message.reply_text("\n".join(response_parts), reply_markup=KB, parse_mode="HTML")
return ConversationHandler.END

async def run_check_and_reply(context: ContextTypes.DEFAULT_TYPE, chat_ids: List[int]):
urls = load_urls_from_file()
if not urls:
    for cid in chat_ids:
        await context.bot.send_message(
            cid, 
            "📝 Список URL порожній. Натисни «➕ Додати URL».", 
            reply_markup=KB
        )
    return

for cid in chat_ids:
    await context.bot.send_message(cid, f"🔄 Перевіряю {len(urls)} URL...")

pairs = await check_many(urls)
msg1, msg2, msg3 = render_three(pairs)

for cid in chat_ids:
    # Відправляємо три блоки
    for section in (msg1 or "—", msg2 or "—", msg3 or "—"):
        for ch in chunk_text(section, TG_LIMIT) or ["—"]:
            await context.bot.send_message(cid, ch)
    
    await context.bot.send_message(cid, "✅ Перевірка завершена!", reply_markup=KB)

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
# Разова перевірка списку з повідомлення
args_text = " ".join(context.args) if context.args else ""
payload = args_text.strip() or (update.message.text or "").replace("/check", "", 1).strip()

if payload:
    candidates = clean_lines(payload)
    if not candidates:
        await update.message.reply_text("⚠️ Не знайдено URL у повідомленні.")
        return
    
    await update.message.reply_text(f"🔄 Перевіряю {len(candidates)} URL...")
    pairs = await check_many(candidates)
    msg1, msg2, msg3 = render_three(pairs)
    
    for sec in (msg1 or "—", msg2 or "—", msg3 or "—"):
        for ch in chunk_text(sec, TG_LIMIT) or ["—"]:
            await update.message.reply_text(ch)
    
    await update.message.reply_text("✅ Перевірка завершена!", reply_markup=KB)
    return

# Якщо без тексту — беремо список із файлу
await update.message.reply_text("🔄 Перевіряю список із файлу...")
await run_check_and_reply(context, [update.effective_chat.id])

async def text_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
# Якщо користувач надіслав просто список — перевіримо разово
text = (update.message.text or "").strip()
candidates = clean_lines(text)

if not candidates:
    return

await update.message.reply_text(f"🔄 Перевіряю {len(candidates)} URL...")
pairs = await check_many(candidates)
msg1, msg2, msg3 = render_three(pairs)

for sec in (msg1 or "—", msg2 or "—", msg3 or "—"):
    for ch in chunk_text(sec, TG_LIMIT) or ["—"]:
        await update.message.reply_text(ch)

await update.message.reply_text("✅ Перевірка завершена!", reply_markup=KB)

# ============== Main ==============
def main():
load_dotenv()
token = _get_token(sys.argv)
if not token:
    raise RuntimeError("BOT_TOKEN не задано. Додай у .env або передай --token <TOKEN>")

app = ApplicationBuilder().token(token).build()

app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("check", cmd_check))

conv = ConversationHandler(
    entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler)],
    states={WAIT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_url_state)]},
    fallbacks=[],
)
app.add_handler(conv)

# fallback для прямого відправлення списків
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback))

log.info("✅ HTTP Status Checker bot started.")
port = int(os.environ.get("PORT", 10000))
app_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME") or "telegram-status-bot-zx0t.onrender.com"
webhook_url = f"https://{app_host}/{token}"

app.run_webhook(
    listen="0.0.0.0",
    port=port,
    url_path=token,
    webhook_url=webhook_url
)

if __name__ == "__main__":
main()
```