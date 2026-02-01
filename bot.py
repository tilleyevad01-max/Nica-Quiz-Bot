import os, json, random, logging, psycopg2
from threading import Thread
from flask import Flask
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from docx import Document

# --- SOZLAMALAR ---
API_TOKEN=oa.getenv('8364345311:AAH0SXGdQOwHowswzMF5phJqNdl74Uoehqk')
DATABASE_URL=os.getenv('postgresql://quiz_db_7ajx_user:LWTTTrdJKNfCxEUPCJaDvQF08rZtoDyh@dpg-d5vnskjuibrs73cup3u0-a/quiz_db_7ajx')

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- DATABASE FUNKSIYALARI ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, lang TEXT DEFAULT 'uz')")
    c.execute('''CREATE TABLE IF NOT EXISTS quizzes 
                 (id SERIAL PRIMARY KEY, name TEXT, questions TEXT, 
                  timer INTEGER, creator_id BIGINT)''')
    conn.commit()
    c.close()
    conn.close()

# --- MATNLAR (3 TILDA) ---
TEXTS = {
    'uz': {
        'start': "Xush kelibsiz! /new - yangi quiz, /myquiz - boshqarish, /language - til.",
        'enter_name': "Quiz uchun nom kiriting:",
        'send_docx': "Endi .docx faylni yuboring:",
        'timer_select': "Har bir savol uchun vaqtni tanlang:",
        'done': "Quiz yaratildi! ID: <code>{id}</code>\nUni ishlatish: <code>/run {id}</code>",
        'my_quizzes': "Sizning quizlaringiz:",
        'no_quiz': "Sizda hali quizlar yo'q.",
        'edit_n': "Nomni tahrirlash", 'edit_t': "Taymerni tahrirlash", 'add_q': "Savol qo'shish"
    },
    'ru': {
        'start': "Добро пожаловать! /new - новый квиз, /myquiz - управление, /language - язык.",
        'enter_name': "Введите название квиза:",
        'send_docx': "Отправьте .docx файл:",
        'timer_select': "Выберите время на один вопрос:",
        'done': "Квиз создан! ID: <code>{id}</code>\nЗапуск: <code>/run {id}</code>",
        'my_quizzes': "Ваши квизы:",
        'no_quiz': "У вас пока нет квизов.",
        'edit_n': "Изменить имя", 'edit_t': "Изменить таймер", 'add_q': "Добавить вопрос"
    },
    'en': {
        'start': "Welcome! /new - new quiz, /myquiz - manage, /language - language.",
        'enter_name': "Enter quiz name:",
        'send_docx': "Send .docx file:",
        'timer_select': "Select timer per question:",
        'done': "Quiz created! ID: <code>{id}</code>\nRun: <code>/run {id}</code>",
        'my_quizzes': "Your quizzes:",
        'no_quiz': "No quizzes found.",
        'edit_n': "Edit Name", 'edit_t': "Edit Timer", 'add_q': "Add Question"
    }
}

class QuizStates(StatesGroup):
    waiting_name = State()
    waiting_file = State()
    waiting_timer = State()
    editing_name = State()

def g_txt(uid, key):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT lang FROM users WHERE id = %s", (uid,))
    res = c.fetchone()
    lang = res[0] if res else 'uz'
    c.close()
    conn.close()
    return TEXTS.get(lang, TEXTS['uz']).get(key, key)

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
    await cb.message.edit_text("Success!")

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
    path = f"tmp_{m.from_user.id}.docx"
    await m.document.download(destination_file=path)
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
            curr['options'].append(t[1:].strip())
            curr['correct'] = t[1:].strip()
        else:
            curr['options'].append(t)
    if curr: qs.append(curr)
    os.remove(path)
    await state.update_data(qs=json.dumps(qs))
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True).add("10", "15", "20", "30", "60")
    await m.answer(g_txt(m.from_user.id, 'timer_select'), reply_markup=kb)
    await QuizStates.waiting_timer.set()

@dp.message_handler(state=QuizStates.waiting_timer)
async def p_timer(m: types.Message, state: FSMContext):
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

@dp.message_handler(commands=['myquiz'])
async def my_quizzes(m: types.Message):
    uid = m.from_user.id
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, name, timer FROM quizzes WHERE creator_id = %s", (uid,))
    rows = c.fetchall()
    c.close()
    conn.close()
    if not rows: return await m.answer(g_txt(uid, 'no_quiz'))
    for r in rows:
        kb = types.InlineKeyboardMarkup(row_width=1).add(
            types.InlineKeyboardButton(g_txt(uid, 'edit_n'), callback_data=f"edn_{r[0]}"),
            types.InlineKeyboardButton(g_txt(uid, 'edit_t'), callback_data=f"edt_{r[0]}")
        )
        await m.answer(f"ID: {r[0]} | <b>{r[1]}</b>\nTimer: {r[2]}s", reply_markup=kb)

@dp.message_handler(commands=['run'])
async def run_q(m: types.Message):
    qid = m.get_args()
    if not qid: return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT questions, timer FROM quizzes WHERE id = %s", (qid,))
    res = c.fetchone()
    c.close()
    conn.close()
    if res:
        qs = json.loads(res[0])
        for q in qs:
            opts = q['options']
            random.shuffle(opts)
            await bot.send_poll(m.chat.id, q['q'], opts, type='quiz', 
                               correct_option_id=opts.index(q['correct']), open_period=res[1], is_anonymous=False)

# --- FLASK KEEP-ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"
def run_flask(): app.run(host='0.0.0.0', port=10000)

if __name__ == '__main__':
    try:
        init_db()
        # Flask'ni daemon rejimida ishga tushiramiz
        flask_thread = Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()
        
        logging.info("Bot ishga tushmoqda...")
        executor.start_polling(dp, skip_updates=True)
    except Exception as e:
        logging.error(f"Kutilmagan xato: {e}")