import logging
import random
import os
import sqlite3
import json
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from docx import Document

# --- SOZLAMALAR ---
API_TOKEN = 'SIZNING_BOT_TOKENINGIZ'

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- DATABASE BILAN ISHLASH ---
DB_NAME = "quiz_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, lang TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS quizzes 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, questions TEXT, 
                  timer INTEGER, creator_id INTEGER)''')
    conn.commit()
    conn.close()

def get_user_lang(uid):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT lang FROM users WHERE id = ?", (uid,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else 'uz'

def set_user_lang(uid, lang):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (id, lang) VALUES (?, ?)", (uid, lang))
    conn.commit()
    conn.close()

# --- MATNLAR ---
TEXTS = {
    'uz': {
        'start': "Assalomu alaykum! /new buyrug'i bilan quiz yarating.",
        'new_quiz': "Yangi quiz uchun nom kiriting:",
        'send_doc': "Endi .docx faylni yuboring.",
        'error_doc': "Faqat .docx fayl!",
        'parse_done': "{n} ta savol topildi. Taymerni tanlang:",
        'finish': "Quiz yaratildi! Uni guruhda ishlatish uchun: <code>/run {id}</code>",
        'my_quizzes': "Sizning quizlaringiz:",
        'lang_select': "Tilni tanlang:",
        'btn_edit_name': "Nomni o'zgartirish",
        'btn_edit_timer': "Taymerni o'zgartirish"
    },
    'ru': {
        'start': "Здравствуйте! Создайте квиз через /new.",
        'new_quiz': "Введите название квиза:",
        'send_doc': "Отправьте .docx файл.",
        'error_doc': "Только .docx!",
        'parse_done': "Найдено {n} вопросов. Выберите таймер:",
        'finish': "Квиз создан! Запуск в группе: <code>/run {id}</code>",
        'my_quizzes': "Ваши квизы:",
        'lang_select': "Выберите язык:",
        'btn_edit_name': "Изменить название",
        'btn_edit_timer': "Изменить таймер"
    },
    'en': {
        'start': "Welcome! Create a quiz with /new.",
        'new_quiz': "Enter quiz name:",
        'send_doc': "Send .docx file.",
        'error_doc': "Only .docx!",
        'parse_done': "Found {n} questions. Select timer:",
        'finish': "Quiz created! To run in group: <code>/run {id}</code>",
        'my_quizzes': "Your quizzes:",
        'lang_select': "Select language:",
        'btn_edit_name': "Edit Name",
        'btn_edit_timer': "Edit Timer"
    }
}

class QuizStates(StatesGroup):
    waiting_name = State()
    waiting_file = State()
    waiting_timer = State()
    edit_name = State()
    edit_timer = State()

def t(uid, key):
    lang = get_user_lang(uid)
    return TEXTS.get(lang, TEXTS['uz']).get(key, key)

# --- BOT LOGIKASI ---

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer(t(message.from_user.id, 'start'))

@dp.message_handler(commands=['language'])
async def cmd_lang(message: types.Message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Uz 🇺🇿", callback_data="l_uz"),
           types.InlineKeyboardButton("Ru 🇷🇺", callback_data="l_ru"),
           types.InlineKeyboardButton("En 🇺🇸", callback_data="l_en"))
    await message.answer(t(message.from_user.id, 'lang_select'), reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('l_'))
async def set_l(cb: types.CallbackQuery):
    lang = cb.data.split('_')[1]
    set_user_lang(cb.from_user.id, lang)
    await cb.message.edit_text("Success!")

@dp.message_handler(commands=['new'])
async def cmd_new(message: types.Message):
    await message.answer(t(message.from_user.id, 'new_quiz'))
    await QuizStates.waiting_name.set()

@dp.message_handler(state=QuizStates.waiting_name)
async def p_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer(t(message.from_user.id, 'send_doc'))
    await QuizStates.waiting_file.set()

@dp.message_handler(content_types=['document'], state=QuizStates.waiting_file)
async def p_doc(message: types.Message, state: FSMContext):
    if not message.document.file_name.endswith('.docx'):
        return await message.answer(t(message.from_user.id, 'error_doc'))
    
    path = f"tmp_{message.from_user.id}.docx"
    await message.document.download(destination_file=path)
    doc = Document(path)
    qs = []
    curr = None
    for p in doc.paragraphs:
        txt = p.text.strip()
        if not txt: continue
        if txt.startswith('#'):
            if curr: qs.append(curr)
            curr = {"q": txt[1:].strip(), "options": []}
        elif txt.startswith('+'):
            curr['options'].append(txt[1:].strip())
            curr['correct'] = txt[1:].strip()
        else:
            curr['options'].append(txt)
    if curr: qs.append(curr)
    os.remove(path)
    
    await state.update_data(qs=json.dumps(qs))
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True).add("10", "15", "20", "30", "60")
    await message.answer(t(message.from_user.id, 'parse_done').format(n=len(qs)), reply_markup=kb)
    await QuizStates.waiting_timer.set()

@dp.message_handler(state=QuizStates.waiting_timer)
async def p_timer(message: types.Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO quizzes (name, questions, timer, creator_id) VALUES (?, ?, ?, ?)",
              (data['name'], data['qs'], int(message.text), message.from_user.id))
    qid = c.lastrowid
    conn.commit()
    conn.close()
    await message.answer(t(message.from_user.id, 'finish').format(id=qid), reply_markup=types.ReplyKeyboardRemove())
    await state.finish()

@dp.message_handler(commands=['myquiz'])
async def my_q(message: types.Message):
    uid = message.from_user.id
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, name, timer FROM quizzes WHERE creator_id = ?", (uid,))
    rows = c.fetchall()
    conn.close()
    
    if not rows: return await message.answer(t(uid, 'no_quiz'))
    
    for row in rows:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(t(uid, 'btn_edit_name'), callback_data=f"en_{row[0]}"),
               types.InlineKeyboardButton(t(uid, 'btn_edit_timer'), callback_data=f"et_{row[0]}"))
        await message.answer(f"ID: {row[0]} | <b>{row[1]}</b>\nVaqt: {row[2]}s", reply_markup=kb)

@dp.message_handler(commands=['run'])
async def run_q(message: types.Message):
    qid = message.get_args()
    if not qid: return
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT questions, timer FROM quizzes WHERE id = ?", (qid,))
    res = c.fetchone()
    conn.close()
    
    if res:
        qs = json.loads(res[0])
        for q in qs:
            opts = q['options']
            random.shuffle(opts)
            await bot.send_poll(message.chat.id, q['q'], opts, type='quiz', 
                               correct_option_id=opts.index(q['correct']), open_period=res[1], is_anonymous=False)

# --- TAHRIRLASH ---
@dp.callback_query_handler(lambda c: c.data.startswith('en_'))
async def edit_name_start(cb: types.CallbackQuery, state: FSMContext):
    await state.update_data(edit_id=cb.data.split('_')[1])
    await cb.message.answer("Yangi nomni kiriting:")
    await QuizStates.edit_name.set()

@dp.message_handler(state=QuizStates.edit_name)
async def edit_name_fin(message: types.Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect(DB_NAME)
    conn.cursor().execute("UPDATE quizzes SET name = ? WHERE id = ?", (message.text, data['edit_id']))
    conn.commit()
    conn.close()
    await message.answer("Tayyor!")
    await state.finish()

if __name__ == '__main__':
    init_db()
    executor.start_polling(dp, skip_updates=True)
