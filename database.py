import json
import aiosqlite
from datetime import datetime
from config import DB_PATH

_SETTINGS_DEFAULTS = {
    "history_limit": 50,
    "required_channels": "[]",
    "default_model": "gpt-4.1-mini",
    "default_image_model": "gpt-image-1",
    "default_mode": "useful",
    "welcome_message": "",
    "banned_users": "[]",
}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_use TEXT,
                last_activity TEXT,
                total_messages INTEGER DEFAULT 0,
                messages_24h INTEGER DEFAULT 0,
                last_reset_24h TEXT,
                current_model TEXT DEFAULT 'gpt-4.1-mini',
                mode TEXT DEFAULT 'useful',
                image_model TEXT DEFAULT 'gpt-image-1',
                total_images INTEGER DEFAULT 0,
                total_voice INTEGER DEFAULT 0,
                total_pdfs INTEGER DEFAULT 0,
                language TEXT DEFAULT 'ru'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                chat_type TEXT,
                chat_title TEXT,
                last_active TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message_id INTEGER,
                rating TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        for col, default in [
            ("current_model", "'gpt-4.1-mini'"),
            ("mode", "'useful'"),
            ("image_model", "'gpt-image-1'"),
            ("total_images", "0"),
            ("total_voice", "0"),
            ("total_pdfs", "0"),
            ("language", "'ru'"),
        ]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {default}")
            except Exception:
                pass

        await db.commit()


async def is_user_registered(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None


async def update_user_activity(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now()
        now_str = now.strftime("%d.%m.%Y %H:%M")

        async with db.execute(
            "SELECT first_use, last_reset_24h, messages_24h FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            default_model = await get_setting("default_model") or "gpt-4.1-mini"
            default_mode = await get_setting("default_mode") or "useful"
            default_img = await get_setting("default_image_model") or "gpt-image-1"
            await db.execute("""
                INSERT INTO users (user_id, first_use, last_activity, total_messages, messages_24h, last_reset_24h, mode, current_model, image_model)
                VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?)
            """, (user_id, now_str, now_str, now.isoformat(), default_mode, default_model, default_img))
        else:
            _, last_reset_24h, _ = row
            last_reset = datetime.fromisoformat(last_reset_24h)

            if (now - last_reset).total_seconds() > 86400:
                await db.execute("""
                    UPDATE users
                    SET last_activity = ?, total_messages = total_messages + 1, messages_24h = 1, last_reset_24h = ?
                    WHERE user_id = ?
                """, (now_str, now.isoformat(), user_id))
            else:
                await db.execute("""
                    UPDATE users
                    SET last_activity = ?, total_messages = total_messages + 1, messages_24h = messages_24h + 1
                    WHERE user_id = ?
                """, (now_str, user_id))

        await db.commit()


async def register_user_with_lang(user_id: int, lang: str):
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now()
        now_str = now.strftime("%d.%m.%Y %H:%M")
        default_model = await get_setting("default_model") or "gpt-4.1-mini"
        default_mode = await get_setting("default_mode") or "useful"
        default_img = await get_setting("default_image_model") or "gpt-image-1"
        await db.execute("""
            INSERT OR IGNORE INTO users (user_id, first_use, last_activity, total_messages, messages_24h, last_reset_24h, mode, language, current_model, image_model)
            VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?)
        """, (user_id, now_str, now_str, now.isoformat(), default_mode, lang, default_model, default_img))
        await db.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id))
        await db.commit()


async def increment_counter(user_id: int, counter: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {counter} = {counter} + 1 WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_user_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, total_messages, messages_24h, first_use, last_activity, "
            "last_reset_24h, total_images, total_voice, total_pdfs "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        user_id, total_messages, messages_24h, first_use, last_activity, _, total_images, total_voice, total_pdfs = row

        first_date = datetime.strptime(first_use, "%d.%m.%Y %H:%M")
        days_with_bot = (datetime.now() - first_date).days
        avg_per_day = round(total_messages / (days_with_bot + 1), 1)

        return {
            "user_id": user_id,
            "total_messages": total_messages,
            "messages_24h": messages_24h,
            "days_with_bot": days_with_bot,
            "avg_per_day": avg_per_day,
            "first_use": first_use,
            "last_activity": last_activity,
            "total_images": total_images or 0,
            "total_voice": total_voice or 0,
            "total_pdfs": total_pdfs or 0,
        }


async def save_message(user_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        await db.commit()


async def get_chat_history(user_id: int, limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT role, content FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"role": row[0], "content": row[1]} for row in reversed(rows)]


async def clear_chat_history(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        await db.commit()


async def set_user_model(user_id: int, model: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET current_model = ? WHERE user_id = ?", (model, user_id))
        await db.commit()


async def get_user_model(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT current_model FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else "gpt-4.1-mini"


async def set_user_image_model(user_id: int, model: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET image_model = ? WHERE user_id = ?", (model, user_id))
        await db.commit()


async def get_user_image_model(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT image_model FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else "gpt-image-1"


async def set_user_mode(user_id: int, mode: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET mode = ? WHERE user_id = ?", (mode, user_id))
        await db.commit()


async def get_user_mode(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT mode FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else "useful"


async def set_user_language(user_id: int, lang: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id))
        await db.commit()


async def get_user_language(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else "ru"


async def save_rating(user_id: int, message_id: int, rating: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ratings (user_id, message_id, rating) VALUES (?, ?, ?)",
            (user_id, message_id, rating),
        )
        await db.commit()


async def get_user_ratings_summary(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT rating, COUNT(*) FROM ratings WHERE user_id = ? GROUP BY rating",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            result = {"like": 0, "dislike": 0}
            for row in rows:
                if row[0] in result:
                    result[row[0]] = row[1]
            return result


async def register_chat_activity(chat_id: int, chat_type: str, chat_title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
        await db.execute("""
            INSERT INTO chats (chat_id, chat_type, chat_title, last_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
            chat_title=excluded.chat_title,
            last_active=excluded.last_active,
            chat_type=excluded.chat_type
        """, (chat_id, chat_type, chat_title, now_str))
        await db.commit()


async def get_global_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM chats") as c:
            total_chats = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM chats WHERE chat_type = 'private'") as c:
            private_chats = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM chats WHERE chat_type != 'private'") as c:
            group_chats = (await c.fetchone())[0]
        async with db.execute("SELECT SUM(messages_24h) FROM users") as c:
            row = await c.fetchone()
            msgs_24h = row[0] if row[0] else 0
        async with db.execute("SELECT SUM(total_messages) FROM users") as c:
            row = await c.fetchone()
            total_msgs = row[0] if row[0] else 0
        async with db.execute("SELECT SUM(total_images) FROM users") as c:
            row = await c.fetchone()
            total_images = row[0] if row[0] else 0

        return {
            "total_users_db": total_users,
            "total_chats_known": total_chats,
            "private_chats": private_chats,
            "group_chats": group_chats,
            "msgs_24h": msgs_24h,
            "total_msgs": total_msgs,
            "total_images": total_images,
        }


async def get_top_users(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, total_messages, last_activity FROM users ORDER BY total_messages DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"user_id": r[0], "total_messages": r[1], "last_activity": r[2]} for r in rows]


async def get_all_chat_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT chat_id FROM chats") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def get_chats_by_type(chat_type: str):
    async with aiosqlite.connect(DB_PATH) as db:
        if chat_type == "private":
            query = "SELECT chat_id FROM chats WHERE chat_type = 'private'"
        else:
            query = "SELECT chat_id FROM chats WHERE chat_type != 'private'"
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


# ── Bot Settings (dynamic config stored in DB) ──

async def get_setting(key: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)) as c:
            row = await c.fetchone()
            return row[0] if row else _SETTINGS_DEFAULTS.get(key, "")


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def get_history_limit() -> int:
    val = await get_setting("history_limit")
    try:
        return int(val)
    except (ValueError, TypeError):
        return 50


async def set_history_limit(limit: int):
    await set_setting("history_limit", str(limit))


async def update_user_field(user_id: int, field: str, value: str) -> bool:
    allowed = {"first_use", "total_messages", "total_images", "total_voice", "total_pdfs"}
    if field not in allowed:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        if field == "first_use":
            await db.execute("UPDATE users SET first_use = ? WHERE user_id = ?", (value, user_id))
        else:
            try:
                int_val = int(value)
            except ValueError:
                return False
            await db.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (int_val, user_id))
        await db.commit()
    return True


async def get_banned_users() -> list[int]:
    val = await get_setting("banned_users")
    try:
        users = json.loads(val)
        return users if isinstance(users, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def ban_user(user_id: int) -> bool:
    banned = await get_banned_users()
    if user_id in banned:
        return False
    banned.append(user_id)
    await set_setting("banned_users", json.dumps(banned))
    return True


async def unban_user(user_id: int) -> bool:
    banned = await get_banned_users()
    if user_id not in banned:
        return False
    banned.remove(user_id)
    await set_setting("banned_users", json.dumps(banned))
    return True


async def is_user_banned(user_id: int) -> bool:
    banned = await get_banned_users()
    return user_id in banned


async def get_required_channels() -> list[dict]:
    val = await get_setting("required_channels")
    try:
        channels = json.loads(val)
        return channels if isinstance(channels, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def set_required_channels(channels: list[dict]):
    await set_setting("required_channels", json.dumps(channels, ensure_ascii=False))


async def add_required_channel(channel_id: str, name: str, invite_link: str = "") -> bool:
    channels = await get_required_channels()
    identifier = channel_id.lstrip("@")
    for ch in channels:
        ch_id = ch.get("channel_id", ch.get("username", "")).lstrip("@")
        if ch_id == identifier:
            return False
    entry = {"channel_id": identifier, "name": name}
    if invite_link:
        entry["invite_link"] = invite_link
    channels.append(entry)
    await set_required_channels(channels)
    return True


async def remove_required_channel(channel_id: str):
    channels = await get_required_channels()
    identifier = channel_id.lstrip("@")
    new_channels = [ch for ch in channels if ch.get("channel_id", ch.get("username", "")).lstrip("@") != identifier]
    if len(new_channels) == len(channels):
        return False
    await set_required_channels(new_channels)
    return True
