import asyncio
import os
import logging
import sys
import traceback
import base64
import re
import time
import random
import html as html_lib
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, BufferedInputFile,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.chat_action import ChatActionSender

from openai import OpenAI
import requests as http_requests

from config import (
    TELEGRAM_BOT_TOKEN, ONLYSQ_API_KEY, ONLYSQ_BASE_URL,
    DEFAULT_MODEL, VISION_MODEL,
    AVAILABLE_MODELS, VISION_CAPABLE_MODELS, ADMIN_IDS,
    MODEL_CATEGORIES, IMAGE_MODELS,
)
from database import (
    init_db, update_user_activity, get_user_stats, save_message,
    get_chat_history, clear_chat_history, get_user_model, set_user_model,
    get_user_mode, set_user_mode, register_chat_activity, get_global_stats,
    get_all_chat_ids, get_chats_by_type,
    get_user_image_model, set_user_image_model, increment_counter,
    get_top_users, is_user_registered, register_user_with_lang,
    get_user_language, set_user_language,
    get_history_limit, set_history_limit,
    get_required_channels, add_required_channel, remove_required_channel,
    get_setting, set_setting,
    ban_user, unban_user, is_user_banned,
)
from system_prompt import get_system_prompt, MODES
from utils import extract_text_for_rag, scrape_web_page, find_urls
from lang import L, L_list

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# Premium Emoji
# ═══════════════════════════════════════════

_PE = {
    "settings":  ("⚙️", "5870982283724328568"),
    "profile":   ("👤", "5870994129244131212"),
    "people":    ("👥", "5870772616305839506"),
    "verified":  ("👤", "5891207662678317861"),
    "file":      ("📁", "5870528606328852614"),
    "smile":     ("🙂", "5870764288364252592"),
    "growth":    ("📊", "5870930636742595124"),
    "stats":     ("📊", "5870921681735781843"),
    "lock":      ("🔒", "6037249452824072506"),
    "horn":      ("📣", "6039422865189638057"),
    "check":     ("✅", "5870633910337015697"),
    "cross":     ("❌", "5870657884844462243"),
    "pencil":    ("🖋", "5870676941614354370"),
    "trash":     ("🗑", "5870875489362513438"),
    "info":      ("ℹ️", "6028435952299413210"),
    "bot":       ("🤖", "6030400221232501136"),
    "eye":       ("👁", "6037397706505195857"),
    "hidden":    ("👁", "6037243349675544634"),
    "bell":      ("🔔", "6039486778597970865"),
    "gift":      ("🎁", "6032644646587338669"),
    "clock":     ("⏰", "5983150113483134607"),
    "party":     ("🎉", "6041731551845159060"),
    "brush":     ("🖌", "6050679691004612757"),
    "media":     ("🖼", "6035128606563241721"),
    "wallet":    ("👛", "5769126056262898415"),
    "money":     ("🪙", "5904462880941545555"),
    "code":      ("🔨", "5940433880585605708"),
    "loading":   ("🔄", "5345906554510012647"),
    "write":     ("✍", "5870753782874246579"),
    "calendar":  ("📅", "5890937706803894250"),
    "download":  ("⬇", "6039802767931871481"),
    "link":      ("🔗", "5769289093221454192"),
    "tag":       ("🏷", "5886285355279193209"),
    "time":      ("🕓", "5775896410780079073"),
    "geo":       ("📍", "6042011682497106307"),
    "send":      ("⬆", "5963103826075456248"),
}


def pe(key: str) -> str:
    emoji, eid = _PE.get(key, ("•", ""))
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{emoji}</tg-emoji>'
    return emoji


def peid(key: str) -> str | None:
    val = _PE.get(key, ("", ""))[1]
    return val if val else None


# ═══════════════════════════════════════════
# Init
# ═══════════════════════════════════════════

ai_client = None
if ONLYSQ_API_KEY:
    try:
        ai_client = OpenAI(
            api_key=ONLYSQ_API_KEY,
            base_url=ONLYSQ_BASE_URL,
            max_retries=0,
        )
        logger.info("AI client initialized (no retries)")
    except Exception as e:
        logger.error("Failed to initialize AI client: %s", e)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

_bot_info = None
_start_time = time.time()

_API_URL = f"{ONLYSQ_BASE_URL.rstrip('/')}/chat/completions"
_API_HEADERS = {
    "Authorization": f"Bearer {ONLYSQ_API_KEY}",
    "Content-Type": "application/json",
} if ONLYSQ_API_KEY else {}


async def get_bot_info():
    global _bot_info
    if _bot_info is None:
        _bot_info = await bot.get_me()
    return _bot_info


# ═══════════════════════════════════════════
# FSM States
# ═══════════════════════════════════════════

class AdminStates(StatesGroup):
    waiting_for_broadcast_content = State()
    waiting_for_specific_chat_id = State()
    waiting_for_specific_broadcast_content = State()
    waiting_for_user_lookup_id = State()
    waiting_for_channel_add = State()
    waiting_for_history_limit = State()


# ═══════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════

async def track_chat(message: Message):
    try:
        title = message.chat.title or message.chat.full_name or "Unknown"
        await register_chat_activity(message.chat.id, message.chat.type, title)
    except Exception as e:
        logger.error("Tracking error: %s", e)


async def _should_respond(message: Message, text_to_check: str = "") -> bool:
    if message.chat.type == "private":
        return True
    bi = await get_bot_info()
    if message.reply_to_message and message.reply_to_message.from_user \
       and message.reply_to_message.from_user.id == bi.id:
        return True
    pattern = rf"(@{re.escape(bi.username or '')}|\bгеникс\b|\bgenix\b|\bгеня\b)"
    return bool(re.search(pattern, text_to_check, re.IGNORECASE))


def _strip_mention(text: str, username: str) -> str:
    pattern = rf"(@{re.escape(username)}|\bгеникс\b|\bгеня\b|\bgenix\b)"
    return re.sub(pattern, '', text, flags=re.IGNORECASE).strip()


def _uptime() -> str:
    s = int(time.time() - _start_time)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _greeting(lang: str = "ru") -> str:
    hour = datetime.now().hour
    if 6 <= hour < 12:
        return L("greeting_morning", lang)
    elif 12 <= hour < 18:
        return L("greeting_afternoon", lang)
    elif 18 <= hour < 23:
        return L("greeting_evening", lang)
    return L("greeting_night", lang)


def esc(text: str) -> str:
    return html_lib.escape(str(text))


def _chat_api_call(messages: list, temperature: float = 0.7,
                   max_tokens: int = 200, timeout: int = 15) -> str | None:
    resp = http_requests.post(_API_URL, headers=_API_HEADERS, json={
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }, timeout=timeout)
    if resp.status_code == 200:
        return resp.json()["choices"][0]["message"]["content"].strip()
    logger.warning("API call failed: %s %s", resp.status_code, resp.text[:200])
    return None


def _mode_label(mode: str, lang: str) -> str:
    key = f"mode_{mode}"
    return L(key, lang) if key in L.__code__.co_varnames else L(key, lang)


# ═══════════════════════════════════════════
# AI Engine
# ═══════════════════════════════════════════

async def get_ai_response(text: str, user_id: int, image_b64: str = None, is_group: bool = False):
    lang = await get_user_language(user_id)
    system_instruction = await get_system_prompt(user_id, is_group)
    history = await get_chat_history(user_id, limit=await get_history_limit())

    if not ai_client:
        return L("ai_no_brain", lang)

    user_model = await get_user_model(user_id)
    model_name = user_model if (not image_b64 or user_model in VISION_CAPABLE_MODELS) else VISION_MODEL

    try:
        messages = [{"role": "system", "content": system_instruction}]
        for h in history:
            messages.append({"role": "assistant" if h["role"] == "assistant" else "user",
                             "content": h["content"]})

        if image_b64:
            content = [
                {"type": "text", "text": text or ("What's in this photo?" if lang == "en" else "Что на этом фото?")},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]
        else:
            content = text

        messages.append({"role": "user", "content": content})

        def call_ai():
            return ai_client.chat.completions.create(
                model=model_name, messages=messages,
                temperature=0.7, max_tokens=2048, timeout=120,
            ).choices[0].message.content

        result = await asyncio.to_thread(call_ai)
        return result or L("ai_silent", lang)

    except Exception:
        logger.error("AI ERROR (model=%s): %s", user_model, traceback.format_exc())
        err_model = f"Error with model {user_model}" if lang == "en" else f"Ошибка при обращении к модели {user_model}"
        return f"{err_model}.\n\n{L('ai_error_hint', lang)}"


async def enhance_prompt(user_prompt: str) -> str:
    if not ONLYSQ_API_KEY:
        return user_prompt

    try:
        result = await asyncio.to_thread(
            _chat_api_call,
            [
                {"role": "system", "content":
                 "You are a prompt engineer for image generation. "
                 "Translate and enhance the user's prompt into detailed English. "
                 "Focus on: subject, environment, lighting, style. "
                 "Output ONLY the prompt text."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7, max_tokens=200, timeout=15,
        )
        if result:
            logger.info("Enhanced: '%s' -> '%s'", user_prompt, result)
            return result
    except Exception as e:
        logger.warning("Enhancement failed: %s", e)

    try:
        result = await asyncio.to_thread(
            _chat_api_call,
            [
                {"role": "system", "content": "Translate to English. Output ONLY the translation."},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3, max_tokens=150, timeout=10,
        )
        if result:
            logger.info("Translated: '%s' -> '%s'", user_prompt, result)
            return result
    except Exception as e:
        logger.warning("Translation failed: %s", e)

    return user_prompt


async def transcribe_voice(file_path: str) -> str | None:
    if not ONLYSQ_API_KEY:
        return None

    url = f"{ONLYSQ_BASE_URL.rstrip('/')}/audio/transcriptions"
    auth_header = {"Authorization": f"Bearer {ONLYSQ_API_KEY}"}

    try:
        def try_multipart():
            with open(file_path, "rb") as f:
                resp = http_requests.post(
                    url, headers=auth_header,
                    files={"file": ("voice.ogg", f, "audio/ogg")},
                    data={"model": "whisper-1"},
                    timeout=30,
                )
            logger.info("Whisper multipart: %s", resp.status_code)
            if resp.status_code == 200:
                return resp.json().get("text", "")
            return None
        text = await asyncio.to_thread(try_multipart)
        if text:
            return text
    except Exception as e:
        logger.warning("Whisper multipart failed: %s", e)

    try:
        def try_json():
            with open(file_path, "rb") as f:
                audio_b64 = base64.b64encode(f.read()).decode()
            resp = http_requests.post(
                url,
                headers={**auth_header, "Content-Type": "application/json"},
                json={"model": "whisper-1", "file": f"data:audio/ogg;base64,{audio_b64}"},
                timeout=30,
            )
            logger.info("Whisper JSON: %s", resp.status_code)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("text", data.get("transcription", ""))
            return None
        text = await asyncio.to_thread(try_json)
        if text:
            return text
    except Exception as e:
        logger.warning("Whisper JSON failed: %s", e)

    if ai_client:
        try:
            def try_client():
                with open(file_path, "rb") as f:
                    return ai_client.audio.transcriptions.create(
                        model="whisper-1", file=f,
                    ).text
            text = await asyncio.to_thread(try_client)
            if text:
                return text
        except Exception as e:
            logger.warning("Whisper client failed: %s", e)

    return None


async def get_conversation_summary(user_id: int) -> str:
    lang = await get_user_language(user_id)
    if not ONLYSQ_API_KEY:
        return L("api_not_connected", lang)
    history = await get_chat_history(user_id, limit=30)
    if not history:
        return L("summary_empty", lang)
    conversation = "\n".join(f"{h['role']}: {h['content'][:200]}" for h in history[-20:])
    try:
        system_msg = L_list("summary_system", lang) if isinstance(L_list("summary_system", lang), str) else L("summary_system", lang)
        result = await asyncio.to_thread(
            _chat_api_call,
            [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": conversation},
            ],
            temperature=0.5, max_tokens=500, timeout=30,
        )
        return result or L("summary_fail", lang)
    except Exception as e:
        logger.error("Summary error: %s", e)
        return L("summary_fail", lang)


# ═══════════════════════════════════════════
# Response Sender
# ═══════════════════════════════════════════

async def send_response(message: Message, text: str, lang: str = "ru"):
    if len(text) > 8000:
        try:
            file = BufferedInputFile(text.encode("utf-8"), filename="response.txt")
            await message.reply_document(file, caption=L("file_too_long", lang))
            return
        except Exception as e:
            logger.error("Failed to send file: %s", e)

    MAX_LEN = 3500
    parts = [text] if len(text) <= MAX_LEN else []
    if not parts:
        remaining = text
        while remaining:
            if len(remaining) <= MAX_LEN:
                parts.append(remaining)
                break
            idx = remaining.rfind("\n", 0, MAX_LEN)
            if idx == -1:
                idx = MAX_LEN
            parts.append(remaining[:idx].strip())
            remaining = remaining[idx:].strip()

    for part in parts:
        if not part:
            continue
        try:
            await message.reply(part, parse_mode="Markdown")
        except Exception:
            try:
                await message.reply(part.replace("*", "").replace("_", "").replace("`", ""))
            except Exception as e2:
                logger.error("Send failed: %s", e2)


# ═══════════════════════════════════════════
# LANGUAGE SELECTION (first use)
# ═══════════════════════════════════════════

def _lang_select_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Русский", callback_data="lang_set:ru", icon_custom_emoji_id=peid("geo")),
            InlineKeyboardButton(text="English", callback_data="lang_set:en", icon_custom_emoji_id=peid("link")),
        ],
    ])


# ═══════════════════════════════════════════
# CHANNEL SUBSCRIPTION CHECK
# ═══════════════════════════════════════════

async def check_subscriptions(user_id: int) -> list[dict]:
    channels = await get_required_channels()
    if not channels:
        return []
    missing = []
    for ch in channels:
        try:
            ch_id = ch.get("channel_id", ch.get("username", ""))
            lookup = ch_id if ch_id.lstrip("-").isdigit() else f"@{ch_id.lstrip('@')}"
            member = await bot.get_chat_member(lookup, user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return missing


async def _send_sub_wall(target, user_id: int, lang: str, edit: bool = False):
    channels = await get_required_channels()
    missing = await check_subscriptions(user_id)
    if not missing:
        return False

    lines = [
        f'{pe("bell")} <b>{L("sub_required_title", lang)}</b>\n',
        f'{L("sub_required_text", lang)}\n',
    ]
    kb = []
    for ch in channels:
        is_missing = ch in missing
        status = L("sub_not_subbed", lang) if is_missing else L("sub_subbed", lang)
        icon = "cross" if is_missing else "check"
        ch_id = ch.get("channel_id", ch.get("username", ""))
        invite = ch.get("invite_link", "")
        if invite:
            url = invite
        elif ch_id.lstrip("-").isdigit():
            url = invite or f"https://t.me/c/{ch_id.lstrip('-')}"
        else:
            uname = ch_id.lstrip("@")
            url = f"https://t.me/{uname}"
        lines.append(f'{pe(icon)} <b>{esc(ch["name"])}</b> — {status}')
        kb.append([InlineKeyboardButton(
            text=ch["name"], url=url,
            icon_custom_emoji_id=peid("horn"),
        )])
    kb.append([InlineKeyboardButton(
        text=L("btn_check_sub", lang), callback_data="check_sub",
        icon_custom_emoji_id=peid("loading"),
    )])

    text = "\n".join(lines)
    markup = InlineKeyboardMarkup(inline_keyboard=kb)

    if edit:
        try:
            await target.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(chat_id=target.chat.id, text=text,
                                   reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        await bot.send_message(chat_id=target.chat.id if hasattr(target, 'chat') else target,
                               text=text, reply_markup=markup, parse_mode=ParseMode.HTML)
    return True


async def _check_ban(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return False
    return await is_user_banned(user_id)


async def _enforce_sub(message_or_callback, user_id: int) -> bool:
    channels = await get_required_channels()
    if not channels:
        return False
    if user_id in ADMIN_IDS:
        return False
    chat = getattr(message_or_callback, 'chat', None)
    if chat and chat.type != "private":
        return False
    lang = await get_user_language(user_id)
    missing = await check_subscriptions(user_id)
    if not missing:
        return False
    await _send_sub_wall(message_or_callback, user_id, lang)
    return True


async def _enforce_sub_cb(callback: CallbackQuery) -> bool:
    channels = await get_required_channels()
    if not channels:
        return False
    uid = callback.from_user.id
    if uid in ADMIN_IDS:
        return False
    if callback.message and callback.message.chat.type != "private":
        return False
    missing = await check_subscriptions(uid)
    if not missing:
        return False
    lang = await get_user_language(uid)
    await callback.answer(L("sub_still_missing", lang), show_alert=True)
    return True


@dp.callback_query(F.data == "check_sub")
async def check_sub_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    lang = await get_user_language(uid)
    missing = await check_subscriptions(uid)

    if not missing:
        await callback.answer(L("sub_success", lang))
        name = callback.from_user.first_name or ("friend" if lang == "en" else "друг")
        model = await get_user_model(uid)
        mode = await get_user_mode(uid)
        try:
            await callback.message.edit_text(
                _start_text(name, model, mode, lang),
                reply_markup=_start_keyboard(lang),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            await callback.message.delete()
            await bot.send_message(
                chat_id=callback.message.chat.id,
                text=_start_text(name, model, mode, lang),
                reply_markup=_start_keyboard(lang),
                parse_mode=ParseMode.HTML,
            )
    else:
        await callback.answer(L("sub_still_missing", lang), show_alert=True)
        await _send_sub_wall(callback.message, uid, lang, edit=True)


# ═══════════════════════════════════════════
# ADMIN PANEL (bilingual RU/EN)
# ═══════════════════════════════════════════

def _admin_text(stats: dict, lang: str = "ru") -> str:
    return (
        f'{pe("lock")} <b>{L("admin_title", lang)}</b>\n\n'
        f'{pe("people")} {L("admin_users", lang)}: <code>{stats["total_users_db"]}</code>\n'
        f'{pe("stats")} {L("admin_chats", lang)}: <code>{stats["total_chats_known"]}</code>  '
        f'({L("admin_private", lang)}: {stats["private_chats"]} · {L("admin_groups", lang)}: {stats["group_chats"]})\n'
        f'{pe("send")} {L("admin_24h", lang)}: <code>{stats["msgs_24h"]}</code> {L("admin_msgs", lang)}\n'
        f'{pe("growth")} {L("admin_total", lang)}: <code>{stats["total_msgs"]}</code> {L("admin_msgs", lang)}\n'
        f'{pe("brush")} {L("admin_images", lang)}: <code>{stats["total_images"]}</code>\n'
    )


def _admin_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=L("admin_broadcast", lang), callback_data="admin_broadcast_select", icon_custom_emoji_id=peid("horn")),
            InlineKeyboardButton(text=L("admin_write_id", lang), callback_data="admin_direct_start", icon_custom_emoji_id=peid("send")),
        ],
        [
            InlineKeyboardButton(text=L("admin_top_users", lang), callback_data="admin_top_users", icon_custom_emoji_id=peid("growth")),
            InlineKeyboardButton(text=L("admin_find_user", lang), callback_data="admin_user_lookup", icon_custom_emoji_id=peid("eye")),
        ],
        [
            InlineKeyboardButton(text=L("admin_channels", lang), callback_data="admin_channels", icon_custom_emoji_id=peid("horn")),
            InlineKeyboardButton(text=L("admin_settings", lang), callback_data="admin_settings", icon_custom_emoji_id=peid("settings")),
        ],
        [InlineKeyboardButton(text=L("admin_refresh", lang), callback_data="admin_refresh", icon_custom_emoji_id=peid("loading"))],
    ])


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(message.from_user.id)
    stats = await get_global_stats()
    await message.answer(_admin_text(stats, lang), reply_markup=_admin_keyboard(lang), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "admin_refresh")
async def admin_refresh(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    stats = await get_global_stats()
    try:
        await callback.message.edit_text(_admin_text(stats, lang), reply_markup=_admin_keyboard(lang), parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer(L("admin_updated", lang))


@dp.callback_query(F.data == "admin_top_users")
async def admin_top_users(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    users = await get_top_users(10)
    if not users:
        await callback.answer(L("admin_no_data", lang), show_alert=True)
        return

    lines = [f'{pe("growth")} <b>{L("admin_top", lang)}</b>\n']
    for i, u in enumerate(users):
        lines.append(f'{i+1}. <code>{u["user_id"]}</code> — {u["total_messages"]} {L("admin_msgs", lang)}')

    kb = [[InlineKeyboardButton(text=L("admin_back", lang), callback_data="admin_refresh", icon_custom_emoji_id=peid("settings"))]]
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "admin_user_lookup")
async def admin_user_lookup(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    await callback.message.edit_text(
        f'{pe("eye")} <b>{L("admin_enter_id", lang)}</b>\n\n{L("admin_cancel", lang)}',
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(AdminStates.waiting_for_user_lookup_id)


@dp.message(AdminStates.waiting_for_user_lookup_id)
async def admin_user_lookup_exec(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(message.from_user.id)
    if message.text and message.text.strip() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer(L("admin_cancelled", lang))
        return
    try:
        uid = int(message.text)
    except ValueError:
        await message.answer(L("admin_enter_num_id", lang))
        return

    stats = await get_user_stats(uid)
    if not stats:
        await message.answer(f"{L('admin_user_label', lang)} <code>{uid}</code> — {L('admin_not_found', lang)}", parse_mode=ParseMode.HTML)
        await state.clear()
        return

    model = await get_user_model(uid)
    mode = await get_user_mode(uid)
    ulang = await get_user_language(uid)

    banned = await is_user_banned(uid)
    ban_status = f'\n{pe("cross")} <b>{L("admin_banned", lang)}</b>' if banned else ""

    text = (
        f'{pe("profile")} <b>{L("admin_user_label", lang)}</b> <code>{uid}</code>{ban_status}\n\n'
        f'{L("admin_mode_label", lang)}: <b>{L(f"mode_{mode}", lang)}</b>\n'
        f'{L("admin_model_label", lang)}: <code>{esc(model)}</code>\n'
        f'{L("admin_lang_label", lang)}: <code>{ulang}</code>\n'
        f'{L("admin_msgs_label", lang)}: <b>{stats["total_messages"]}</b> ({stats["avg_per_day"]}{L("admin_per_day", lang)})\n'
        f'{L("admin_images", lang)}: {stats["total_images"]} · Voice: {stats["total_voice"]} · PDF: {stats["total_pdfs"]}\n'
        f'{stats["days_with_bot"]} {L("admin_days_with_us", lang)} {esc(stats["first_use"])})\n'
        f'{L("admin_last_active", lang)}: {esc(stats["last_activity"])}'
    )
    kb = []
    if banned:
        kb.append([InlineKeyboardButton(text=L("admin_unban", lang), callback_data=f"admin_unban:{uid}", icon_custom_emoji_id=peid("check"))])
    else:
        kb.append([InlineKeyboardButton(text=L("admin_ban", lang), callback_data=f"admin_ban:{uid}", icon_custom_emoji_id=peid("cross"))])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)
    await state.clear()


@dp.callback_query(F.data.startswith("admin_ban:"))
async def admin_ban_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    uid = int(callback.data.split(":", 1)[1])
    done = await ban_user(uid)
    if done:
        await callback.answer(f"{L('admin_banned', lang)}: {uid}")
    else:
        await callback.answer(f"{uid} — {L('admin_already_banned', lang)}")


@dp.callback_query(F.data.startswith("admin_unban:"))
async def admin_unban_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    uid = int(callback.data.split(":", 1)[1])
    done = await unban_user(uid)
    if done:
        await callback.answer(f"{L('admin_unbanned', lang)}: {uid}")
    else:
        await callback.answer(f"{uid} — {L('admin_not_found', lang)}")


@dp.callback_query(F.data == "admin_broadcast_select")
async def admin_broadcast_select(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    kb = [
        [InlineKeyboardButton(text=L("admin_private_only", lang), callback_data="bc_type_private", icon_custom_emoji_id=peid("lock"))],
        [InlineKeyboardButton(text=L("admin_groups_only", lang), callback_data="bc_type_group", icon_custom_emoji_id=peid("people"))],
        [InlineKeyboardButton(text=L("admin_all", lang), callback_data="bc_type_all", icon_custom_emoji_id=peid("horn"))],
        [InlineKeyboardButton(text=L("admin_back", lang), callback_data="admin_refresh", icon_custom_emoji_id=peid("settings"))],
    ]
    await callback.message.edit_text(
        f'{pe("horn")} <b>{L("admin_where_send", lang)}</b>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("bc_type_"))
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    bc_type = callback.data.split("_", 2)[2]
    await state.update_data(bc_type=bc_type)
    label_map = {"all": L("admin_all", lang), "private": L("admin_private_only", lang)}
    label = label_map.get(bc_type, L("admin_groups_only", lang))
    await callback.message.edit_text(
        f'{pe("horn")} <b>{L("admin_broadcast_to", lang)} {label}</b>\n\n{L("admin_send_msg", lang)}\n{L("admin_cancel", lang)}',
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(AdminStates.waiting_for_broadcast_content)
    await callback.answer()


@dp.callback_query(F.data == "admin_direct_start")
async def admin_direct_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    await callback.message.edit_text(
        f'{pe("send")} <b>{L("admin_enter_id", lang)}</b>\n\n{L("admin_cancel", lang)}',
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(AdminStates.waiting_for_specific_chat_id)


@dp.message(AdminStates.waiting_for_specific_chat_id)
async def admin_direct_get_id(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(message.from_user.id)
    if message.text and message.text.strip() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer(L("admin_cancelled", lang))
        return
    try:
        target_id = int(message.text)
        await state.update_data(target_chat_id=target_id)
        await message.answer(f'{L("admin_target", lang)}: <code>{target_id}</code>\n{L("admin_send_msg", lang)}', parse_mode=ParseMode.HTML)
        await state.set_state(AdminStates.waiting_for_specific_broadcast_content)
    except ValueError:
        await message.answer(L("admin_enter_num_id", lang))


@dp.message(AdminStates.waiting_for_specific_broadcast_content)
async def admin_direct_exec(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(message.from_user.id)
    if message.text and message.text.strip() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer(L("admin_cancelled", lang))
        return
    data = await state.get_data()
    try:
        await message.copy_to(data['target_chat_id'])
        await message.answer(f'{pe("check")} {L("admin_sent", lang)}', parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.answer(f'{pe("cross")} {esc(str(e))}', parse_mode=ParseMode.HTML)
    await state.clear()


@dp.message(AdminStates.waiting_for_broadcast_content)
async def admin_broadcast_execute(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(message.from_user.id)
    if message.text and message.text.strip() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer(L("admin_cancelled", lang))
        return

    data = await state.get_data()
    bc_type = data.get('bc_type', 'all')
    chat_ids = await get_all_chat_ids() if bc_type == "all" else await get_chats_by_type(bc_type)

    status = await message.answer(f'{pe("loading")} {L("admin_broadcasting", lang)} 0/{len(chat_ids)}', parse_mode=ParseMode.HTML)
    success, blocked, errors = 0, 0, 0

    for i, cid in enumerate(chat_ids):
        try:
            await message.copy_to(cid)
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            if any(kw in str(e).lower() for kw in ("blocked", "kicked", "not found", "peer_id")):
                blocked += 1
            else:
                errors += 1
        if (i + 1) % 50 == 0:
            try:
                await status.edit_text(
                    f'{pe("loading")} {L("admin_broadcasting", lang)} {i+1}/{len(chat_ids)}',
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    await status.edit_text(
        f'{pe("check")} <b>{L("admin_bc_done", lang)}</b>\n\n'
        f'{L("admin_bc_success", lang)}: {success}\n{L("admin_bc_blocked", lang)}: {blocked}\n{L("admin_bc_errors", lang)}: {errors}',
        parse_mode=ParseMode.HTML,
    )
    await state.clear()


# ═══════════════════════════════════════════
# ADMIN — BOT SETTINGS (expanded)
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "admin_settings")
async def admin_settings_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    h_limit = await get_history_limit()
    def_model = await get_setting("default_model") or DEFAULT_MODEL
    def_img = await get_setting("default_image_model") or "gpt-image-1"
    def_mode = await get_setting("default_mode") or "useful"
    def_model_name = AVAILABLE_MODELS.get(def_model, def_model)
    def_img_name = IMAGE_MODELS.get(def_img, {}).get("name", def_img)
    mode_label = L(f"mode_{def_mode}", lang)

    text = (
        f'{pe("settings")} <b>{L("admin_bot_settings", lang)}</b>\n\n'
        f'{pe("file")} {L("admin_history_limit", lang)}: <code>{h_limit}</code> {L("admin_msgs", lang)}\n'
        f'{pe("bot")} {L("admin_default_model", lang)}: <code>{esc(def_model_name)}</code>\n'
        f'{pe("brush")} {L("admin_default_img", lang)}: <code>{esc(def_img_name)}</code>\n'
        f'{pe("smile")} {L("admin_default_mode", lang)}: <code>{esc(mode_label)}</code>\n'
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=L("admin_history_limit", lang), callback_data="admin_set_history",
                              icon_custom_emoji_id=peid("file"))],
        [InlineKeyboardButton(text=L("admin_default_model", lang), callback_data="admin_set_def_model",
                              icon_custom_emoji_id=peid("bot"))],
        [InlineKeyboardButton(text=L("admin_default_img", lang), callback_data="admin_set_def_img",
                              icon_custom_emoji_id=peid("brush"))],
        [InlineKeyboardButton(text=L("admin_default_mode", lang), callback_data="admin_set_def_mode",
                              icon_custom_emoji_id=peid("smile"))],
        [InlineKeyboardButton(text=L("admin_back", lang), callback_data="admin_refresh", icon_custom_emoji_id=peid("settings"))],
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "admin_set_history")
async def admin_set_history_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    await callback.message.edit_text(
        f'{pe("file")} <b>{L("admin_history_limit", lang)}</b>\n\n{L("admin_new_limit", lang)}\n{L("admin_cancel", lang)}',
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(AdminStates.waiting_for_history_limit)


@dp.message(AdminStates.waiting_for_history_limit)
async def admin_set_history_input(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(message.from_user.id)
    if message.text and message.text.strip() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer(L("admin_cancelled", lang))
        return
    try:
        val = int(message.text.strip())
        if val < 1 or val > 500:
            raise ValueError
    except (ValueError, TypeError):
        await message.reply(L("admin_limit_range", lang))
        return

    await set_history_limit(val)
    await state.clear()
    await message.reply(
        f'{pe("check")} {L("admin_limit_set", lang)} <code>{val}</code>',
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "admin_set_def_model")
async def admin_set_def_model_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    cur = await get_setting("default_model") or DEFAULT_MODEL
    kb = []
    for cat, cat_data in MODEL_CATEGORIES.items():
        cat_pe = cat_data.get("_pe_key", "bot")
        kb.append([InlineKeyboardButton(text=cat, callback_data=f"admin_mcat:{cat}", icon_custom_emoji_id=peid(cat_pe))])
    kb.append([InlineKeyboardButton(text=L("admin_back", lang), callback_data="admin_settings", icon_custom_emoji_id=peid("settings"))])
    await callback.message.edit_text(
        f'{pe("bot")} <b>{L("admin_choose_model", lang)}</b>\n\n{L("model_current", lang)}: <code>{esc(cur)}</code>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("admin_mcat:"))
async def admin_model_cat_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    cat = callback.data.split(":", 1)[1]
    cat_data = MODEL_CATEGORIES.get(cat, {})
    models = cat_data.get("models", {})
    cur = await get_setting("default_model") or DEFAULT_MODEL

    kb = []
    for mid, info in models.items():
        check = " ·" if mid == cur else ""
        kb.append([InlineKeyboardButton(
            text=f"{info['name']}{check}",
            callback_data=f"admin_defm:{mid}",
            icon_custom_emoji_id=peid(info.get("pe_key", "bot")),
        )])
    kb.append([InlineKeyboardButton(text=L("admin_back", lang), callback_data="admin_set_def_model", icon_custom_emoji_id=peid("settings"))])

    cat_pe = cat_data.get("_pe_key", "bot")
    lines = [f'{pe(cat_pe)} <b>{esc(cat)}</b>\n']
    for mid, info in models.items():
        m = "▸" if mid == cur else "·"
        lines.append(f'  {m} <b>{esc(info["name"])}</b> — {esc(info["desc"])}')

    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("admin_defm:"))
async def admin_set_def_model_exec(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    mid = callback.data.split(":", 1)[1]
    if mid in AVAILABLE_MODELS:
        await set_setting("default_model", mid)
        await callback.answer(f"{L('admin_default_model', lang)} → {AVAILABLE_MODELS[mid]}")
        await admin_settings_cb(callback)
    else:
        await callback.answer(L("admin_not_found", lang), show_alert=True)


@dp.callback_query(F.data == "admin_set_def_img")
async def admin_set_def_img_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    cur = await get_setting("default_image_model") or "gpt-image-1"
    kb = []
    for mid, info in IMAGE_MODELS.items():
        check = " ·" if mid == cur else ""
        kb.append([InlineKeyboardButton(
            text=f"{info['name']}{check}",
            callback_data=f"admin_defi:{mid}",
            icon_custom_emoji_id=peid(info.get("pe_key", "brush")),
        )])
    kb.append([InlineKeyboardButton(text=L("admin_back", lang), callback_data="admin_settings", icon_custom_emoji_id=peid("settings"))])
    await callback.message.edit_text(
        f'{pe("brush")} <b>{L("admin_choose_img", lang)}</b>\n\n{L("model_current", lang)}: <code>{esc(cur)}</code>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("admin_defi:"))
async def admin_set_def_img_exec(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    mid = callback.data.split(":", 1)[1]
    if mid in IMAGE_MODELS:
        await set_setting("default_image_model", mid)
        await callback.answer(f"{L('admin_default_img', lang)} → {IMAGE_MODELS[mid]['name']}")
        await admin_settings_cb(callback)
    else:
        await callback.answer(L("admin_not_found", lang), show_alert=True)


@dp.callback_query(F.data == "admin_set_def_mode")
async def admin_set_def_mode_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    cur = await get_setting("default_mode") or "useful"
    kb = []
    for mode_id, mode_info in MODES.items():
        check = " ·" if mode_id == cur else ""
        kb.append([InlineKeyboardButton(
            text=f"{L(f'mode_{mode_id}', lang)}{check}",
            callback_data=f"admin_defmode:{mode_id}",
            icon_custom_emoji_id=peid(mode_info["pe_key"]),
        )])
    kb.append([InlineKeyboardButton(text=L("admin_back", lang), callback_data="admin_settings", icon_custom_emoji_id=peid("settings"))])
    await callback.message.edit_text(
        f'{pe("smile")} <b>{L("admin_choose_mode", lang)}</b>\n\n{L("mode_current", lang)}: <code>{esc(cur)}</code>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("admin_defmode:"))
async def admin_set_def_mode_exec(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    mid = callback.data.split(":", 1)[1]
    if mid in MODES:
        await set_setting("default_mode", mid)
        await callback.answer(f"{L('admin_default_mode', lang)} → {L(f'mode_{mid}', lang)}")
        await admin_settings_cb(callback)
    else:
        await callback.answer(L("admin_not_found", lang), show_alert=True)


# ═══════════════════════════════════════════
# ADMIN — CHANNELS MANAGEMENT
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "admin_channels")
async def admin_channels_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    channels = await get_required_channels()

    if channels:
        lines = [f'{pe("horn")} <b>{L("admin_req_channels", lang)}</b>\n']
        for i, ch in enumerate(channels, 1):
            ch_id = ch.get("channel_id", ch.get("username", ""))
            invite = ch.get("invite_link", "")
            if ch_id.lstrip("-").isdigit():
                display = f'ID: {ch_id}'
                if invite:
                    display += f' ({invite})'
            else:
                display = f'@{ch_id.lstrip("@")}'
            lines.append(f'{i}. <b>{esc(ch["name"])}</b> — {display}')
    else:
        lines = [f'{pe("horn")} <b>{L("admin_req_channels", lang)}</b>\n\n{L("admin_ch_empty", lang)}']

    kb_rows = []
    kb_rows.append([InlineKeyboardButton(text=L("admin_ch_add", lang), callback_data="admin_ch_add",
                                          icon_custom_emoji_id=peid("check"))])
    for ch in channels:
        ch_id = ch.get("channel_id", ch.get("username", "")).lstrip("@")
        kb_rows.append([InlineKeyboardButton(
            text=f"{L('admin_ch_delete', lang)} {ch['name']}", callback_data=f"admin_ch_del:{ch_id}",
            icon_custom_emoji_id=peid("trash"),
        )])
    kb_rows.append([InlineKeyboardButton(text=L("admin_back", lang), callback_data="admin_refresh",
                                          icon_custom_emoji_id=peid("settings"))])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "admin_ch_add")
async def admin_ch_add_cb(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=L("admin_cancel_btn", lang), callback_data="admin_channels", icon_custom_emoji_id=peid("cross"))],
    ])
    await callback.message.edit_text(
        f'{pe("horn")} <b>{L("admin_ch_add", lang)}</b>\n\n'
        f'{L("admin_ch_format", lang)}\n'
        f'<code>@username Name</code>\n\n'
        f'{L("admin_ch_private", lang)}\n'
        f'<code>CHAT_ID Name invite_link</code>\n\n'
        f'Example:\n'
        f'<code>@genix_news Genix News</code>\n'
        f'<code>-1001234567890 Secret https://t.me/+abc</code>\n\n'
        f'{L("admin_cancel", lang)}',
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(AdminStates.waiting_for_channel_add)


@dp.message(AdminStates.waiting_for_channel_add)
async def admin_ch_add_input(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(message.from_user.id)
    if not message.text:
        return
    if message.text.strip() in ("/cancel", "cancel", L("admin_cancel_btn", lang)):
        await state.clear()
        await message.answer(L("admin_cancelled", lang))
        return
    text = message.text.strip()
    parts = text.split(None)

    if len(parts) < 2:
        await message.reply(
            f'{L("admin_ch_format_hint", lang)}',
            parse_mode=ParseMode.HTML,
        )
        return

    if parts[0].startswith("@"):
        channel_id = parts[0].lstrip("@")
        name = " ".join(parts[1:])
        invite_link = ""
    elif parts[0].lstrip("-").isdigit():
        channel_id = parts[0]
        invite_link = ""
        name_parts = []
        for p in parts[1:]:
            if p.startswith("https://"):
                invite_link = p
            else:
                name_parts.append(p)
        name = " ".join(name_parts) if name_parts else f"Channel {channel_id}"
    else:
        await message.reply(
            f'{L("admin_ch_format_hint", lang)}',
            parse_mode=ParseMode.HTML,
        )
        return

    added = await add_required_channel(channel_id, name, invite_link)
    await state.clear()

    if added:
        await message.reply(
            f'{pe("check")} {L("admin_ch_added", lang)} <b>{esc(name)}</b>',
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply(
            f'{pe("cross")} {L("admin_ch_exists", lang)}',
            parse_mode=ParseMode.HTML,
        )


@dp.callback_query(F.data.startswith("admin_ch_del:"))
async def admin_ch_del_cb(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    lang = await get_user_language(callback.from_user.id)
    ch_id = callback.data.split(":", 1)[1]
    removed = await remove_required_channel(ch_id)

    if removed:
        await callback.answer(f"{ch_id} {L('admin_ch_removed', lang)}")
    else:
        await callback.answer(L("admin_not_found", lang))

    await admin_channels_cb(callback)


# ═══════════════════════════════════════════
# LANG SET (callback from first use + settings)
# ═══════════════════════════════════════════

@dp.callback_query(F.data.startswith("lang_set:"))
async def lang_set_cb(callback: CallbackQuery):
    lang = callback.data.split(":", 1)[1]
    if lang not in ("ru", "en"):
        lang = "ru"

    uid = callback.from_user.id
    registered = await is_user_registered(uid)

    if not registered:
        await register_user_with_lang(uid, lang)
    else:
        await set_user_language(uid, lang)

    await callback.answer(L("lang_changed", lang))

    channels = await get_required_channels()
    if channels:
        missing = await check_subscriptions(uid)
        if missing:
            await _send_sub_wall(callback.message, uid, lang, edit=True)
            return

    name = callback.from_user.first_name or ("friend" if lang == "en" else "друг")
    model = await get_user_model(uid)
    mode = await get_user_mode(uid)

    try:
        await callback.message.edit_text(
            _start_text(name, model, mode, lang),
            reply_markup=_start_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await callback.message.delete()
        await bot.send_message(
            chat_id=callback.message.chat.id,
            text=_start_text(name, model, mode, lang),
            reply_markup=_start_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )


# ═══════════════════════════════════════════
# USER COMMANDS — /start /help
# ═══════════════════════════════════════════

def _start_text(name: str, model: str, mode: str, lang: str = "ru") -> str:
    model_name = AVAILABLE_MODELS.get(model, model)
    tips = L_list("tips", lang)
    tip = random.choice(tips) if tips else ""
    mode_label = L(f"mode_{mode}", lang)
    return (
        f'{pe("bot")} <b>{L("start_title", lang)}</b>\n\n'
        f'{_greeting(lang)}, <b>{esc(name)}</b>\n\n'
        f'{pe("settings")} <b>{L("start_features", lang)}</b>\n'
        f'  · {L("feat_models", lang)}\n'
        f'  · {L("feat_images", lang)}\n'
        f'  · {L("feat_photo_pdf", lang)}\n'
        f'  · {L("feat_voice", lang)}\n'
        f'  · {L("feat_web", lang)}\n\n'
        f'{pe("bot")} {L("start_model", lang)}: <b>{esc(model_name)}</b>\n'
        f'{pe("smile")} {L("start_mode", lang)}: <b>{mode_label}</b>\n\n'
        f'{pe("info")} <i>{L("tip_label", lang)}: {tip}</i>\n'
    )


def _start_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=L("btn_settings", lang), callback_data="settings_main", icon_custom_emoji_id=peid("settings")),
            InlineKeyboardButton(text=L("btn_profile", lang), callback_data="profile_show", icon_custom_emoji_id=peid("profile")),
        ],
        [
            InlineKeyboardButton(text=L("btn_draw", lang), callback_data="draw_help", icon_custom_emoji_id=peid("brush")),
            InlineKeyboardButton(text=L("btn_clear", lang), callback_data="clear_confirm", icon_custom_emoji_id=peid("trash")),
        ],
        [
            InlineKeyboardButton(text=L("btn_export", lang), callback_data="export_history", icon_custom_emoji_id=peid("download")),
        ],
        [InlineKeyboardButton(text=L("btn_all_commands", lang), callback_data="commands_list", icon_custom_emoji_id=peid("file"))],
    ])


@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    await track_chat(message)
    uid = message.from_user.id

    registered = await is_user_registered(uid)
    if not registered and message.chat.type == "private":
        await bot.send_message(
            chat_id=message.chat.id,
            text=f'{pe("bot")} <b>Welcome to Genix AI!</b>\n\nChoose your language:',
            reply_markup=_lang_select_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    await update_user_activity(uid)
    lang = await get_user_language(uid)

    if await _enforce_sub(message, uid):
        return

    model = await get_user_model(uid)
    mode = await get_user_mode(uid)
    name = message.from_user.first_name or ("friend" if lang == "en" else "друг")

    await bot.send_message(
        chat_id=message.chat.id,
        text=_start_text(name, model, mode, lang),
        reply_markup=_start_keyboard(lang),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "commands_list")
async def commands_list(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    text = (
        f'{pe("file")} <b>{L("cmd_title", lang)}</b>\n\n'
        f'<b>{L("cmd_cat_main", lang)}</b>\n'
        f'  /start — {L("cmd_start", lang)}\n'
        f'  /settings — {L("cmd_settings", lang)}\n'
        f'  /model — {L("cmd_model", lang)}\n'
        f'  /clear — {L("cmd_clear", lang)}\n'
        f'  /summary — {L("cmd_summary", lang)}\n\n'
        f'<b>{L("cmd_cat_gen", lang)}</b>\n'
        f'  /draw [prompt] — {L("cmd_draw", lang)}\n'
        f'  /imagine — {L("cmd_imagine", lang)}\n\n'
        f'<b>{L("cmd_cat_info", lang)}</b>\n'
        f'  /profile — {L("cmd_profile", lang)}\n'
        f'  /ping — {L("cmd_ping", lang)}\n\n'
        f'<b>{L("cmd_cat_other", lang)}</b>\n'
        f'  /export — {L("cmd_export", lang)}\n'
        f'  /translate — {L("btn_translate", lang)}\n'
    )
    kb = [[InlineKeyboardButton(text=L("btn_back", lang), callback_data="back_to_start", icon_custom_emoji_id=peid("settings"))]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    model = await get_user_model(callback.from_user.id)
    mode = await get_user_mode(callback.from_user.id)
    name = callback.from_user.first_name or ("friend" if lang == "en" else "друг")
    try:
        await callback.message.edit_text(
            _start_text(name, model, mode, lang),
            reply_markup=_start_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await callback.message.delete()
        await bot.send_message(
            chat_id=callback.message.chat.id,
            text=_start_text(name, model, mode, lang),
            reply_markup=_start_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )


# ═══════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════

async def _show_settings(message: Message, user_id: int):
    lang = await get_user_language(user_id)
    mode = await get_user_mode(user_id)
    model = await get_user_model(user_id)
    img_model = await get_user_image_model(user_id)
    model_name = AVAILABLE_MODELS.get(model, model)
    img_name = IMAGE_MODELS.get(img_model, {}).get("name", img_model)
    mode_label = L(f"mode_{mode}", lang)

    lang_label = "Русский" if lang == "ru" else "English"

    text = (
        f'{pe("settings")} <b>{L("settings_title", lang)}</b>\n\n'
        f'{L("settings_mode", lang)}: <b>{mode_label}</b>\n'
        f'{L("settings_model", lang)}: <code>{esc(model)}</code>\n'
        f'{L("settings_gen", lang)}: <code>{esc(img_name)}</code>\n'
    )
    kb = [
        [InlineKeyboardButton(text=f"{L('settings_mode', lang)}: {mode_label}", callback_data="mode_select",
                              icon_custom_emoji_id=peid(MODES.get(mode, {}).get("pe_key", "smile")))],
        [InlineKeyboardButton(text=f"{L('settings_model', lang)}: {model_name}", callback_data="model_categories",
                              icon_custom_emoji_id=peid("bot"))],
        [InlineKeyboardButton(text=f"{L('settings_gen', lang)}: {img_name}", callback_data="imgmodel_list",
                              icon_custom_emoji_id=peid("brush"))],
        [InlineKeyboardButton(text=f"{'Язык' if lang == 'ru' else 'Lang'}: {lang_label}", callback_data="settings_lang",
                              icon_custom_emoji_id=peid("geo"))],
        [InlineKeyboardButton(text=L("btn_back", lang), callback_data="back_to_start", icon_custom_emoji_id=peid("settings"))],
    ]
    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)
    except Exception:
        await bot.send_message(chat_id=message.chat.id, text=text,
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == "settings_main")
async def settings_menu(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    await _show_settings(callback.message, callback.from_user.id)


@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    if await _enforce_sub(message, message.from_user.id):
        return
    await _show_settings(message, message.from_user.id)


# ═══════════════════════════════════════════
# MODE SELECTION (menu instead of toggle)
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "mode_select")
async def mode_select_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    cur = await get_user_mode(callback.from_user.id)

    kb = []
    for mode_id, mode_info in MODES.items():
        check = " ·" if mode_id == cur else ""
        desc = mode_info[f"desc_{lang}"] if f"desc_{lang}" in mode_info else mode_info.get("desc_en", "")
        kb.append([InlineKeyboardButton(
            text=f"{L(f'mode_{mode_id}', lang)}{check} — {desc}",
            callback_data=f"setmode:{mode_id}",
            icon_custom_emoji_id=peid(mode_info["pe_key"]),
        )])
    kb.append([InlineKeyboardButton(text=L("btn_back", lang), callback_data="settings_main",
                                     icon_custom_emoji_id=peid("settings"))])

    text = (
        f'{pe("smile")} <b>{L("mode_choose", lang)}</b>\n\n'
        f'{L("mode_current", lang)}: <b>{L(f"mode_{cur}", lang)}</b>'
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("setmode:"))
async def set_mode_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    mid = callback.data.split(":", 1)[1]
    if mid in MODES:
        await set_user_mode(callback.from_user.id, mid)
        lang = await get_user_language(callback.from_user.id)
        await callback.answer(f"{L('settings_mode', lang)} → {L(f'mode_{mid}', lang)}")
        await settings_menu(callback)
    else:
        await callback.answer("Not found", show_alert=True)


@dp.callback_query(F.data == "settings_lang")
async def settings_lang(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    text = f'{pe("geo")} <b>{"Выбери язык" if lang == "ru" else "Choose language"}:</b>'
    kb = [
        [
            InlineKeyboardButton(text="Русский", callback_data="lang_set:ru", icon_custom_emoji_id=peid("geo")),
            InlineKeyboardButton(text="English", callback_data="lang_set:en", icon_custom_emoji_id=peid("link")),
        ],
        [InlineKeyboardButton(text=L("btn_back", lang), callback_data="settings_main", icon_custom_emoji_id=peid("settings"))],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════
# MODEL SELECTION (with premium emoji icons)
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "model_categories")
async def model_categories_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    cur = await get_user_model(callback.from_user.id)
    kb = []
    for cat, cat_data in MODEL_CATEGORIES.items():
        cat_pe = cat_data.get("_pe_key", "bot")
        kb.append([InlineKeyboardButton(text=cat, callback_data=f"mcat:{cat}", icon_custom_emoji_id=peid(cat_pe))])
    kb.append([InlineKeyboardButton(text=L("btn_back", lang), callback_data="settings_main", icon_custom_emoji_id=peid("settings"))])
    text = f'{pe("bot")} <b>{L("model_choose_cat", lang)}</b>\n\n{L("model_current", lang)}: <code>{esc(cur)}</code>'
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("mcat:"))
async def model_list_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    cat = callback.data.split(":", 1)[1]
    cat_data = MODEL_CATEGORIES.get(cat, {})
    models = cat_data.get("models", {})
    cur = await get_user_model(callback.from_user.id)

    kb = []
    for mid, info in models.items():
        check = " ·" if mid == cur else ""
        model_pe = info.get("pe_key", "bot")
        kb.append([InlineKeyboardButton(
            text=f"{info['name']}{check}",
            callback_data=f"setmodel:{mid}",
            icon_custom_emoji_id=peid(model_pe),
        )])
    kb.append([InlineKeyboardButton(text=L("btn_back", lang), callback_data="model_categories", icon_custom_emoji_id=peid("settings"))])

    cat_pe = cat_data.get("_pe_key", "bot")
    lines = [f'{pe(cat_pe)} <b>{esc(cat)}</b>\n']
    for mid, info in models.items():
        m = "▸" if mid == cur else "·"
        lines.append(f'  {m} <b>{esc(info["name"])}</b> — {esc(info["desc"])}')

    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.message(Command("model"))
async def cmd_model(message: Message):
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    cur = await get_user_model(message.from_user.id)
    kb = []
    for cat, cat_data in MODEL_CATEGORIES.items():
        cat_pe = cat_data.get("_pe_key", "bot")
        kb.append([InlineKeyboardButton(text=cat, callback_data=f"mcat:{cat}", icon_custom_emoji_id=peid(cat_pe))])
    await message.answer(
        f'{pe("bot")} <b>{L("model_choose_cat", lang)}</b>\n\n{L("model_current", lang)}: <code>{esc(cur)}</code>',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith("setmodel:"))
async def set_model_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    mid = callback.data.split(":", 1)[1]
    if mid in AVAILABLE_MODELS:
        await set_user_model(callback.from_user.id, mid)
        lang = await get_user_language(callback.from_user.id)
        label = L("settings_model", lang)
        await callback.answer(f"{label} → {AVAILABLE_MODELS[mid]}")
        await settings_menu(callback)
    else:
        await callback.answer("Not found", show_alert=True)


# ═══════════════════════════════════════════
# IMAGE MODEL SELECTION (with premium emoji)
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "imgmodel_list")
async def imgmodel_list_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    cur = await get_user_image_model(callback.from_user.id)
    kb = []
    for mid, info in IMAGE_MODELS.items():
        check = " ·" if mid == cur else ""
        img_pe = info.get("pe_key", "brush")
        kb.append([InlineKeyboardButton(
            text=f"{info['name']}{check}",
            callback_data=f"setimg:{mid}",
            icon_custom_emoji_id=peid(img_pe),
        )])
    kb.append([InlineKeyboardButton(text=L("btn_back", lang), callback_data="settings_main", icon_custom_emoji_id=peid("settings"))])

    lines = [f'{pe("brush")} <b>{L("model_gen_title", lang)}</b>\n']
    for mid, info in IMAGE_MODELS.items():
        m = "▸" if mid == cur else "·"
        lines.append(f'  {m} <b>{esc(info["name"])}</b> — {esc(info["desc"])}')

    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data.startswith("setimg:"))
async def set_img_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    mid = callback.data.split(":", 1)[1]
    if mid in IMAGE_MODELS:
        await set_user_image_model(callback.from_user.id, mid)
        lang = await get_user_language(callback.from_user.id)
        label = L("settings_gen", lang)
        await callback.answer(f"{label} → {IMAGE_MODELS[mid]['name']}")
        await settings_menu(callback)
    else:
        await callback.answer("Not found", show_alert=True)


@dp.message(Command("imagine"))
async def cmd_imagine(message: Message):
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    cur = await get_user_image_model(message.from_user.id)
    kb = []
    for mid, info in IMAGE_MODELS.items():
        check = " ·" if mid == cur else ""
        img_pe = info.get("pe_key", "brush")
        kb.append([InlineKeyboardButton(
            text=f"{info['name']}{check}",
            callback_data=f"setimg:{mid}",
            icon_custom_emoji_id=peid(img_pe),
        )])
    await message.answer(
        f'{pe("brush")} <b>{L("model_gen_title", lang)}</b>\n\n'
        f'{L("model_current", lang)}: <code>{esc(cur)}</code>\n'
        f'{L("model_after", lang)}: /draw [prompt]',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML,
    )


# ═══════════════════════════════════════════
# CLEAR HISTORY
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "clear_confirm")
async def clear_confirm(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    kb = [[
        InlineKeyboardButton(text=L("btn_yes_clear", lang), callback_data="clear_execute", icon_custom_emoji_id=peid("trash")),
        InlineKeyboardButton(text=L("btn_cancel", lang), callback_data="back_to_start", icon_custom_emoji_id=peid("settings")),
    ]]
    await callback.message.edit_text(
        f'{pe("trash")} <b>{L("clear_confirm", lang)}</b>\n\n{L("clear_irreversible", lang)}',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "clear_execute")
async def clear_execute(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    await clear_chat_history(callback.from_user.id)
    await callback.answer(L("clear_done", lang))
    await callback.message.edit_text(
        f'{pe("check")} <b>{L("clear_done", lang)}</b>',
        parse_mode=ParseMode.HTML,
    )
    await asyncio.sleep(1.5)
    await back_to_start(callback)


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    await clear_chat_history(message.from_user.id)
    await message.answer(f'{pe("check")} <b>{L("clear_done", lang)}</b>', parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════
# IMAGE GENERATION
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "draw_help")
async def draw_help(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    img_model = await get_user_image_model(callback.from_user.id)
    img_name = IMAGE_MODELS.get(img_model, {}).get("name", img_model)
    example1 = "/draw cat in space" if lang == "en" else "/draw котик в космосе"
    example2 = "/draw cyberpunk city at night"
    text = (
        f'{pe("brush")} <b>{L("draw_title", lang)}</b>\n\n'
        f'<code>{example1}</code>\n'
        f'<code>{example2}</code>\n\n'
        f'{L("settings_model", lang)}: <b>{esc(img_name)}</b>\n'
        f'{L("draw_auto_enhance", lang)}\n'
    )
    kb = [
        [InlineKeyboardButton(text=L("btn_change_model", lang), callback_data="imgmodel_list", icon_custom_emoji_id=peid("brush"))],
        [InlineKeyboardButton(text=L("btn_back", lang), callback_data="back_to_start", icon_custom_emoji_id=peid("settings"))],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.message(Command("draw"))
async def cmd_draw(message: Message):
    await track_chat(message)
    await update_user_activity(message.from_user.id)
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)

    prompt = message.text.replace("/draw", "").strip()
    if not prompt:
        example = "/draw cat in space" if lang == "en" else "/draw котик в космосе"
        await message.reply(
            f'{pe("brush")} <b>{L("draw_prompt_hint", lang)}</b>\n\n<code>{example}</code>',
            parse_mode=ParseMode.HTML,
        )
        return

    img_model = await get_user_image_model(message.from_user.id)
    img_name = IMAGE_MODELS.get(img_model, {}).get("name", img_model)

    sent = await message.answer(
        f'{pe("loading")} <b>{L("draw_generating", lang)}</b>\n\n'
        f'{L("settings_model", lang)}: {esc(img_name)}\n{L("draw_enhancing", lang)}',
        parse_mode=ParseMode.HTML,
    )

    async with ChatActionSender.upload_photo(bot=bot, chat_id=message.chat.id):
        try:
            enhanced = await enhance_prompt(prompt)

            try:
                short = esc(enhanced[:80]) + ('...' if len(enhanced) > 80 else '')
                await sent.edit_text(
                    f'{pe("loading")} <b>{L("draw_generating", lang)}</b>\n\n'
                    f'{L("settings_model", lang)}: {esc(img_name)}\n{L("draw_prompt", lang)}: <i>{short}</i>',
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

            def gen_image():
                url = f"{ONLYSQ_BASE_URL.rstrip('/')}/images/generations"
                resp = http_requests.post(url, headers=_API_HEADERS, json={
                    "model": img_model, "prompt": enhanced, "n": 1,
                }, timeout=120)
                if resp.status_code == 200:
                    return resp.json()['data'][0]['url']
                raise Exception(f"API {resp.status_code}: {resp.text[:300]}")

            img_data = await asyncio.to_thread(gen_image)
            await increment_counter(message.from_user.id, "total_images")

            caption = f'{pe("media")} <b>{L("draw_done", lang)}</b>\n{L("settings_model", lang)}: {esc(img_name)}'

            if img_data.startswith('data:image'):
                header, encoded = img_data.split(",", 1)
                match = re.search(r'image/(\w+);base64', header)
                ext = match.group(1) if match else "png"
                photo = BufferedInputFile(base64.b64decode(encoded), filename=f"genix.{ext}")
                await bot.send_photo(chat_id=message.chat.id, photo=photo, caption=caption, parse_mode=ParseMode.HTML)
            else:
                await bot.send_photo(chat_id=message.chat.id, photo=img_data, caption=caption, parse_mode=ParseMode.HTML)

            await sent.delete()

        except Exception as e:
            logger.error("DRAW ERROR: %s", e)
            await sent.edit_text(
                f'{pe("cross")} <b>{L("draw_fail", lang)}</b>\n\n'
                f'· {L("draw_tip1", lang)}\n'
                f'· {L("draw_tip2", lang)}: /imagine\n'
                f'· {L("draw_tip3", lang)}',
                parse_mode=ParseMode.HTML,
            )


# ═══════════════════════════════════════════
# PROFILE (with registration date)
# ═══════════════════════════════════════════

async def _format_profile(user_id: int, name: str = "User") -> str | None:
    stats = await get_user_stats(user_id)
    if not stats:
        return None

    lang = await get_user_language(user_id)
    model = await get_user_model(user_id)
    mode = await get_user_mode(user_id)
    img_model = await get_user_image_model(user_id)

    model_name = AVAILABLE_MODELS.get(model, model)
    img_name = IMAGE_MODELS.get(img_model, {}).get("name", img_model)
    mode_label = L(f"mode_{mode}", lang)

    total = stats['total_messages']
    if total >= 1000: rank = "Diamond"
    elif total >= 500: rank = "Gold"
    elif total >= 100: rank = "Silver"
    elif total >= 10: rank = "Bronze"
    else: rank = "Beginner" if lang == "en" else "Новичок"

    day_word = L("profile_day", lang)

    return (
        f'{pe("profile")} <b>{esc(name)}</b>\n'
        f'{pe("gift")} {rank}  ·  {stats["days_with_bot"]} {L("profile_days", lang)}\n\n'
        f'{pe("calendar")} <b>{L("profile_registered", lang)}</b>: {esc(stats["first_use"])}\n\n'
        f'{pe("stats")} <b>{L("profile_stats", lang)}</b>\n'
        f'  {L("profile_msgs", lang)}: <b>{total}</b> ({stats["avg_per_day"]}/{day_word})\n'
        f'  {L("profile_images", lang)}: {stats["total_images"]}  ·  {L("profile_voice", lang)}: {stats["total_voice"]}  ·  PDF: {stats["total_pdfs"]}\n\n'
        f'{pe("settings")} <b>{L("profile_settings", lang)}</b>\n'
        f'  {L("settings_mode", lang)}: {mode_label}\n'
        f'  {L("settings_model", lang)}: {esc(model_name)}\n'
        f'  {L("settings_gen", lang)}: {esc(img_name)}\n'
    )


@dp.callback_query(F.data == "profile_show")
async def profile_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    lang = await get_user_language(callback.from_user.id)
    text = await _format_profile(callback.from_user.id, callback.from_user.full_name)
    if not text:
        await callback.answer(L("profile_nodata", lang), show_alert=True)
        return
    kb = [[InlineKeyboardButton(text=L("btn_back", lang), callback_data="back_to_start", icon_custom_emoji_id=peid("settings"))]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode=ParseMode.HTML)


@dp.message(Command("profile", "stats"))
async def cmd_profile(message: Message):
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    text = await _format_profile(message.from_user.id, message.from_user.full_name)
    if not text:
        await message.answer(L("profile_nodata", lang))
        return
    await message.answer(text, parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════
# PING
# ═══════════════════════════════════════════

@dp.message(Command("ping"))
async def cmd_ping(message: Message):
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    t0 = time.time()
    api_status = L("ping_noconn", lang)
    if ai_client:
        try:
            await asyncio.to_thread(lambda: ai_client.models.list())
            api_status = f"{L('ping_online', lang)} ({int((time.time() - t0) * 1000)}ms)"
        except Exception:
            api_status = L("ping_error", lang)

    text = (
        f'{pe("info")} <b>Genix AI</b>\n\n'
        f'{pe("clock")} {L("ping_uptime", lang)}: {_uptime()}\n'
        f'{pe("bot")} {L("ping_ping", lang)}: {int((time.time() - t0) * 1000)}ms\n'
        f'{pe("link")} {L("ping_api", lang)}: {api_status}\n'
    )
    await message.answer(text, parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════

@dp.message(Command("summary"))
async def cmd_summary(message: Message):
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    sent = await message.answer(f'{pe("loading")} <b>{L("summary_loading", lang)}</b>', parse_mode=ParseMode.HTML)
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        summary = await get_conversation_summary(message.from_user.id)
    await sent.edit_text(
        f'{pe("write")} <b>{L("summary_title", lang)}</b>\n\n{esc(summary)}',
        parse_mode=ParseMode.HTML,
    )


# ═══════════════════════════════════════════
# EXPORT HISTORY
# ═══════════════════════════════════════════

@dp.callback_query(F.data == "export_history")
async def export_history_cb(callback: CallbackQuery):
    if await _enforce_sub_cb(callback):
        return
    await _do_export(callback.message, callback.from_user.id, callback.from_user.full_name)


@dp.message(Command("export"))
async def cmd_export(message: Message):
    if await _enforce_sub(message, message.from_user.id):
        return
    await _do_export(message, message.from_user.id, message.from_user.full_name)


async def _do_export(message: Message, user_id: int, name: str):
    lang = await get_user_language(user_id)
    history = await get_chat_history(user_id, limit=999)
    if not history:
        await message.answer(f'{pe("cross")} <b>{L("export_empty", lang)}</b>', parse_mode=ParseMode.HTML)
        return

    you_label = "You" if lang == "en" else "Вы"
    lines = [
        L("export_header", lang),
        f"{L('export_user', lang)}: {name} ({user_id})",
        f"{L('export_date', lang)}: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        "",
    ]
    for h in history:
        role = you_label if h["role"] == "user" else "Genix"
        lines.append(f"[{role}]\n{h['content']}\n")

    content = "\n".join(lines)
    file = BufferedInputFile(content.encode("utf-8"), filename=f"genix_history_{user_id}.txt")
    await bot.send_document(
        chat_id=message.chat.id,
        document=file,
        caption=f'{pe("download")} <b>{L("export_title", lang)}</b>\n{len(history)} {L("export_msgs", lang)}',
        parse_mode=ParseMode.HTML,
    )


# ═══════════════════════════════════════════
# TRANSLATE (via /translate)
# ═══════════════════════════════════════════

@dp.message(Command("translate"))
async def cmd_translate(message: Message):
    await track_chat(message)
    uid = message.from_user.id
    if await _enforce_sub(message, uid):
        return
    lang = await get_user_language(uid)

    text = message.text.replace("/translate", "").strip()
    if not text:
        await message.reply(
            f'{pe("write")} <b>{L("btn_translate", lang)}</b>\n\n'
            f'<code>/translate Hello world</code>',
            parse_mode=ParseMode.HTML,
        )
        return

    sent = await message.reply(
        f'{pe("loading")} <b>{L("translate_loading", lang)}</b>',
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await asyncio.to_thread(
            _chat_api_call,
            [
                {"role": "system", "content": L("translate_prompt", lang)},
                {"role": "user", "content": text},
            ],
            temperature=0.3, max_tokens=500, timeout=20,
        )
        if result:
            await sent.edit_text(
                f'{pe("write")} {esc(result)}',
                parse_mode=ParseMode.HTML,
            )
        else:
            await sent.edit_text(f'{pe("cross")} Error', parse_mode=ParseMode.HTML)
    except Exception:
        await sent.edit_text(f'{pe("cross")} Error', parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════
# DOCUMENT (PDF)
# ═══════════════════════════════════════════

@dp.message(F.document)
async def handle_document(message: Message):
    if not message.document.file_name.lower().endswith(".pdf"):
        return
    await track_chat(message)
    await update_user_activity(message.from_user.id)
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    caption = message.caption or ""
    if not await _should_respond(message, caption):
        return

    is_private = message.chat.type == "private"
    sent = await message.answer(f'{pe("loading")} <b>{L("pdf_loading", lang)}</b>', parse_mode=ParseMode.HTML)

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        temp_path = f"temp/{message.document.file_id}.pdf"
        try:
            os.makedirs("temp", exist_ok=True)
            file_info = await bot.get_file(message.document.file_id)
            await bot.download_file(file_info.file_path, temp_path)

            pdf_text = await asyncio.to_thread(extract_text_for_rag, temp_path)
            if not pdf_text:
                await sent.edit_text(f'{pe("cross")} <b>{L("pdf_fail", lang)}</b>', parse_mode=ParseMode.HTML)
                return

            await increment_counter(message.from_user.id, "total_pdfs")
            default_q = "Summarize this document." if lang == "en" else "Сделай краткую выжимку этого документа."
            user_prompt = caption or default_q
            rag_prompt = f"### PDF:\n{pdf_text}\n\n### {'QUESTION' if lang == 'en' else 'ВОПРОС'}:\n{user_prompt}"

            await save_message(message.from_user.id, "user", f"[PDF: {message.document.file_name}] {user_prompt}")
            response = await get_ai_response(rag_prompt, message.from_user.id, is_group=(not is_private))
            await save_message(message.from_user.id, "assistant", response)
            await send_response(message, response, lang)
            await sent.delete()
        except Exception as e:
            logger.error("PDF Error: %s", e)
            try:
                await sent.edit_text(f'{pe("cross")} <b>{L("pdf_error", lang)}</b>', parse_mode=ParseMode.HTML)
            except Exception:
                pass
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass


# ═══════════════════════════════════════════
# VOICE
# ═══════════════════════════════════════════

@dp.message(F.voice)
async def handle_voice(message: Message):
    await track_chat(message)
    await update_user_activity(message.from_user.id)
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)
    if not await _should_respond(message, ""):
        return

    is_private = message.chat.type == "private"
    sent = await message.answer(f'{pe("loading")} <b>{L("voice_recognizing", lang)}</b>', parse_mode=ParseMode.HTML)

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        temp_path = f"temp/voice_{message.voice.file_id}.ogg"
        try:
            os.makedirs("temp", exist_ok=True)
            file_info = await bot.get_file(message.voice.file_id)
            await bot.download_file(file_info.file_path, temp_path)

            transcript = await transcribe_voice(temp_path)

            if not transcript:
                await sent.edit_text(
                    f'{pe("cross")} <b>{L("voice_fail", lang)}</b>\n\n{L("voice_hint", lang)}',
                    parse_mode=ParseMode.HTML,
                )
                return

            await increment_counter(message.from_user.id, "total_voice")

            try:
                short = esc(transcript[:150]) + ('...' if len(transcript) > 150 else '')
                await sent.edit_text(
                    f'{pe("check")} <b>{L("voice_recognized", lang)}</b>\n<i>{short}</i>\n\n{pe("loading")} {L("voice_generating", lang)}',
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

            await save_message(message.from_user.id, "user", f"[VOICE] {transcript}")
            response = await get_ai_response(transcript, message.from_user.id, is_group=(not is_private))
            await save_message(message.from_user.id, "assistant", response)
            await send_response(message, response, lang)
            await sent.delete()
        except Exception as e:
            logger.error("Voice error: %s", e)
            try:
                await sent.edit_text(f'{pe("cross")} <b>{L("voice_error", lang)}</b>', parse_mode=ParseMode.HTML)
            except Exception:
                pass
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass


# ═══════════════════════════════════════════
# MEDIA GROUP
# ═══════════════════════════════════════════

media_groups: dict = {}


async def process_media_group(media_group_id: str, message: Message):
    await asyncio.sleep(1.5)
    if media_group_id not in media_groups:
        return
    group = media_groups.pop(media_group_id)
    msgs = group['messages']
    main = msgs[0]

    caption = " ".join([m.caption for m in msgs if m.caption])
    if not await _should_respond(main, caption):
        return

    is_private = main.chat.type == "private"
    lang = await get_user_language(main.from_user.id)
    async with ChatActionSender.typing(bot=bot, chat_id=main.chat.id):
        try:
            photo = main.photo[-1]
            fi = await bot.get_file(photo.file_id)
            content = await bot.download_file(fi.file_path)
            img_b64 = base64.b64encode(content.read()).decode('utf-8')

            default_q = "What's in these photos?" if lang == "en" else "Что на этих фото?"
            prompt = caption or default_q
            await track_chat(main)
            await save_message(main.from_user.id, "user", f"[ALBUM] {prompt}")
            response = await get_ai_response(prompt, main.from_user.id, image_b64=img_b64, is_group=(not is_private))
            await save_message(main.from_user.id, "assistant", response)
            await send_response(main, response, lang)
        except Exception as e:
            logger.error("Media group error: %s", e)


# ═══════════════════════════════════════════
# PHOTO
# ═══════════════════════════════════════════

@dp.message(F.photo)
async def handle_photo(message: Message):
    await update_user_activity(message.from_user.id)
    if await _enforce_sub(message, message.from_user.id):
        return
    if message.media_group_id:
        if message.media_group_id not in media_groups:
            media_groups[message.media_group_id] = {'messages': [message]}
            asyncio.create_task(process_media_group(message.media_group_id, message))
        else:
            media_groups[message.media_group_id]['messages'].append(message)
        return

    caption = message.caption or ""
    if not await _should_respond(message, caption):
        await track_chat(message)
        return

    is_private = message.chat.type == "private"
    lang = await get_user_language(message.from_user.id)
    await track_chat(message)

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        try:
            photo = message.photo[-1]
            fi = await bot.get_file(photo.file_id)
            content = await bot.download_file(fi.file_path)
            img_b64 = base64.b64encode(content.read()).decode('utf-8')
            default_q = "What's in this photo?" if lang == "en" else "Что на фото?"
            prompt = caption or default_q
            await save_message(message.from_user.id, "user", f"[IMAGE] {prompt}")
            response = await get_ai_response(prompt, message.from_user.id, image_b64=img_b64, is_group=(not is_private))
            await save_message(message.from_user.id, "assistant", response)
            await send_response(message, response, lang)
        except Exception as e:
            logger.error("Photo error: %s", e)
            await message.answer(f'{pe("cross")} <b>{L("photo_error", lang)}</b>', parse_mode=ParseMode.HTML)


# ═══════════════════════════════════════════
# TEXT MESSAGE
# ═══════════════════════════════════════════

@dp.message(F.text)
async def handle_message(message: Message):
    if message.text.startswith('/'):
        return

    is_private = message.chat.type == "private"
    bi = await get_bot_info()
    prompt = message.text

    if not is_private:
        if not await _should_respond(message, message.text):
            await track_chat(message)
            return
        prompt = _strip_mention(message.text, bi.username or "")

    await track_chat(message)
    await update_user_activity(message.from_user.id)
    if await _check_ban(message.from_user.id):
        return
    if await _enforce_sub(message, message.from_user.id):
        return
    lang = await get_user_language(message.from_user.id)

    if not prompt:
        if not is_private:
            await message.reply(L("group_mention", lang))
        return

    urls = find_urls(prompt)
    if urls and len(prompt.split()) < 10:
        url = urls[0]
        url_msg = await message.answer(
            f'{pe("link")} <b>{L("url_reading", lang)}</b>\n<code>{esc(url[:60])}</code>',
            parse_mode=ParseMode.HTML,
        )
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            page = await scrape_web_page(url)
        if page:
            default_q = "What is this site about?" if lang == "en" else "О чем этот сайт?"
            user_q = prompt.replace(url, "").strip() or default_q
            prompt = f"### {'WEBSITE' if lang == 'en' else 'САЙТ'} ({url}):\n{page}\n\n### {'QUESTION' if lang == 'en' else 'ВОПРОС'}:\n{user_q}"
            await url_msg.delete()
        else:
            await url_msg.edit_text(
                f'{pe("cross")} <b>{L("url_fail", lang)}</b> {L("url_fallback", lang)}',
                parse_mode=ParseMode.HTML,
            )

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        await save_message(message.from_user.id, "user", prompt)
        response = await get_ai_response(prompt, message.from_user.id, is_group=(not is_private))
        await save_message(message.from_user.id, "assistant", response)
        await send_response(message, response, lang)


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

async def main():
    logger.info("Genix AI Starting...")
    await init_db()
    await get_bot_info()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
