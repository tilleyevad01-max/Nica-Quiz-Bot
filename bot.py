import os
import json
import random
import logging
import asyncio
import psycopg2
from threading import Thread
from flask import Flask
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from docx import Document

# --- SOZLAMALAR (Render Environment Variables'dan olinadi) ---
API_TOKEN = os.getenv('API_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

logging.basicConfig(level=logging.INFO)

# Botni tekshirish
if not API_TOKEN:
    raise ValueError("XATO: API_TOKEN topilmadi! Render sozlamalariga kiring.")

bot = Bot(token=API_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- DATABASE FUNKSIYALARI ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # PostgreSQL uchun 'uz' yaktirnoqda bo'lishi shart
    c.execute("CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, lang TEXT DEFAULT 'uz')")
    c.execute('''CREATE TABLE IF NOT EXISTS quizzes 
                 (id SERIAL PRIMARY KEY, name TEXT, questions TEXT, 
                  timer INTEGER, creator_id BIGINT)''')
    conn.commit()
    c.close()
    conn.close()

# --- MATNLAR ---
TEXTS = {
    'uz': {
        'start': "Xush kelibsiz! /new - yangi quiz, /myquiz - boshqarish, /language - til.",
        'enter_name': "Quiz uchun nom kiriting:",
        'send_docx': "Endi .docx faylni yuboring:",
        'timer_select': "Har bir savol uchun vaqtni tanlang (soniya):",
        'done': "Quiz yaratildi! ID: <code>{id}</code>\nIshlatish: <code>/run {id}</code>",
        'no_quiz': "Sizda hali quizlar yo'q.",
        'edit_n': "Nomni tahrirlash", 'edit_t': "Taymerni tahrirlash"
    },
    'ru': {
        'start': "Добро пожаловать! /new - новый квиз, /myquiz - управление.",
        'enter_name': "Введите название квиза:",
        'send_docx': "Отправьте .docx файл:",
        'timer_select': "Выберите время на вопрос:",
        'done': "Квиз создан! ID: <code>{id}</code>",
        'no_quiz': "У вас пока нет квизов.",
        'edit_n': "Изменить имя", 'edit_t': "Изменить таймер"
    },
    'en': {
        'start': "Welcome! /new - new quiz, /myquiz - manage.",
        'enter_name': "Enter quiz name:",
        'send_docx': "Send .docx file:",
        'timer_select': "Select timer per question:",
        'done': "Quiz created! ID: <code>{id}</code>",
        'no_quiz': "No quizzes found.",
        'edit_n': "Edit Name", 'edit_t': "Edit Timer"
    }
}

class QuizStates(StatesGroup):
    waiting_name = State()
    waiting_file = State()
    waiting_timer = State()

def g_txt(uid, key):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT lang FROM users WHERE id = %s", (uid,))
        res = c.fetchone()
        lang = res[0] if res else 'uz'
        c.close()
        conn.close()
        return TEXTS.get(lang, TEXTS['uz']).get(key, key)
    except:
        return TEXTS['uz'].get(key, key)

# --- BUYRUQLAR ---

@dp.message_handler(commands=['start'])
async def cmd_start(m: types.Message):
    await m.answer(g_txt(m.from_user.id, 'start'))

@dp.message_handler(commands=['language'])
async def cmd_lang(m: types.Message):
    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("Uzbek 🇺🇿", callback_data="setl_uz"),
        types.InlineKeyboardButton("Русский 🇷🇺", callback_data="setl_ru"),
        types.InlineKeyboardButton("English 🇺🇸", callback_data="setl_en")
    )
    await m.answer("Select language / Tilni tanlang:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('setl_'))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split('_')[1]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO users (id, lang) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET lang = %s", (cb.from_user.id, lang, lang))
    conn.commit()
    c.close()
    conn.close()
    await cb.message.edit_text("Success / Muvaffaqiyatli!")

@dp.message_handler(commands=['new'])
async def cmd_new(m: types.Message):
    await m.answer(g_txt(m.from_user.id, 'enter_name'))
    await QuizStates.waiting_name.set()

@dp.message_handler(state=QuizStates.waiting_name)
async def p_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await m.answer(g_txt(m.from_user.id, 'send_docx'))
    await QuizStates.waiting_file.set()

@dp.message_handler(content_types=['document'], state=QuizStates.waiting_file)
async def p_file(m: types.Message, state: FSMContext):
    if not m.document.file_name.endswith('.docx'):
        return await m.answer("Faqat .docx fayl yuboring!")
    
    path = f"tmp_{m.from_user.id}.docx"
    await m.document.download(destination_file=path)
    
    try:
        doc = Document(path)
        qs = []
        curr = None
        for p in doc.paragraphs:
            t = p.text.strip()
            if not t: continue
            if t.startswith('#'):
                if curr: qs.append(curr)
                curr = {"q": t[1:].strip(), "options": []}
            elif t.startswith('+'):
                option = t[1:].strip()
                curr['options'].append(option)
                curr['correct'] = option
            else:
                if curr: curr['options'].append(t)
        if curr: qs.append(curr)
        os.remove(path)
        
        await state.update_data(qs=json.dumps(qs))
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True).add("10", "15", "20", "30", "60")
        await m.answer(g_txt(m.from_user.id, 'timer_select'), reply_markup=kb)
        await QuizStates.waiting_timer.set()
    except Exception as e:
        await m.answer(f"Faylni o'qishda xato: {e}")

@dp.message_handler(state=QuizStates.waiting_timer)
async def p_timer(m: types.Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("Faqat raqam kiriting!")
        
    data = await state.get_data()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO quizzes (name, questions, timer, creator_id) VALUES (%s, %s, %s, %s) RETURNING id",
              (data['name'], data['qs'], int(m.text), m.from_user.id))
    qid = c.fetchone()[0]
    conn.commit()
    c.close()
    conn.close()
    await m.answer(g_txt(m.from_user.id, 'done').format(id=qid), reply_markup=types.ReplyKeyboardRemove())
    await state.finish()

@dp.message_handler(commands=['run'])
async def run_q(m: types.Message):
    args = m.get_args()
    if not args or not args.isdigit():
        return await m.answer("Ishlatish: /run ID (Masalan: /run 1)")
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT questions, timer FROM quizzes WHERE id = %s", (args,))
    res = c.fetchone()
    c.close()
    conn.close()
    
    if res:
        qs = json.loads(res[0])
        t_sec = res[1]
        await m.answer(f"Test boshlanmoqda... Jami savollar: {len(qs)}")
        
        for q in qs:
            opts = list(q['options'])
            random.shuffle(opts)
            correct_id = opts.index(q['correct'])
            
            await bot.send_poll(
                m.chat.id, q['q'], opts, 
                type='quiz', correct_option_id=correct_id, 
                open_period=t_sec, is_anonymous=False
            )
            # SAVOLLAR ORASIDAGI KUTISH
            await asyncio.sleep(t_sec + 3) 
    else:
        await m.answer("Quiz topilmadi.")

# --- FLASK KEEP-ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"
def run_flask(): app.run(host='0.0.0.0', port=10000)

if __name__ == '__main__':
    init_db()
    # Flaskni daemon qilib ishga tushiramiz
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Bot ishga tushdi...")
    executor.start_polling(dp, skip_updates=True)