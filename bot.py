import os
import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from openai import AsyncOpenAI
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)

# Загружаем переменные окружения из .env (для локального запуска)
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("Не задан TELEGRAM_TOKEN. Укажи его в переменных окружения или в .env")

if not OPENAI_API_KEY:
    raise ValueError("Не задан OPENAI_API_KEY. Укажи его в переменных окружения или в .env")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Инициализируем клиента OpenAI
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# URL, который Render подставляет автоматически (нужен для вебхука)
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")

# Путь и полный URL вебхука
WEBHOOK_PATH = f"/webhook/{TELEGRAM_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else None

# ----- Память сессий -----
# user_sessions = {
#   user_id: {
#       "model": "gpt4o",
#       "messages": [ {"role": "user"/"assistant", "content": "..."} ]
#   }
# }

user_sessions = {}


def get_user_session(user_id: int):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "model": "gpt4o",   # модель по умолчанию
            "messages": []
        }
    return user_sessions[user_id]


def map_model_code_to_openai_id(model_code: str) -> str:
    # Маппинг "красивых" названий на реальные ID моделей OpenAI
    if model_code == "gpt5_instance":
        return "gpt-4o-mini"   # условно "быстрая"
    if model_code == "gpt5_syncing":
        return "gpt-4.1"       # условно "глубокая"
    if model_code == "gpt4o":
        return "gpt-4o"
    return "gpt-4o"


# ----- Клавиатуры -----

def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🧠 Change model", callback_data="change_model")
    kb.button(text="🧹 New chat", callback_data="new_chat")
    kb.button(text="ℹ️ About bot", callback_data="about_bot")
    kb.adjust(1)
    return kb.as_markup()


def model_menu(current_model: str):
    kb = InlineKeyboardBuilder()

    models = [
        ("GPT-5 Instance", "gpt5_instance"),
        ("GPT-5 Syncing", "gpt5_syncing"),
        ("GPT-4o", "gpt4o"),
    ]

    for title, code in models:
        label = f"✅ {title}" if code == current_model else title
        kb.button(text=label, callback_data=f"set_model:{code}")

    kb.button(text="⬅️ Back", callback_data="back_to_menu")
    kb.adjust(1)
    return kb.as_markup()


# ----- Обработчики -----

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    get_user_session(user_id)

    text = (
        "Привет, я виртуальный помощник, написанный самим искусственным интеллектом 🤖 "
        "почти без участия человека.\n\n"
        "Выбери модель и начнём!"
    )

    await message.answer(text, reply_markup=main_menu())


@dp.callback_query()
async def handle_callbacks(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    session = get_user_session(user_id)
    data = callback.data or ""

    # Выбор модели
    if data == "change_model":
        await callback.message.edit_text(
            "Выбери модель для AistaiBot:",
            reply_markup=model_menu(session["model"])
        )
        await callback.answer()
        return

    # Новый чат
    if data == "new_chat":
        session["messages"] = []
        await callback.message.edit_text(
            "Начали новый чат 🧹\nМожешь задать первый вопрос.",
            reply_markup=main_menu()
        )
        await callback.answer()
        return

    # О боте
    if data == "about_bot":
        about_text = (
            "🤖 AistaiBot\n"
            "ИИ-помощник на базе моделей OpenAI.\n"
            "У каждого пользователя свой отдельный контекст диалога.\n"
            "Создан практически без участия человека 😉"
        )
        await callback.message.edit_text(about_text, reply_markup=main_menu())
        await callback.answer()
        return

    # Назад в меню
    if data == "back_to_menu":
        await callback.message.edit_text("Главное меню:", reply_markup=main_menu())
        await callback.answer()
        return

    # Установка модели
    if data.startswith("set_model:"):
        _, model_code = data.split(":", 1)
        session["model"] = model_code

        name_map = {
            "gpt5_instance": "GPT-5 Instance",
            "gpt5_syncing": "GPT-5 Syncing",
            "gpt4o": "GPT-4o",
        }
        model_name = name_map.get(model_code, model_code)

        await callback.message.edit_text(
            f"Модель изменена на: {model_name}",
            reply_markup=main_menu()
        )
        await callback.answer()
        return

    await callback.answer()


@dp.message()
async def handle_message(message: types.Message):
    if not message.text:
        return

    user_id = message.from_user.id
    session = get_user_session(user_id)

    model_code = session["model"]
    openai_model_id = map_model_code_to_openai_id(model_code)

    # Добавляем сообщение пользователя в историю
    session["messages"].append({"role": "user", "content": message.text})

    # Ограничиваем длину истории (по количеству сообщений, а не символов)
    max_messages = 30
    if len(session["messages"]) > max_messages:
        session["messages"] = session["messages"][-max_messages:]

    try:
        completion = await client.chat.completions.create(
            model=openai_model_id,
            messages=session["messages"],
            temperature=0.7,
        )
        answer = completion.choices[0].message.content.strip()
    except Exception as e:
        logging.exception("Ошибка при запросе к OpenAI")
        answer = (
            "⚠️ Произошла ошибка при обращении к модели.\n"
            "Проверь, что OpenAI API ключ и ID модели указаны верно."
        )

    # Сохраняем ответ ассистента в историю
    session["messages"].append({"role": "assistant", "content": answer})

    await message.answer(answer, reply_markup=main_menu())


# ----- Webhook / запуск на Render -----

async def on_startup(bot: Bot):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook установлен: {WEBHOOK_URL}")
    else:
        logging.warning("RENDER_EXTERNAL_URL не задан, webhook не установлен.")


async def main():
    logging.info("AistaiBot запускается (webhook режим)...")

    app = web.Application()

    # Обработчик запросов от Telegram по пути WEBHOOK_PATH
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    # Настраиваем приложение aiogram + webhook
    setup_application(app, dp, bot=bot, on_startup=on_startup)

    # Render передаёт порт через переменную PORT
    port = int(os.getenv("PORT", "10000"))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logging.info(f"AistaiBot запущен на порту {port}")
    # Держим процесс живым
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
