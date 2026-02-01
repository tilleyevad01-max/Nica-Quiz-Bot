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
    raise ValueError("API_TOKEN Render'da sozlanmagan!")

bot = Bot(token=API_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# --- DATABASE ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, lang TEXT DEFAULT 'uz')")
    c.execute('''CREATE TABLE IF NOT EXISTS quizzes 
                 (id SERIAL PRIMARY KEY, name TEXT, questions TEXT, 
                  timer INTEGER, creator_id BIGINT)''')
    # Natijalarni saqlash uchun jadval
    c.execute('''CREATE TABLE IF NOT EXISTS results 
                 (poll_id TEXT PRIMARY KEY, chat_id BIGINT, user_id BIGINT, 
                  user_name TEXT, is_correct BOOLEAN)''')
    conn.commit()
    c.close()
    conn.close()

# --- STATES ---
class QuizStates(StatesGroup):
    waiting_name = State()
    waiting_file = State()
    waiting_timer = State()

# --- TEST JARAYONI UCHUN GLOBAL LUG'AT ---
# Qaysi chatda qaysi test ketayotganini kuzatish uchun
active_quizzes = {}

# --- HANDLERS ---

@dp.message_handler(commands=['start'])
async def cmd_start(m: types.Message):
    await m.answer("Xush kelibsiz! \n/new - Yangi test yaratish\n/run ID - Testni boshlash")

@dp.message_handler(commands=['new'])
async def cmd_new(m: types.Message):
    await m.answer("Quiz uchun nom kiriting:")
    await QuizStates.waiting_name.set()

@dp.message_handler(state=QuizStates.waiting_name)
async def p_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await m.answer("Endi .docx faylni yuboring:")
    await QuizStates.waiting_file.set()

@dp.message_handler(content_types=['document'], state=QuizStates.waiting_file)
async def p_file(m: types.Message, state: FSMContext):
    if not m.document.file_name.endswith('.docx'):
        return await m.answer("Faqat .docx fayl!")
    
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
            if curr: curr['options'].append(t)
    if curr: qs.append(curr)
    os.remove(path)
    
    await state.update_data(qs=json.dumps(qs))
    await m.answer("Vaqtni tanlang (soniya):", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add("15", "30", "60"))
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
    await m.answer(f"Tayyor! ID: <code>{qid}</code>", reply_markup=types.ReplyKeyboardRemove())
    await state.finish()

# --- JAVOBLARNI TUTIB OLISH ---
@dp.poll_answer_handler()
async def handle_poll_answer(quiz_answer: types.PollAnswer):
    # active_quizzes lug'atidan ushbu poll tegishli ekanini tekshiramiz
    p_id = quiz_answer.poll_id
    conn = get_db_connection()
    c = conn.cursor()
    
    # Javob to'g'riligini tekshirish uchun bazadan yoki lug'atdan foydalanamiz
    # Bu yerda soddalashtirish uchun bazaga yozib ketamiz
    is_correct = False
    for quiz_id, data in active_quizzes.items():
        if p_id in data['polls']:
            if quiz_answer.option_ids[0] == data['polls'][p_id]:
                is_correct = True
            
            c.execute("INSERT INTO results (poll_id, chat_id, user_id, user_name, is_correct) VALUES (%s, %s, %s, %s, %s)",
                      (p_id, quiz_answer.user.id, quiz_answer.user.id, quiz_answer.user.full_name, is_correct))
            break
    
    conn.commit()
    c.close()
    conn.close()

@dp.message_handler(commands=['run'])
async def run_q(m: types.Message):
    qid = m.get_args()
    if not qid: return
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT questions, timer, name FROM quizzes WHERE id = %s", (qid,))
    res = c.fetchone()
    
    if res:
        questions = json.loads(res[0])
        timer = res[1]
        quiz_name = res[2]
        
        # Chat uchun testni aktivlashtirish
        active_quizzes[m.chat.id] = {'polls': {}, 'total': len(questions)}
        
        await m.answer(f"🏁 <b>{quiz_name}</b> testi boshlanmoqda!\nSavollar soni: {len(questions)}")
        
        for q in questions:
            opts = list(q['options'])
            random.shuffle(opts)
            correct_idx = opts.index(q['correct'])
            
            sent_poll = await bot.send_poll(
                m.chat.id, q['q'], opts, type='quiz', 
                correct_option_id=correct_idx, open_period=timer, is_anonymous=False
            )
            
            # Poll ID va to'g'ri javobni saqlaymiz
            active_quizzes[m.chat.id]['polls'][sent_poll.poll.id] = correct_idx
            await asyncio.sleep(timer + 3)
        
        # --- NATIJALARNI HISOBLASH ---
        await m.answer("✅ Test yakunlandi! Natijalar hisoblanmoqda...")
        await asyncio.sleep(2)
        
        c.execute("""SELECT user_name, COUNT(*) FILTER (WHERE is_correct = True) as correct
                     FROM results WHERE poll_id IN %s GROUP BY user_id, user_name 
                     ORDER BY correct DESC""", (tuple(active_quizzes[m.chat.id]['polls'].keys()),))
        
        results = c.fetchall()
        
        text = f"📊 <b>{quiz_name}</b> Natijalari:\n\n"
        for i, row in enumerate(results, 1):
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            rank = medals.get(i, f"{i}-o'rin:")
            wrong = len(questions) - row[1]
            text += f"{rank} {row[0]} — {row[1]} to'g'ri, {wrong} xato\n"
        
        if not results:
            text += "Hech kim qatnashmadi. 🤷‍♂️"
            
        await m.answer(text)
        # Tozalash
        del active_quizzes[m.chat.id]
        
    c.close()
    conn.close()

# --- FLASK ---
app = Flask('')
@app.route('/')
def home(): return "OK"
def run_flask(): app.run(host='0.0.0.0', port=10000)

if __name__ == '__main__':
    init_db()
    Thread(target=run_flask, daemon=True).start()
    executor.start_polling(dp, skip_updates=True)