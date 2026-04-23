import asyncio
import sqlite3
import json
import random
import os
from datetime import datetime
from typing import Dict, List, Optional
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========== KONFIGURATSIYA ==========
TELEGRAM_TOKEN = "8711992596:AAEIT25yzo0dAuM0An5_Kvt-eacCK4TQzBE"
GEMINI_API_KEY = "AIzaSyA9uJL3GbE66qBfLb9XtGQYfCUQvSWQy38"

# ========== DATABASE (SQLite) ==========
def init_db():
    conn = sqlite3.connect('students.db')
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            access_code TEXT NOT NULL,
            level TEXT DEFAULT 'A1',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            category TEXT,
            book_source TEXT,
            unit INTEGER,
            score INTEGER,
            total_questions INTEGER,
            percentage REAL,
            answers TEXT,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            user_id INTEGER PRIMARY KEY,
            student_id INTEGER,
            current_category TEXT,
            current_unit INTEGER,
            current_test JSON,
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    ''')
    
    conn.commit()
    conn.close()

init_db()

# ========== FSM STATE ==========
class TestState(StatesGroup):
    waiting_for_name = State()
    waiting_for_access_code = State()
    taking_test = State()
    answering_question = State()

# ========== AI CALL ==========
async def call_gemini(prompt: str, max_retries: int = 3) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2000}
    }
    
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        try:
                            result = data['candidates'][0]['content']['parts'][0]['text']
                            return result if result else "⚠️ AI javob qaytarmadi."
                        except (KeyError, IndexError):
                            return "⚠️ AI javob formati xato."
                    else:
                        return f"⚠️ AI xatolik: {response.status}"
        except asyncio.TimeoutError:
            if attempt == max_retries - 1:
                return "⚠️ AI vaqt oralig'ida javob bermadi."
            await asyncio.sleep(2)
        except Exception as e:
            if attempt == max_retries - 1:
                return f"⚠️ Xatolik: {str(e)}"
            await asyncio.sleep(2)
    return "⚠️ AI hozircha javob bera olmayapti."

# ========== TEST GENERATION ==========
async def generate_test(category: str, level: str, unit: int) -> Dict:
    if category in ['vocabulary', 'grammar']:
        book = "Destination Malcolm Mann"
    else:
        book = "Jaloliddin Qutimov"
    
    prompt = f"""
    {book} kitobi asosida {level} darajasi uchun {category} fanidan {unit}-mavzu bo'yicha TEST yarat.
    15 ta savol (90% test, 10% ochiq). Format:
    SAVOL: [matn]
    A) ... B) ... C) ... D) ...
    TO'G'RI JAVOB: A/B/C/D
    TUSHUNTIRISH: ...
    """
    
    response = await call_gemini(prompt)
    questions = []
    current_q = {}
    
    for line in response.split('\n'):
        line = line.strip()
        if line.startswith('SAVOL:'):
            if current_q:
                questions.append(current_q)
            current_q = {'question': line[6:].strip(), 'type': 'multiple'}
        elif line.startswith('A)'):
            current_q['A'] = line[2:].strip()
        elif line.startswith('B)'):
            current_q['B'] = line[2:].strip()
        elif line.startswith('C)'):
            current_q['C'] = line[2:].strip()
        elif line.startswith('D)'):
            current_q['D'] = line[2:].strip()
        elif line.startswith("TO'G'RI JAVOB:"):
            current_q['correct'] = line[13:].strip()
        elif line.startswith('TUSHUNTIRISH:'):
            current_q['explanation'] = line[13:].strip()
    
    if current_q:
        questions.append(current_q)
    
    return {'category': category, 'level': level, 'unit': unit, 'questions': questions[:15], 'total': len(questions[:15])}

# ========== CHECK ANSWER ==========
async def check_answer(question: Dict, user_answer: str) -> Dict:
    if question.get('type') == 'multiple':
        is_correct = user_answer.upper() == question.get('correct', '').upper()
        return {'is_correct': is_correct, 'score': 1 if is_correct else 0, 'correct_answer': question.get('correct', ''), 'explanation': question.get('explanation', '')}
    else:
        prompt = f"Savol: {question.get('question')}\\nO'quvchi javobi: {user_answer}\\nBaho: 0/1 va tushuntirish ber."
        response = await call_gemini(prompt)
        score = 1 if "1" in response else 0
        return {'is_correct': score == 1, 'score': score, 'explanation': response}

# ========== BOT HANDLERS ==========
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def get_main_keyboard():
    buttons = [
        [KeyboardButton(text="📖 Vocabulary"), KeyboardButton(text="📚 Grammar")],
        [KeyboardButton(text="📖 Reading"), KeyboardButton(text="🎧 Listening")],
        [KeyboardButton(text="✍️ Writing"), KeyboardButton(text="🎙️ Speaking")],
        [KeyboardButton(text="📊 My Results")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("🎓 Ingliz tili kursiga xush kelibsiz!\\n\\nTo'liq ismingizni kiriting:", parse_mode="Markdown")
    await state.set_state(TestState.waiting_for_name)

@dp.message(TestState.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text.strip())
    await message.answer("Endi maxsus kodni kiriting:")
    await state.set_state(TestState.waiting_for_access_code)

@dp.message(TestState.waiting_for_access_code)
async def process_access_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    valid_codes = ["STU2024", "TEST123", "CODE789"]
    if code in valid_codes:
        user_data = await state.get_data()
        conn = sqlite3.connect('students.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO students (full_name, access_code) VALUES (?, ?)", (user_data['full_name'], code))
        student_id = cursor.lastrowid
        conn.commit()
        conn.close()
        await state.update_data(student_id=student_id)
        await message.answer("✅ Tizimga kirdingiz! Quyidagi bo'limlardan tanlang:", reply_markup=get_main_keyboard())
        await state.clear()
    else:
        await message.answer("❌ Noto'g'ri kod! Qaytadan urinib ko'ring.")

@dp.message(lambda m: m.text in ["📖 Vocabulary", "📚 Grammar"])
async def show_levels(message: types.Message, state: FSMContext):
    category = "vocabulary" if message.text == "📖 Vocabulary" else "grammar"
    await state.update_data(category=category)
    buttons = [
        [InlineKeyboardButton(text="🌱 A1", callback_data=f"level_A1_{category}")],
        [InlineKeyboardButton(text="📘 A2", callback_data=f"level_A2_{category}")],
        [InlineKeyboardButton(text="📗 B1", callback_data=f"level_B1_{category}")],
        [InlineKeyboardButton(text="📕 B2", callback_data=f"level_B2_{category}")],
        [InlineKeyboardButton(text="📙 C1-C2", callback_data=f"level_C1_{category}")]
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"📚 {message.text} bo'limi uchun darajangizni tanlang:", reply_markup=markup)

@dp.callback_query(lambda c: c.data.startswith("level_"))
async def show_units(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    parts = callback_query.data.split("_")
    level, category = parts[1], parts[2]
    await state.update_data(level=level, category=category)
    
    if category in ['vocabulary', 'grammar'] and level == 'B1':
        units = [3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42]
    else:
        units = list(range(1, 13))
    
    buttons = []
    row = []
    for u in units:
        row.append(InlineKeyboardButton(text=str(u), callback_data=f"unit_{u}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback_query.message.edit_text(f"Unit tanlang:", reply_markup=markup)
    await state.update_data(units_list=units)

@dp.callback_query(lambda c: c.data.startswith("unit_"))
async def start_test(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    unit = int(callback_query.data.split("_")[1])
    user_data = await state.get_data()
    category, level = user_data.get('category'), user_data.get('level')
    await callback_query.message.edit_text("⏳ Test tayyorlanmoqda...")
    test = await generate_test(category, level, unit)
    
    if test and test.get('questions'):
        await state.update_data(current_test=test, current_question_index=0, current_unit=unit, student_answers=[])
        await send_question(callback_query.message, state)
    else:
        await callback_query.message.edit_text("❌ Test yaratishda xatolik. Qaytadan urinib ko'ring.")

async def send_question(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    test, index = user_data.get('current_test'), user_data.get('current_question_index', 0)
    questions = test.get('questions', [])
    
    if index >= len(questions):
        await finish_test(message, state)
        return
    
    q = questions[index]
    text = f"*{index+1}. {q['question']}*\\n\\nA) {q.get('A', '')}\\nB) {q.get('B', '')}\\nC) {q.get('C', '')}\\nD) {q.get('D', '')}\\n\\nJavobingizni A/B/C/D harfi bilan yuboring."
    await message.edit_text(text, parse_mode="Markdown")
    await state.set_state(TestState.answering_question)

@dp.message(TestState.answering_question)
async def process_answer(message: types.Message, state: FSMContext):
    user_answer = message.text.strip()
    user_data = await state.get_data()
    test, index, answers = user_data.get('current_test'), user_data.get('current_question_index', 0), user_data.get('student_answers', [])
    questions = test.get('questions', [])
    
    if index < len(questions):
        q = questions[index]
        result = await check_answer(q, user_answer)
        answers.append({'question': q.get('question'), 'user_answer': user_answer, 'correct': result.get('is_correct'), 'score': result.get('score'), 'explanation': result.get('explanation')})
        await state.update_data(student_answers=answers, current_question_index=index+1)
        
        if result.get('is_correct'):
            await message.answer("✅ To'g'ri! " + result.get('explanation', ''))
        else:
            await message.answer(f"❌ Noto'g'ri! To'g'ri javob: {result.get('correct_answer', '')}\\n{result.get('explanation', '')}")
        await send_question(message, state)

async def finish_test(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    student_id, category, level, unit, answers = user_data.get('student_id'), user_data.get('category'), user_data.get('level'), user_data.get('current_unit'), user_data.get('student_answers', [])
    total = len(answers)
    score = sum(a.get('score', 0) for a in answers)
    percent = (score / total * 100) if total > 0 else 0
    
    conn = sqlite3.connect('students.db')
    cursor = conn.cursor()
    book_source = 'Destination Malcolm Mann' if category in ['vocabulary', 'grammar'] else 'Jaloliddin Qutimov'
    cursor.execute("INSERT INTO test_results (student_id, category, book_source, unit, score, total_questions, percentage, answers) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (student_id, category, book_source, unit, score, total, percent, json.dumps(answers)))
    conn.commit()
    conn.close()
    
    await message.answer(f"📊 NATIJALAR\\n✅ {score}/{total}\\n📈 {percent:.1f}%", reply_markup=get_main_keyboard())
    await state.clear()

@dp.message(lambda m: m.text == "📊 My Results")
async def show_results(message: types.Message):
    conn = sqlite3.connect('students.db')
    cursor = conn.cursor()
    cursor.execute("SELECT full_name FROM students ORDER BY id DESC LIMIT 1")
    student = cursor.fetchone()
    if student:
        cursor.execute("SELECT category, unit, percentage, completed_at FROM test_results WHERE student_id = (SELECT id FROM students ORDER BY id DESC LIMIT 1) ORDER BY completed_at DESC LIMIT 10")
        results = cursor.fetchall()
        if results:
            text = f"📊 {student[0]} ning natijalari:\\n\\n"
            for r in results:
                text += f"• {r[0].upper()} Unit {r[1]}: {r[2]:.0f}%\\n"
        else:
            text = "Hali test ishlamagansiz. /start"
    else:
        text = "/start bosing"
    conn.close()
    await message.answer(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def main():
    print("🤖 Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())