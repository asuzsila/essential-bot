import asyncio
import html
import json
import logging
import os
import random
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiohttp import web  # Render uchun mini HTTP-server
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise SystemExit(".env faylida BOT_TOKEN yo'q!")

# DATA[book][unit] = [{"en", "uz", "tr"?, "pos"?, "ex"?}, ...]
DATA = json.loads((Path(__file__).parent / "units.json").read_text(encoding="utf-8"))
if not DATA:
    raise SystemExit("units.json bo'sh! `python build_json.py` ni ishga tushiring.")

dp = Dispatcher()
SESSIONS = {}  # user_id -> joriy quiz holati (xotirada)
LETTERS = ["A", "B", "C", "D"]


# ---------- yordamchi funksiyalar ----------
def e(text):
    return html.escape(str(text))


def book_pool(book):
    return [w for unit in DATA[book].values() for w in unit]


def unit_words(book, unit):
    return book_pool(book) if unit == "all" else DATA[book][unit]


def unit_title(book, unit):
    return f"{book}-kitob, " + ("hamma mavzular" if unit == "all" else f"Unit {unit}")


def word_line(w):
    """so'z + transkripsiya + turkum"""
    line = e(w["en"])
    if w.get("tr"):
        line += f" {e(w['tr'])}"
    if w.get("pos"):
        line += f" ({e(w['pos'])})"
    return line


def books_kb():
    kb = InlineKeyboardBuilder()
    for b in DATA:
        kb.button(text=f"📘 {b}-kitob", callback_data=f"b:{b}")
    kb.adjust(2)
    return kb.as_markup()


def units_kb(book):
    kb = InlineKeyboardBuilder()
    for u in DATA[book]:
        kb.button(text=f"Unit {u}", callback_data=f"u:{book}:{u}")
    kb.adjust(5)
    kb.row(InlineKeyboardButton(text="🎲 Hamma mavzular aralash", callback_data=f"u:{book}:all"))
    kb.row(InlineKeyboardButton(text="⬅️ Kitoblar", callback_data="books"))
    return kb.as_markup()


def start_kb(book, unit):
    kb = InlineKeyboardBuilder()
    kb.button(text="▶️ Boshlash (EN → UZ)", callback_data=f"s:{book}:{unit}:e")
    kb.button(text="🔁 Boshlash (UZ → EN)", callback_data=f"s:{book}:{unit}:u")
    kb.button(text="📖 So'zlar ro'yxati", callback_data=f"l:{book}:{unit}")
    kb.button(text="⬅️ Mavzular", callback_data=f"b:{book}")
    kb.adjust(1)
    return kb.as_markup()


# ---------- quiz mantiqi ----------
def new_session(book, unit, mode, items=None):
    items = list(items if items is not None else unit_words(book, unit))
    random.shuffle(items)
    return {
        "book": book, "unit": unit, "mode": mode, "items": items,
        "i": 0, "score": 0, "wrong": [], "answered": False,
        "options": [], "correct": 0, "question": None,
    }


def build_question(s):
    item = s["items"][s["i"]]
    q_key, a_key = ("en", "uz") if s["mode"] == "e" else ("uz", "en")
    correct = item[a_key]
    pool_items = [w for w in book_pool(s["book"]) if w[a_key] != correct]
    # imkon bo'lsa, xuddi shu so'z turkumidan chalg'ituvchi variantlar tanlaymiz
    if item.get("pos"):
        same = [w for w in pool_items if w.get("pos") == item["pos"]]
        if len({w[a_key] for w in same}) >= 3:
            pool_items = same
    pool = sorted({w[a_key] for w in pool_items})
    options = random.sample(pool, min(3, len(pool))) + [correct]
    random.shuffle(options)
    s["options"] = options
    s["correct"] = options.index(correct)
    s["answered"] = False
    s["question"] = word_line(item) if s["mode"] == "e" else e(item["uz"])


def question_text(s, chosen=None):
    n = len(s["items"])
    lines = [f"❓ <b>{s['i'] + 1}/{n}</b>  ✅ {s['score']}", "", f"<b>{s['question']}</b>", ""]
    for idx, opt in enumerate(s["options"]):
        mark = ""
        if chosen is not None:
            if idx == s["correct"]:
                mark = " ✅"
            elif idx == chosen:
                mark = " ❌"
        lines.append(f"{LETTERS[idx]}) {e(opt)}{mark}")
    if chosen is not None:
        item = s["items"][s["i"]]
        if s["mode"] == "u":
            lines += ["", f"📌 {word_line(item)}"]
        if item.get("ex"):
            lines += ["", f"💬 <i>{e(item['ex'])}</i>"]
    return "\n".join(lines)


def answers_kb():
    kb = InlineKeyboardBuilder()
    for idx, letter in enumerate(LETTERS):
        kb.button(text=letter, callback_data=f"a:{idx}")
    kb.adjust(4)
    kb.row(InlineKeyboardButton(text="⏹ To'xtatish", callback_data="stop"))
    return kb.as_markup()


async def show_question(cb: CallbackQuery, s):
    build_question(s)
    await cb.message.edit_text(question_text(s), reply_markup=answers_kb())


async def show_result(cb: CallbackQuery, s):
    n = len(s["items"])
    pct = round(100 * s["score"] / n) if n else 0
    emoji = "🏆" if pct >= 90 else "👍" if pct >= 60 else "💪"
    text = f"{emoji} <b>Natija: {s['score']}/{n}</b> ({pct}%)\n{unit_title(s['book'], s['unit'])}\n"
    if s["wrong"]:
        text += "\n<b>Xatolar:</b>\n"
        for w in s["wrong"]:
            line = f"• {e(w['en'])} — {e(w['uz'])}\n"
            if len(text) + len(line) > 3800:
                text += "…\n"
                break
            text += line
    kb = InlineKeyboardBuilder()
    if s["wrong"]:
        kb.button(text="🔥 Xatolarni takrorlash", callback_data="retry")
    kb.button(text="🔄 Qayta boshlash", callback_data=f"s:{s['book']}:{s['unit']}:{s['mode']}")
    kb.button(text="⬅️ Mavzular", callback_data=f"b:{s['book']}")
    kb.adjust(1)
    if not s["wrong"]:
        SESSIONS.pop(cb.from_user.id, None)
    await cb.message.edit_text(text, reply_markup=kb.as_markup())


# ---------- handlerlar ----------
@dp.message(Command("start", "menu"))
async def cmd_start(m: Message):
    SESSIONS.pop(m.from_user.id, None)
    await m.answer(
        "👋 Salom! Bu <b>4000 Essential English Words</b> so'zlarini quiz orqali yodlash boti.\n\n"
        "Kitobni tanlang:",
        reply_markup=books_kb(),
    )


@dp.callback_query(F.data == "books")
async def cb_books(cb: CallbackQuery):
    await cb.message.edit_text("Kitobni tanlang:", reply_markup=books_kb())
    await cb.answer()


@dp.callback_query(F.data.startswith("b:"))
async def cb_book(cb: CallbackQuery):
    book = cb.data.split(":")[1]
    SESSIONS.pop(cb.from_user.id, None)
    await cb.message.edit_text(f"📘 <b>{book}-kitob</b>\nMavzuni tanlang:", reply_markup=units_kb(book))
    await cb.answer()


@dp.callback_query(F.data.startswith("u:"))
async def cb_unit(cb: CallbackQuery):
    _, book, unit = cb.data.split(":")
    n = len(unit_words(book, unit))
    await cb.message.edit_text(
        f"📘 <b>{unit_title(book, unit)}</b>\nSo'zlar soni: {n}\n\nQuizni boshlaymizmi?",
        reply_markup=start_kb(book, unit),
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("l:"))
async def cb_list(cb: CallbackQuery):
    _, book, unit = cb.data.split(":")
    text = f"📖 <b>{unit_title(book, unit)}</b>\n\n"
    for w in unit_words(book, unit):
        line = f"{word_line(w)} — {e(w['uz'])}\n"
        if len(text) + len(line) > 3900:
            text += "…"
            break
        text += line
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Orqaga", callback_data=f"u:{book}:{unit}")
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("s:"))
async def cb_start_quiz(cb: CallbackQuery):
    _, book, unit, mode = cb.data.split(":")
    s = new_session(book, unit, mode)
    SESSIONS[cb.from_user.id] = s
    await show_question(cb, s)
    await cb.answer()


@dp.callback_query(F.data == "retry")
async def cb_retry(cb: CallbackQuery):
    old = SESSIONS.get(cb.from_user.id)
    if not old or not old["wrong"]:
        return await cb.answer("Xatolar yo'q.", show_alert=True)
    s = new_session(old["book"], old["unit"], old["mode"], items=old["wrong"])
    SESSIONS[cb.from_user.id] = s
    await show_question(cb, s)
    await cb.answer()


@dp.callback_query(F.data.startswith("a:"))
async def cb_answer(cb: CallbackQuery):
    s = SESSIONS.get(cb.from_user.id)
    if not s or not s["question"]:
        return await cb.answer("Quiz topilmadi. /start bosing.", show_alert=True)
    if s["answered"]:
        return await cb.answer()
    s["answered"] = True
    chosen = int(cb.data.split(":")[1])
    if chosen == s["correct"]:
        s["score"] += 1
    else:
        s["wrong"].append(s["items"][s["i"]])
    last = s["i"] + 1 >= len(s["items"])
    kb = InlineKeyboardBuilder()
    kb.button(text="🏁 Natija" if last else "➡️ Keyingisi", callback_data="next")
    await cb.message.edit_text(question_text(s, chosen), reply_markup=kb.as_markup())
    await cb.answer("✅ To'g'ri!" if chosen == s["correct"] else "❌ Noto'g'ri")


@dp.callback_query(F.data == "next")
async def cb_next(cb: CallbackQuery):
    s = SESSIONS.get(cb.from_user.id)
    if not s:
        return await cb.answer("Quiz topilmadi. /start bosing.", show_alert=True)
    s["i"] += 1
    if s["i"] >= len(s["items"]):
        await show_result(cb, s)
    else:
        await show_question(cb, s)
    await cb.answer()


@dp.callback_query(F.data == "stop")
async def cb_stop(cb: CallbackQuery):
    s = SESSIONS.pop(cb.from_user.id, None)
    await cb.message.edit_text(
        "⏹ Quiz to'xtatildi.",
        reply_markup=units_kb(s["book"]) if s else books_kb(),
    )
    await cb.answer()


# ---------- Render uchun mini HTTP-server ----------
async def handle_health(request):
    # UptimeRobot va Render shu manzilga murojaat qiladi, javob: 200 OK
    return web.Response(text="OK")


async def start_server():
    app = web.Application()
    app.router.add_get("/", handle_health)         # GET va HEAD ni ham qabul qiladi
    app.router.add_get("/healthz", handle_health)  # Render health check uchun
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))         # Render PORT ni o'zi beradi
    site = web.TCPSite(runner, "0.0.0.0", port)    # 0.0.0.0 bo'lishi shart
    await site.start()                             # serverni fonda ishga tushiradi
    logging.info("HTTP server %s-portda ishlayapti", port)
    return runner


async def main():
    logging.basicConfig(level=logging.INFO)
    runner = await start_server()                  # 1) avval HTTP-server
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await dp.start_polling(bot)                # 2) keyin bot (to'xtamay ishlaydi)
    finally:
        await bot.session.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
