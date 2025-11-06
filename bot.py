import os
import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openai import AsyncOpenAI
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)

# Загружаем переменные окружения из .env (на локальном запуске)
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("Не задан TELEGRAM_TOKEN. Укажи его в переменных окружения или файле .env")

if not OPENAI_API_KEY:
    raise ValueError("Не задан OPENAI_API_KEY. Укажи его в переменных окружения или файле .env")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Инициализируем клиента OpenAI
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Храним данные по пользователям в памяти:
# user_sessions = {
#   user_id: {
#       "model": "gpt-4o",
#       "messages": [ {"role": "user"/"assistant", "content": "..."} ]
#   }
# }
user_sessions = {}

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
        # помечаем активную модель галочкой
        label = f"✅ {title}" if code == current_model else title
        kb.button(text=label, callback_data=f"set_model:{code}")

    kb.button(text="⬅️ Back", callback_data="back_to_menu")
    kb.adjust(1)
    return kb.as_markup()

# ----- Вспомогательные функции -----

def get_user_session(user_id: int):
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "model": "gpt4o",   # по умолчанию GPT-4o
            "messages": []
        }
    return user_sessions[user_id]

def map_model_code_to_openai_id(model_code: str) -> str:
    # Здесь мы сопоставляем красивые названия с реальными ID моделей OpenAI.
    # При необходимости можешь заменить на актуальные.
    if model_code == "gpt5_instance":
        # условно "быстрая" модель
        return "gpt-4o-mini"
    if model_code == "gpt5_syncing":
        # условно "глубокая"
        return "gpt-4.1"
    if model_code == "gpt4o":
        return "gpt-4o"
    # запасной вариант
    return "gpt-4o"

# ----- Обработчики -----

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    session = get_user_session(user_id)

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

    # Открыть меню выбора модели
    if data == "change_model":
        await callback.message.edit_text(
            "Выбери модель для AistaiBot:",
            reply_markup=model_menu(session["model"])
        )
        await callback.answer()
        return

    # Новый чат — очищаем текущую историю, но сессия пользователя остаётся
    if data == "new_chat":
        session["messages"] = []
        await callback.message.edit_text(
            "Начали новый чат 🧹
Можешь задать первый вопрос.",
            reply_markup=main_menu()
        )
        await callback.answer()
        return

    # О боте
    if data == "about_bot":
        about_text = (
            "🤖 AistaiBot
"
            "ИИ-помощник на базе моделей OpenAI.
"
            "У каждого пользователя свой отдельный контекст диалога.
"
            "Создан практически без участия человека 😉"
        )
        await callback.message.edit_text(about_text, reply_markup=main_menu())
        await callback.answer()
        return

    # Вернуться в главное меню
    if data == "back_to_menu":
        await callback.message.edit_text(
            "Главное меню:",
            reply_markup=main_menu()
        )
        await callback.answer()
        return

    # Установка модели
    if data.startswith("set_model:"):
        _, model_code = data.split(":", 1)
        session["model"] = model_code

        # Красивое имя для отображения
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

    await callback.answer()  # на всякий случай


@dp.message()
async def handle_message(message: types.Message):
    # Игнорируем служебные сообщения и пустой текст
    if not message.text:
        return

    user_id = message.from_user.id
    session = get_user_session(user_id)

    model_code = session["model"]
    openai_model_id = map_model_code_to_openai_id(model_code)

    # Добавляем сообщение пользователя в историю
    session["messages"].append({"role": "user", "content": message.text})

    # Ограничиваем длину истории (чтобы не раздувать и не тратить лишние токены)
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
            "⚠️ Произошла ошибка при обращении к модели.
"
            "Проверь, что OpenAI API ключ и модель указаны верно."
        )

    # Сохраняем ответ ассистента в историю
    session["messages"].append({"role": "assistant", "content": answer})

    await message.answer(answer, reply_markup=main_menu())


async def main():
    logging.info("AistaiBot запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
