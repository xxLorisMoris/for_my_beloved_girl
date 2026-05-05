import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ONLYSQ_API_KEY = os.getenv("ONLYSQ_API_KEY")
ONLYSQ_BASE_URL = os.getenv("ONLYSQ_BASE_URL", "https://api.onlysq.ru/ai/openai/")

ADMIN_IDS = [997130391]

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set")

DB_PATH = "bot_database.db"

# --- Model Configuration ---
DEFAULT_MODEL = "gpt-4.1-mini"
VISION_MODEL = "gpt-4o"
IMAGE_GEN_MODEL = "gpt-image-1"

# Categorized models (pe_key = premium emoji key for button icon)
MODEL_CATEGORIES = {
    "OpenAI": {
        "_pe_key": "bot",
        "models": {
            "gpt-4.1":      {"name": "GPT-4.1",      "desc": "Новейшая модель",        "pe_key": "bot"},
            "gpt-4.1-mini": {"name": "GPT-4.1 Mini",  "desc": "Быстрая и умная",        "pe_key": "clock"},
            "gpt-5":        {"name": "GPT-5",         "desc": "Максимум интеллекта",     "pe_key": "gift"},
            "gpt-4o":       {"name": "GPT-4o",        "desc": "Мультимодальная",         "pe_key": "eye"},
            "gpt-4o-mini":  {"name": "GPT-4o Mini",   "desc": "Лёгкая мультимодальная",  "pe_key": "send"},
            "o3-mini":      {"name": "o3-mini",        "desc": "Рассуждение",             "pe_key": "code"},
        },
    },
    "Anthropic": {
        "_pe_key": "code",
        "models": {
            "claude-sonnet-4-5": {"name": "Claude Sonnet 4.5", "desc": "Креативный и точный", "pe_key": "pencil"},
        },
    },
    "DeepSeek": {
        "_pe_key": "link",
        "models": {
            "deepseek-r1": {"name": "DeepSeek R1", "desc": "Цепочка рассуждений", "pe_key": "link"},
            "deepseek-v3": {"name": "DeepSeek V3", "desc": "Универсальная",       "pe_key": "download"},
        },
    },
    "Google & Open Source": {
        "_pe_key": "tag",
        "models": {
            "gemini-3-flash":        {"name": "Gemini 3 Flash", "desc": "Молниеносная",      "pe_key": "party"},
            "qwen3-235b-a22b-2507":  {"name": "Qwen 3 235B",   "desc": "Огромная open-source", "pe_key": "code"},
            "llama-3.3-70b":         {"name": "LLaMA 3.3 70B", "desc": "Meta AI",             "pe_key": "tag"},
        },
    },
}

# Flat dict for quick lookup
AVAILABLE_MODELS = {}
for cat_data in MODEL_CATEGORIES.values():
    for model_id, info in cat_data["models"].items():
        AVAILABLE_MODELS[model_id] = info["name"]

# Vision-capable models
VISION_CAPABLE_MODELS = {
    "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-5",
    "gemini-3-flash", "claude-sonnet-4-5",
}

# Image generation models (pe_key = premium emoji key)
IMAGE_MODELS = {
    "gpt-image-1":   {"name": "GPT Image",     "desc": "OpenAI генерация",      "pe_key": "media"},
    "gpt-image-1.5": {"name": "GPT Image 1.5", "desc": "Новейшая OpenAI",       "pe_key": "party"},
    "flux":          {"name": "Flux",           "desc": "Быстрая генерация",     "pe_key": "clock"},
    "flux-2-dev":    {"name": "Flux 2 Dev",     "desc": "Улучшенное качество",   "pe_key": "brush"},
    "grok-2-image":  {"name": "Grok 2 Image",  "desc": "xAI генерация",         "pe_key": "send"},
}

# ── Defaults (actual values stored in DB, editable via admin panel) ──
# History limit default: 50 (change via admin panel → Настройки бота)
# Required channels: [] (add/remove via admin panel → Каналы)
