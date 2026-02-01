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

# --- SOZLAMALAR ---
API_TOKEN = os.getenv('API_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

logging.basicConfig(level=logging.INFO)

if not API_TOKEN:
    raise ValueError("API_TOKEN Render sozlamalarida kiritilmagan!")

bot = Bot(token=API_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- MA'LUMOTLAR BAZASI ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, lang TEXT DEFAULT 'uz')")
    c.execute('''CREATE TABLE IF NOT EXISTS quizzes 
                 (id SERIAL PRIMARY KEY, name TEXT, questions TEXT, 
                  timer INTEGER, creator_id BIGINT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS results 
                 (poll_id TEXT PRIMARY KEY, chat_id BIGINT, user_id BIGINT, 
                  user_name TEXT, is_correct BOOLEAN)''')
    conn.commit()
    c.close()
    conn.close()

# --- KO'P TILLI MATNLAR ---
TEXTS = {
    'uz': {
        'start': "Xush kelibsiz! \n/new - Yangi quiz yaratish\n/myquiz - Testlarim\n/language - Tilni o'zgartirish",
        'name': "Quiz uchun nom kiriting:",
        'file': "Endi .docx faylni yuboring:",
        'timer': "Vaqtni tanlang (soniya):",
        'done': "Tayyor! ID: <code>{id}</code>\nIshlatish: <code>/run {id}</code>",
        'run': "🏁 <b>{name}</b> testi boshlanmoqda!",
        'finish': "✅ Test yakunlandi! Natijalar:",
        'no_quiz': "Sizda hali quizlar yo'q.",
        'wait': "Navbatdagi savol yuborilmoqda..."
    },
    'ru': {
        'start': "Добро пожаловать! \n/new - Создать квиз\n/myquiz - Мои тесты\n/language - Сменить язык",
        'name': "Введите название квиза:",
        'file': "Отправьте .docx файл:",
        'timer': "Выберите время (секунды):",
        'done': "Готово! ID: <code>{id}</code>\nЗапуск: <code>/run {id}</code>",
        'run': "🏁 Тест <b>{name}</b> начинается!",
        'finish': "✅ Тест окончен! Результаты:",
        'no_quiz': "У вас пока нет квизов.",
        'wait': "Отправка следующего вопроса..."
    },
    'en': {
        'start': "Welcome! \n/new - Create quiz\n/myquiz - My quizzes\n/language - Change language",
        'name': "Enter quiz name:",
        'file': "Send the .docx file:",
        'timer': "Select timer (seconds):",
        'done': "Done! ID: <code>{id}</code>\nRun: <code>/run {id}</code>",
        'run': "🏁 Starting quiz: <b>{name}</b>!",
        'finish': "✅ Quiz finished! Results:",
        'no_quiz': "No quizzes found.",
        'wait': "Sending next question..."
    }
}

class QuizStates(StatesGroup):
    waiting_name = State()
    waiting_file = State()
    waiting_timer = State()

# --- GLOBAL HOLAT ---
active_quizzes = {}

async def get_lang(uid):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT lang FROM users WHERE id = %s", (uid,))
    res = c.fetchone()
    c.close()
    conn.close()
    return res[0] if res else 'uz'

# --- HANDLERS ---

@dp.message_handler(commands=['start'])
async def cmd_start(m: types.Message):
    lang = await get_lang(m.from_user.id)
    await m.answer(TEXTS[lang]['start'])

@dp.message_handler(commands=['language'])
async def cmd_lang(m: types.Message):
    kb = types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("Uzbek 🇺🇿", callback_data="setl_uz"),
        types.InlineKeyboardButton("Русский 🇷🇺", callback_data="setl_ru"),
        types.InlineKeyboardButton("English 🇺🇸", callback_data="setl_en")
    )
    await m.answer("Tilni tanlang / Выберите язык / Choose language:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith('setl_'))
async def set_lang(cb: types.CallbackQuery):
    lang = cb.data.split('_')[1]
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO users (id, lang) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET lang = %s", (cb.from_user.id, lang, lang))
    conn.commit()
    c.close()
    conn.close()
    await cb.message.edit_text("✅ Done!")

@dp.message_handler(commands=['new'])
async def cmd_new(m: types.Message):
    lang = await get_lang(m.from_user.id)
    await m.answer(TEXTS[lang]['name'])
    await QuizStates.waiting_name.set()

@dp.message_handler(state=QuizStates.waiting_name)
async def p_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    lang = await get_lang(m.from_user.id)
    await m.answer(TEXTS[lang]['file'])
    await QuizStates.waiting_file.set()

@dp.message_handler(content_types=['document'], state=QuizStates.waiting_file)
async def p_file(m: types.Message, state: FSMContext):
    path = f"tmp_{m.from_user.id}.docx"
    await m.document.download(destination_file=path)
    doc = Document(path)
    qs, curr = [], None
    for p in doc.paragraphs:
        t = p.text.strip()
        if not t: continue
        if t.startswith('#'):
            if curr: qs.append(curr)
            curr = {"q": t[1:].strip(), "options": []}
        elif t.startswith('+'):
            curr['options'].append(t[1:].strip())
            curr['correct'] = t[1:].strip()
        else:
            if curr: curr['options'].append(t)
    if curr: qs.append(curr)
    os.remove(path)
    await state.update_data(qs=json.dumps(qs))
    lang = await get_lang(m.from_user.id)
    await m.answer(TEXTS[lang]['timer'], reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("15", "30", "60"))
    await QuizStates.waiting_timer.set()

@dp.message_handler(state=QuizStates.waiting_timer)
async def p_timer(m: types.Message, state: FSMContext):
    data = await state.get_data()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO quizzes (name, questions, timer, creator_id) VALUES (%s, %s, %s, %s) RETURNING id",
              (data['name'], data['qs'], int(m.text), m.from_user.id))
    qid = c.fetchone()[0]
    conn.commit(); c.close(); conn.close()
    lang = await get_lang(m.from_user.id)
    await m.answer(TEXTS[lang]['done'].format(id=qid), reply_markup=types.ReplyKeyboardRemove())
    await state.finish()

@dp.poll_answer_handler()
async def handle_poll_answer(qa: types.PollAnswer):
    conn = get_db_connection(); c = conn.cursor()
    for chat_id, data in active_quizzes.items():
        if qa.poll_id in data['polls']:
            is_correct = qa.option_ids[0] == data['polls'][qa.poll_id]
            c.execute("INSERT INTO results (poll_id, chat_id, user_id, user_name, is_correct) VALUES (%s, %s, %s, %s, %s)",
                      (qa.poll_id, chat_id, qa.user.id, qa.user.full_name, is_correct))
            data['answered'].add(qa.user.id)
            break
    conn.commit(); c.close(); conn.close()

@dp.message_handler(commands=['run'])
async def run_q(m: types.Message):
    qid = m.get_args()
    if not qid: return
    lang = await get_lang(m.from_user.id)
    conn = get_db_connection(); c = conn.cursor()
    c.execute("SELECT questions, timer, name FROM quizzes WHERE id = %s", (qid,))
    res = c.fetchone()
    if res:
        questions = json.loads(res[0]); timer = res[1]; name = res[2]
        active_quizzes[m.chat.id] = {'polls': {}, 'answered': set()}
        await m.answer(TEXTS[lang]['run'].format(name=name))
        
        for q in questions:
            opts = list(q['options']); random.shuffle(opts)
            correct_idx = opts.index(q['correct'])
            sent_poll = await bot.send_poll(m.chat.id, q['q'], opts, type='quiz', 
                                           correct_option_id=correct_idx, open_period=timer, is_anonymous=False)
            poll_id = sent_poll.poll.id
            active_quizzes[m.chat.id]['polls'][poll_id] = correct_idx
            
            # AQLLI KUTISH: LS'da tezkor, guruhda taymer boyicha
            if m.chat.type == 'private':
                for _ in range(timer * 2):
                    if len(active_quizzes[m.chat.id]['answered']) > 0:
                        await asyncio.sleep(1.5); break
                    await asyncio.sleep(0.5)
                active_quizzes[m.chat.id]['answered'].clear()
            else:
                await asyncio.sleep(timer + 2)
        
        # REYTING
        await m.answer(TEXTS[lang]['finish'])
        poll_ids = tuple(active_quizzes[m.chat.id]['polls'].keys())
        c.execute("""SELECT user_name, COUNT(*) FILTER (WHERE is_correct = True) as correct
                     FROM results WHERE poll_id IN %s GROUP BY user_id, user_name ORDER BY correct DESC""", (poll_ids,))
        rows = c.fetchall()
        report = ""
        for i, r in enumerate(rows, 1):
            icon = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            wrong = len(questions) - r[1]
            report += f"{icon} {r[0]}: {r[1]} ✅ | {wrong} ❌\n"
        await m.answer(report or "No results.")
        del active_quizzes[m.chat.id]
    c.close(); conn.close()

# --- FLASK ---
app = Flask('')
@app.route('/')
def home(): return "OK"
def run_flask(): app.run(host='0.0.0.0', port=10000)

if __name__ == '__main__':
    init_db()
    Thread(target=run_flask, daemon=True).start()
    executor.start_polling(dp, skip_updates=True)