import asyncio
import logging
import re
import sys
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8510728793:AAEoiqcz1C6aQaACXbI-5V_yAt7KJ4DitwQ"  # <--- ВСТАВЬ ТОКЕН
DB_NAME = "school_bot_v5.db"  # V5 - новая версия с именами преподов

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# --- КОНСТАНТЫ ---
WEEKDAYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
WEEKDAYS_MAP = {day: i for i, day in enumerate(WEEKDAYS)}


# --- ХЕЛПЕРЫ ---
def get_current_week_dates():
    """Возвращает даты текущей недели"""
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())  # Понедельник
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_week = start_of_week + timedelta(days=6, hours=23, minutes=59)

    week_map = {}
    for i in range(7):
        day = start_of_week + timedelta(days=i)
        week_map[i] = day.strftime("%d.%m")

    return week_map, start_of_week, end_of_week


def is_date_in_week_range(date_str, start_dt, end_dt):
    try:
        day, month = map(int, date_str.split('.'))
        current_year = start_dt.year
        year = current_year
        if start_dt.month == 12 and month == 1:
            year += 1
        elif start_dt.month == 1 and month == 12:
            year -= 1
        check_date = datetime(year, month, day, 12, 0)
        return start_dt <= check_date <= end_dt
    except:
        return False


def is_valid_time(time_str):
    return bool(re.match(r'^\d{1,2}:\d{2}$', time_str))


def is_valid_date(date_str):
    return bool(re.match(r'^\d{1,2}\.\d{1,2}$', date_str))


# --- DB INIT ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # T1: Финансы (Лог операций)
        await db.execute(
            "CREATE TABLE IF NOT EXISTS T1 (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, amount REAL, teacher_share REAL, tax REAL, description TEXT)")
        # T2: Постоянное расписание
        await db.execute(
            "CREATE TABLE IF NOT EXISTS T2 (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, day_of_week TEXT, lesson_time TEXT)")
        # T3: Ученики
        await db.execute(
            "CREATE TABLE IF NOT EXISTS T3 (student_id INTEGER PRIMARY KEY, teacher_id INTEGER, student_name TEXT, subject TEXT, price_per_hour REAL)")
        # T4: Баланс преподавателей (Кошелек)
        await db.execute(
            "CREATE TABLE IF NOT EXISTS T4 (teacher_id INTEGER PRIMARY KEY, teacher_earnings REAL DEFAULT 0)")
        # T5: Разовые занятия
        await db.execute(
            "CREATE TABLE IF NOT EXISTS T5 (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, lesson_date TEXT, lesson_time TEXT, type TEXT)")
        # T6: Отмены/Переносы постоянных
        await db.execute(
            "CREATE TABLE IF NOT EXISTS T6 (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, date_to_skip TEXT)")

        # --- NEW: T7 - Информация о преподавателях ---
        # id (вручную вводим), name (Имя), rate (Ставка за час)
        await db.execute("CREATE TABLE IF NOT EXISTS T7 (id INTEGER PRIMARY KEY, name TEXT, rate REAL)")

        await db.commit()


# --- STATES ---
class StudentStates(StatesGroup):
    waiting_for_id = State()
    waiting_for_teacher_id = State()
    waiting_for_name = State()
    waiting_for_subject = State()
    waiting_for_price = State()
    waiting_for_trial_date = State()
    waiting_for_trial_time = State()

class StudentEditStates(StatesGroup):
    waiting_for_field = State()  # Ждем выбора поля
    waiting_for_value = State()  # Ждем ввода нового значения (текст/цифра)

class MoneyStates(StatesGroup):
    waiting_for_hours = State()
    expense_amount = State()
    expense_reason = State()
    manual_amount = State()

class TeacherEditStates(StatesGroup):
    waiting_for_balance = State()

class LessonStates(StatesGroup):
    waiting_for_category = State()
    waiting_for_type = State()
    waiting_for_day = State()
    waiting_for_time = State()


# Обновленный стейт для добавления препода
class TeacherAddStates(StatesGroup):
    waiting_for_new_id = State()
    waiting_for_name = State()
    waiting_for_rate = State()


class LessonMoveStates(StatesGroup):
    waiting_for_mode = State()
    waiting_for_new_day = State()
    waiting_for_new_time = State()


# --- КЛАВИАТУРЫ ---
async def get_students_keyboard(callback_prefix: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT student_id, student_name FROM T3") as cursor:
            students = await cursor.fetchall()
    builder = InlineKeyboardBuilder()
    if not students:
        builder.button(text="Список пуст", callback_data="ignore")
    else:
        for s_id, s_name in students:
            builder.button(text=f"{s_name}", callback_data=f"{callback_prefix}_{s_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data="back_main"))
    return builder.as_markup()


async def get_teachers_keyboard_select(callback_prefix: str):
    """Клавиатура с ИМЕНАМИ преподавателей (берем из T7)"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Берем ID и Имя из T7
        async with db.execute("SELECT id, name FROM T7") as cursor:
            teachers = await cursor.fetchall()

    builder = InlineKeyboardBuilder()
    if not teachers:
        return None

    for t_id, t_name in teachers:
        # Отображаем Имя
        builder.button(text=f"{t_name}", callback_data=f"{callback_prefix}_{t_id}")
    builder.adjust(2)
    return builder.as_markup()


def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 РАСПИСАНИЕ", callback_data="menu_lessons")],
        [InlineKeyboardButton(text="💰 ДЕНЬГИ", callback_data="menu_money")],
        [InlineKeyboardButton(text="🎓 УЧЕНИКИ", callback_data="menu_students")],
        [InlineKeyboardButton(text="👨‍🏫 ПРЕПОДЫ", callback_data="menu_teachers")]
    ])


bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Школьная CRM v5.0 (Имена преподов)", reply_markup=main_menu_kb())


@dp.callback_query(F.data == "back_main")
async def back_main(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Главное меню", reply_markup=main_menu_kb())


# =====================================================================
# УЧЕНИКИ
# =====================================================================
@dp.callback_query(F.data == "menu_students")
async def students_menu(c: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить нового", callback_data="add_student")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_student")], # <--- ВСТАВИТЬ СЮДА
        [InlineKeyboardButton(text="➖ Удалить", callback_data="remove_student")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ])
    await c.message.edit_text("Меню учеников:", reply_markup=kb)


@dp.callback_query(F.data == "add_student")
async def add_student_start(c: types.CallbackQuery, state: FSMContext):
    # Используем новую функцию с именами из T7
    kb = await get_teachers_keyboard_select("sel_t")
    if kb is None:
        await c.answer("Нет преподавателей! Добавьте их в меню 'Преподы'.", show_alert=True)
        return
    await c.message.edit_text("Выберите преподавателя:", reply_markup=kb)


@dp.callback_query(F.data.startswith("sel_t_"))
async def add_student_tid_selected(c: types.CallbackQuery, state: FSMContext):
    t_id = int(c.data.split("_")[2])

    # Получим имя для красоты
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT name FROM T7 WHERE id=?", (t_id,)) as cur:
            res = await cur.fetchone()
            t_name = res[0] if res else str(t_id)

    await state.update_data(t_id=t_id)
    await c.message.edit_text(f"Преподаватель: {t_name}.\nВведите ID ученика (число):")
    await state.set_state(StudentStates.waiting_for_id)


@dp.message(StudentStates.waiting_for_id)
async def st_id(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Нужно число!")
    s_id = int(m.text)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM T3 WHERE student_id = ?", (s_id,)) as cur:
            if await cur.fetchone():
                await m.answer("Такой ID уже есть!")
                return
    await state.update_data(s_id=s_id)
    await m.answer("Имя ученика:")
    await state.set_state(StudentStates.waiting_for_name)


@dp.message(StudentStates.waiting_for_name)
async def st_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await m.answer("Предмет:")
    await state.set_state(StudentStates.waiting_for_subject)


@dp.message(StudentStates.waiting_for_subject)
async def st_sub(m: types.Message, state: FSMContext):
    await state.update_data(subject=m.text)
    await m.answer("Стоимость часа для ученика:")
    await state.set_state(StudentStates.waiting_for_price)


@dp.message(StudentStates.waiting_for_price)
async def st_price(m: types.Message, state: FSMContext):
    try:
        price = float(m.text)
    except:
        return await m.answer("Число!")
    await state.update_data(price=price)
    await m.answer("📅 ДАТА ПРОБНОГО (ДД.ММ):")
    await state.set_state(StudentStates.waiting_for_trial_date)


@dp.message(StudentStates.waiting_for_trial_date)
async def st_tr_date(m: types.Message, state: FSMContext):
    if not is_valid_date(m.text): return await m.answer("Формат ДД.ММ!")
    await state.update_data(trial_date=m.text)
    await m.answer("⏰ ВРЕМЯ (ЧЧ:ММ):")
    await state.set_state(StudentStates.waiting_for_trial_time)


@dp.message(StudentStates.waiting_for_trial_time)
async def st_finish(m: types.Message, state: FSMContext):
    if not is_valid_time(m.text): return await m.answer("Формат ЧЧ:ММ!")
    d = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        # БЫЛО: INSERT INTO ...
        # СТАЛО: INSERT OR REPLACE INTO ... (Это разрешит перезапись старых ID)
        await db.execute(
            "INSERT OR REPLACE INTO T3 (student_id, teacher_id, student_name, subject, price_per_hour) VALUES (?,?,?,?,?)",
            (d['s_id'], d['t_id'], d['name'], d['subject'], d['price']))

        # Пробное занятие добавляем как и раньше
        await db.execute("INSERT INTO T5 (student_id, lesson_date, lesson_time, type) VALUES (?,?,?,?)",
                         (d['s_id'], d['trial_date'], m.text, "trial"))
        await db.commit()
    await m.answer("✅ Ученик создан (или обновлен)!", reply_markup=main_menu_kb())
    await state.clear()


@dp.callback_query(F.data == "remove_student")
async def rm_st_start(c: types.CallbackQuery):
    kb = await get_students_keyboard("del_st")
    await c.message.edit_text("Кого удаляем?", reply_markup=kb)


@dp.callback_query(F.data.startswith("del_st_"))
async def rm_st_fin(c: types.CallbackQuery):
    s_id = int(c.data.split("_")[2])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM T3 WHERE student_id=?", (s_id,))
        await db.execute("DELETE FROM T2 WHERE student_id=?", (s_id,))
        await db.execute("DELETE FROM T5 WHERE student_id=?", (s_id,))
        await db.execute("DELETE FROM T6 WHERE student_id=?", (s_id,))
        await db.commit()
    await c.message.edit_text("✅ Удален.", reply_markup=main_menu_kb())


# --- ЛОГИКА РЕДАКТИРОВАНИЯ УЧЕНИКА ---

@dp.callback_query(F.data == "edit_student")
async def edit_student_start(c: types.CallbackQuery):
    # Используем тот же список, но с префиксом edit_st
    kb = await get_students_keyboard("edit_st")
    await c.message.edit_text("Кого редактируем?", reply_markup=kb)


@dp.callback_query(F.data.startswith("edit_st_"))
async def edit_student_pick_field(c: types.CallbackQuery, state: FSMContext):
    s_id = int(c.data.split("_")[2])
    await state.update_data(s_id=s_id)

    # Меню выбора полей
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Имя", callback_data="f_edit_name"),
         InlineKeyboardButton(text="📚 Предмет", callback_data="f_edit_subj")],
        [InlineKeyboardButton(text="💰 Ставка", callback_data="f_edit_price"),
         InlineKeyboardButton(text="👨‍🏫 Преподаватель", callback_data="f_edit_teach")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data="menu_students")]
    ])
    await c.message.edit_text("Что меняем?", reply_markup=kb)
    await state.set_state(StudentEditStates.waiting_for_field)


@dp.callback_query(F.data.startswith("f_edit_"))
async def edit_student_field_selected(c: types.CallbackQuery, state: FSMContext):
    field_code = c.data.split("_")[2]  # name, subj, price, teach
    await state.update_data(field=field_code)

    if field_code == "teach":
        # Если меняем препода, показываем список преподов
        kb = await get_teachers_keyboard_select("new_t")
        await c.message.edit_text("Выберите нового преподавателя:", reply_markup=kb)
        # State не меняем, ждем клика по преподу
    else:
        # Если текст или цена
        msg_map = {
            "name": "Введите НОВОЕ ИМЯ:",
            "subj": "Введите НОВЫЙ ПРЕДМЕТ:",
            "price": "Введите НОВУЮ СТАВКУ (число):"
        }
        await c.message.edit_text(msg_map.get(field_code, "Введите значение:"))
        await state.set_state(StudentEditStates.waiting_for_value)


# Обработка выбора нового препода (кнопка)
@dp.callback_query(F.data.startswith("new_t_"))
async def edit_student_save_teacher(c: types.CallbackQuery, state: FSMContext):
    new_t_id = int(c.data.split("_")[2])
    d = await state.get_data()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE T3 SET teacher_id = ? WHERE student_id = ?", (new_t_id, d['s_id']))
        await db.commit()

    await c.message.edit_text("✅ Преподаватель изменен!", reply_markup=main_menu_kb())
    await state.clear()


# Обработка ввода текста/цены
@dp.message(StudentEditStates.waiting_for_value)
async def edit_student_save_value(m: types.Message, state: FSMContext):
    d = await state.get_data()
    field = d['field']
    new_val = m.text

    # Маппинг кодов к названиям колонок в БД
    col_map = {
        "name": "student_name",
        "subj": "subject",
        "price": "price_per_hour"
    }
    db_col = col_map[field]

    # Валидация цены
    if field == "price":
        try:
            new_val = float(new_val)
        except ValueError:
            await m.answer("Ошибка! Введите число.")
            return

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE T3 SET {db_col} = ? WHERE student_id = ?", (new_val, d['s_id']))
        await db.commit()

    await m.answer(f"✅ Поле '{field}' обновлено.", reply_markup=main_menu_kb())
    await state.clear()

# =====================================================================
# РАСПИСАНИЕ
# =====================================================================
@dp.callback_query(F.data == "menu_lessons")
async def lessons_menu(c: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 ЭТА НЕДЕЛЯ", callback_data="show_week")],
        [InlineKeyboardButton(text="♾ Шаблон", callback_data="show_reg")],
        [InlineKeyboardButton(text="➕ Добавить", callback_data="l_add")],
        [InlineKeyboardButton(text="🔄 Изменить / Перенести", callback_data="l_move_type")],
        [InlineKeyboardButton(text="❌ Удалить", callback_data="l_del")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ])
    await c.message.edit_text("Расписание:", reply_markup=kb)


@dp.callback_query(F.data == "show_week")
async def show_week(c: types.CallbackQuery):
    week_map, start_dt, end_dt = get_current_week_dates()
    start_s = start_dt.strftime("%d.%m")
    end_s = end_dt.strftime("%d.%m")

    async with aiosqlite.connect(DB_NAME) as db:
        # --- ИЗМЕНЕНИЕ: JOIN с таблицей T7, чтобы получить имя учителя ---
        async with db.execute("""
                              SELECT T3.student_id, T3.student_name, T7.name
                              FROM T3
                                       LEFT JOIN T7 ON T3.teacher_id = T7.id
                              """) as cur:
            # Словарь теперь хранит кортеж: {id: ("Имя Ученика", "Имя Препода")}
            rows = await cur.fetchall()
            studs = {r[0]: (r[1], r[2] if r[2] else "Неизвестно") for r in rows}

        async with db.execute("SELECT student_id, day_of_week, lesson_time FROM T2") as cur:
            regs = await cur.fetchall()
        async with db.execute("SELECT student_id, lesson_date, lesson_time, type FROM T5") as cur:
            ones = await cur.fetchall()
        async with db.execute("SELECT student_id, date_to_skip FROM T6") as cur:
            skips = set((r[0], r[1]) for r in await cur.fetchall())

    final = []

    # 1. Постоянные
    for s_id, day, time in regs:
        if day not in WEEKDAYS: continue
        date_on_week = week_map[WEEKDAYS.index(day)]
        if (s_id, date_on_week) in skips: continue

        # Достаем имена
        s_info = studs.get(s_id, ("?", "?"))
        s_name, t_name = s_info[0], s_info[1]

        dt = datetime.strptime(date_on_week, "%d.%m")
        # Формат: • Дата Время — Ученик (Препод)
        final.append((dt, time, f"• {date_on_week} ({day}) {time} — {s_name} ({t_name})"))

    # 2. Разовые
    for s_id, date_str, time, typ in ones:
        if is_date_in_week_range(date_str, start_dt, end_dt):
            try:
                dt = datetime.strptime(date_str, "%d.%m")

                s_info = studs.get(s_id, ("?", "?"))
                s_name, t_name = s_info[0], s_info[1]

                icon = "🆕" if typ == 'trial' else "🔄"
                final.append((dt, time, f"• {date_str} {time} {icon} — {s_name} ({t_name})"))
            except:
                continue

    final.sort(key=lambda x: (x[0], x[1]))

    txt = f"📅 <b>НЕДЕЛЯ {start_s} - {end_s}:</b>\n\n" + ("\n".join([x[2] for x in final]) if final else "Занятий нет.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="menu_lessons")]])
    await c.message.edit_text(txt, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "show_reg")
async def show_reg(c: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        # --- ИЗМЕНЕНИЕ: Тоже делаем JOIN с T7 ---
        async with db.execute("""
                              SELECT T3.student_name, T7.name, T2.day_of_week, T2.lesson_time
                              FROM T2
                                       JOIN T3 ON T2.student_id = T3.student_id
                                       LEFT JOIN T7 ON T3.teacher_id = T7.id
                              """) as cur:
            rows = await cur.fetchall()

    # Сортировка по дням недели
    rows.sort(key=lambda x: (WEEKDAYS_MAP.get(x[2], 99), x[3]))

    txt = "♾ <b>ПОСТОЯННОЕ РАСПИСАНИЕ:</b>\n"
    for s_name, t_name, day, time in rows:
        t_name = t_name if t_name else "?"
        txt += f"• {day} {time} — {s_name} ({t_name})\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="menu_lessons")]])
    await c.message.edit_text(txt, reply_markup=kb, parse_mode="HTML")

# --- ДОБАВЛЕНИЕ ---
@dp.callback_query(F.data == "l_add")
async def l_add_start(c: types.CallbackQuery):
    kb = await get_students_keyboard("l_add_st")
    await c.message.edit_text("Кому?", reply_markup=kb)


@dp.callback_query(F.data.startswith("l_add_st_"))
async def l_add_cat(c: types.CallbackQuery, state: FSMContext):
    sid = int(c.data.split("_")[3])
    await state.update_data(s_id=sid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="♾ Постоянное", callback_data="cat_reg")],
        [InlineKeyboardButton(text="1️⃣ Разовое", callback_data="cat_one")]
    ])
    await c.message.edit_text("Тип?", reply_markup=kb)
    await state.set_state(LessonStates.waiting_for_category)


@dp.callback_query(LessonStates.waiting_for_category)
async def l_add_day(c: types.CallbackQuery, state: FSMContext):
    cat = c.data
    await state.update_data(category=cat)
    msg = "День капсом (ПН..):" if cat == "cat_reg" else "Дата (ДД.ММ):"
    await c.message.edit_text(msg)
    await state.set_state(LessonStates.waiting_for_day)


@dp.message(LessonStates.waiting_for_day)
async def l_add_time(m: types.Message, state: FSMContext):
    d = await state.get_data()
    val = m.text.strip().upper()
    if d['category'] == 'cat_reg' and val not in WEEKDAYS: return await m.answer("Ошибка! ПН, ВТ...")
    if d['category'] == 'cat_one' and not is_valid_date(val): return await m.answer("Ошибка! ДД.ММ")
    await state.update_data(day=val)
    await m.answer("Время (ЧЧ:ММ):")
    await state.set_state(LessonStates.waiting_for_time)


@dp.message(LessonStates.waiting_for_time)
async def l_add_fin(m: types.Message, state: FSMContext):
    if not is_valid_time(m.text): return await m.answer("ЧЧ:ММ!")
    d = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        if d['category'] == "cat_reg":
            await db.execute("INSERT INTO T2 (student_id, day_of_week, lesson_time) VALUES (?,?,?)",
                             (d['s_id'], d['day'], m.text))
        else:
            await db.execute("INSERT INTO T5 (student_id, lesson_date, lesson_time, type) VALUES (?,?,?,?)",
                             (d['s_id'], d['day'], m.text, "one_time"))
        await db.commit()
    await m.answer("✅ Добавлено", reply_markup=main_menu_kb())
    await state.clear()


# --- ПЕРЕНОС ---
@dp.callback_query(F.data == "l_move_type")
async def l_move_choose_type(c: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Из ПОСТОЯННОГО", callback_data="mv_src_reg")],
        [InlineKeyboardButton(text="Из РАЗОВОГО", callback_data="mv_src_one")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_lessons")]
    ])
    await c.message.edit_text("Что переносим?", reply_markup=kb)


@dp.callback_query(F.data.startswith("mv_src_"))
async def l_move_list_start(c: types.CallbackQuery, state: FSMContext):
    is_reg = c.data == "mv_src_reg"
    await state.update_data(is_reg_move=is_reg)
    kb = await get_students_keyboard("mv_st")
    await c.message.edit_text("Ученик:", reply_markup=kb)


@dp.callback_query(F.data.startswith("mv_st_"))
async def l_move_show_lessons(c: types.CallbackQuery, state: FSMContext):
    sid = int(c.data.split("_")[2])
    data = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        if data.get('is_reg_move'):
            async with db.execute("SELECT id, day_of_week, lesson_time FROM T2 WHERE student_id=?", (sid,)) as cur:
                lessons = await cur.fetchall()
        else:
            async with db.execute("SELECT id, lesson_date, lesson_time FROM T5 WHERE student_id=?", (sid,)) as cur:
                lessons = await cur.fetchall()

    if not lessons: return await c.answer("Уроков нет", show_alert=True)
    builder = InlineKeyboardBuilder()
    for lid, day_date, time in lessons:
        cb = f"ed_reg_{lid}_{sid}_{day_date}" if data.get('is_reg_move') else f"ed_one_{lid}_{sid}"
        builder.button(text=f"{day_date} {time}", callback_data=cb)
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="l_move_type"))
    await c.message.edit_text("Урок:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("ed_reg_"))
async def l_move_reg_mode(c: types.CallbackQuery, state: FSMContext):
    parts = c.data.split("_")
    lid, sid, day = int(parts[2]), int(parts[3]), parts[4]
    await state.update_data(lid=lid, sid=sid, old_day=day, move_type="REGULAR")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 ТОЛЬКО ЭТОТ РАЗ", callback_data="m_once")],
        [InlineKeyboardButton(text="♾ НАВСЕГДА", callback_data="m_forever")]
    ])
    await c.message.edit_text(f"Урок {day}. Как?", reply_markup=kb)
    await state.set_state(LessonMoveStates.waiting_for_mode)


@dp.callback_query(LessonMoveStates.waiting_for_mode)
async def l_move_ask_val(c: types.CallbackQuery, state: FSMContext):
    mode = c.data
    await state.update_data(mode=mode)
    msg = "Дата (ДД.ММ):" if mode == "m_once" else "День (ПН..):"
    await c.message.edit_text(msg)
    await state.set_state(LessonMoveStates.waiting_for_new_day)


@dp.callback_query(F.data.startswith("ed_one_"))
async def l_move_one_start(c: types.CallbackQuery, state: FSMContext):
    parts = c.data.split("_")
    lid, sid = int(parts[2]), int(parts[3])
    await state.update_data(lid=lid, sid=sid, move_type="ONETIME", mode="simple_update")
    await c.message.edit_text("Новая дата (ДД.ММ):")
    await state.set_state(LessonMoveStates.waiting_for_new_day)


@dp.message(LessonMoveStates.waiting_for_new_day)
async def l_move_time(m: types.Message, state: FSMContext):
    d = await state.get_data()
    val = m.text.strip().upper()
    if d.get('mode') == 'm_forever' and val not in WEEKDAYS: return await m.answer("Ошибка! ПН, ВТ...")
    if d.get('mode') != 'm_forever' and not is_valid_date(val): return await m.answer("Ошибка! ДД.ММ")
    await state.update_data(new_val=val)
    await m.answer("Время (ЧЧ:ММ):")
    await state.set_state(LessonMoveStates.waiting_for_new_time)


@dp.message(LessonMoveStates.waiting_for_new_time)
async def l_move_fin(m: types.Message, state: FSMContext):
    if not is_valid_time(m.text): return await m.answer("ЧЧ:ММ")
    d = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        if d.get('move_type') == "ONETIME":
            await db.execute("UPDATE T5 SET lesson_date=?, lesson_time=? WHERE id=?", (d['new_val'], m.text, d['lid']))
            res = "✅ Перенесено"
        else:
            if d['mode'] == 'm_forever':
                await db.execute("UPDATE T2 SET day_of_week=?, lesson_time=? WHERE id=?",
                                 (d['new_val'], m.text, d['lid']))
                res = "✅ Изменено навсегда"
            else:
                week_map, _, _ = get_current_week_dates()
                try:
                    idx = WEEKDAYS.index(d['old_day'])
                    await db.execute("INSERT INTO T6 (student_id, date_to_skip) VALUES (?,?)",
                                     (d['sid'], week_map[idx]))
                except:
                    pass
                await db.execute("INSERT INTO T5 (student_id, lesson_date, lesson_time, type) VALUES (?,?,?,?)",
                                 (d['sid'], d['new_val'], m.text, "moved"))
                res = "✅ Перенесено"
        await db.commit()
    await m.answer(res, reply_markup=main_menu_kb())
    await state.clear()


@dp.callback_query(F.data == "l_del")
async def l_del_start(c: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Постоянные", callback_data="del_reg"),
         InlineKeyboardButton(text="Разовые", callback_data="del_one")],
        [InlineKeyboardButton(text="Назад", callback_data="menu_lessons")]
    ])
    await c.message.edit_text("Что удаляем?", reply_markup=kb)


@dp.callback_query(F.data.in_({"del_reg", "del_one"}))
async def l_del_list(c: types.CallbackQuery):
    is_reg = c.data == "del_reg"
    q = "SELECT T2.id, T3.student_name, T2.day_of_week, T2.lesson_time FROM T2 JOIN T3 ON T2.student_id=T3.student_id" if is_reg else \
        "SELECT T5.id, T3.student_name, T5.lesson_date, T5.lesson_time FROM T5 JOIN T3 ON T5.student_id=T3.student_id"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(q) as cur: rows = await cur.fetchall()
    builder = InlineKeyboardBuilder()
    for rid, name, dt, tm in rows:
        builder.button(text=f"❌ {dt} {tm} {name}", callback_data=f"kill_{'R' if is_reg else 'O'}_{rid}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Назад", callback_data="l_del"))
    await c.message.edit_text("Удаление:", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith("kill_"))
async def l_del_do(c: types.CallbackQuery):
    _, typ, rid = c.data.split("_")
    tbl = "T2" if typ == "R" else "T5"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"DELETE FROM {tbl} WHERE id=?", (rid,))
        await db.commit()
    await c.answer("Удалено")
    await l_del_list(types.CallbackQuery(id=c.id, data=f"del_{'reg' if typ == 'R' else 'one'}", message=c.message,
                                         from_user=c.from_user))


# =====================================================================
# ДЕНЬГИ (С учетом ставки препода)
# =====================================================================
@dp.callback_query(F.data == "menu_money")
async def money_menu(c: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT SUM(amount) FROM T1") as c1: r1 = await c1.fetchone()
        async with db.execute("SELECT SUM(teacher_earnings) FROM T4") as c2: r2 = await c2.fetchone()
        async with db.execute("SELECT SUM(tax) FROM T1") as c3: r3 = await c3.fetchone()
    cash, debt, tax = r1[0] or 0, r2[0] or 0, r3[0] or 0
    profit = cash - debt - tax
    txt = f"💰 Касса: {cash:.2f}\n⏳ Долг преподам: {debt:.2f}\n🏛 Налог: {tax:.2f}\n🏦 Чистые: {profit:.2f}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Доход (Урок)", callback_data="inc_start")],
        [InlineKeyboardButton(text="📉 Расход (С причиной)", callback_data="exp_start")],
        [InlineKeyboardButton(text="💵 Ручная операция (+/-)", callback_data="manual_bank")],  # <--- НОВАЯ КНОПКА
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")]
    ])
    await c.message.edit_text(txt, reply_markup=kb)


@dp.callback_query(F.data == "manual_bank")
async def manual_bank_start(c: types.CallbackQuery, state: FSMContext):
    await c.message.edit_text(
        "Введите сумму (число).\n\n• Чтобы добавить: просто число (напр. 5000)\n• Чтобы убавить: число с минусом (напр. -1500)")
    await state.set_state(MoneyStates.manual_amount)


@dp.message(MoneyStates.manual_amount)
async def manual_bank_save(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
    except ValueError:
        return await m.answer("Ошибка! Введите число.")

    async with aiosqlite.connect(DB_NAME) as db:
        # Пишем в T1 без доли учителя и без налога, описание "Ручная операция"
        await db.execute("INSERT INTO T1 (student_id, amount, teacher_share, tax, description) VALUES (0,?,0,0,?)",
                         (amt, "Ручная операция"))
        await db.commit()

    await m.answer(f"✅ Баланс изменен на {amt}", reply_markup=main_menu_kb())
    await state.clear()


@dp.callback_query(F.data == "inc_start")
async def inc_start(c: types.CallbackQuery):
    kb = await get_students_keyboard("inc_s")
    await c.message.edit_text("Ученик:", reply_markup=kb)


@dp.callback_query(F.data.startswith("inc_s_"))
async def inc_id(c: types.CallbackQuery, state: FSMContext):
    sid = int(c.data.split("_")[2])
    # Теперь достаем не только цену ученика, но и препода, чтобы узнать ставку
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
                              SELECT T3.price_per_hour, T3.teacher_id, T3.student_name
                              FROM T3
                              WHERE T3.student_id = ?
                              """, (sid,)) as cur:
            row = await cur.fetchone()

    if not row: return await c.answer("Ошибка БД")
    price_st, tid, name = row
    await state.update_data(sid=sid, price=price_st, tid=tid)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 час", callback_data="hours_1.0"),
         InlineKeyboardButton(text="1.5 часа", callback_data="hours_1.5"),
         InlineKeyboardButton(text="2 часа", callback_data="hours_2.0")]
    ])
    await c.message.edit_text(f"Ученик: {name}\nК оплате: {price_st}/час\nДлительность?", reply_markup=kb)


@dp.callback_query(F.data.startswith("hours_"))
async def inc_process(c: types.CallbackQuery, state: FSMContext):
    h = float(c.data.split("_")[1])
    d = await state.get_data()

    amt = d['price'] * h
    tax = amt * 0.04

    # 💥 НОВАЯ ЛОГИКА: Берем ставку препода из T7
    share = 0
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT rate FROM T7 WHERE id=?", (d['tid'],)) as cur:
            res = await cur.fetchone()
            if res:
                t_rate = res[0]
                share = t_rate * h  # Ставка * часы
            else:
                share = 0  # Или ошибка

        await db.execute("INSERT INTO T1 (student_id, amount, teacher_share, tax, description) VALUES (?,?,?,?,?)",
                         (d['sid'], amt, share, tax, "Income"))
        await db.execute("UPDATE T4 SET teacher_earnings = teacher_earnings + ? WHERE teacher_id=?", (share, d['tid']))
        await db.commit()

    await c.message.edit_text(f"✅ Доход: {amt}\nЗП Препода: {share} ({h}ч)", reply_markup=main_menu_kb())
    await state.clear()


@dp.callback_query(F.data == "exp_start")
async def exp_start(c: types.CallbackQuery, state: FSMContext):
    await c.message.edit_text("Сумма:")
    await state.set_state(MoneyStates.expense_amount)


@dp.message(MoneyStates.expense_amount)
async def exp_amt(m: types.Message, state: FSMContext):
    try:
        a = float(m.text)
    except:
        return
    await state.update_data(amt=-abs(a))
    await m.answer("Причина:")
    await state.set_state(MoneyStates.expense_reason)


@dp.message(MoneyStates.expense_reason)
async def exp_fin(m: types.Message, state: FSMContext):
    d = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO T1 (student_id, amount, teacher_share, tax, description) VALUES (0,?,0,0,?)",
                         (d['amt'], m.text))
        await db.commit()
    await m.answer("✅ Записано", reply_markup=main_menu_kb())
    await state.clear()


# =====================================================================
# ПРЕПОДЫ (Обновлено с именами и ставками)
# =====================================================================
async def get_teachers_keyboard_list(callback_prefix: str):
    # Теперь берем из T7 чтобы показывать имена
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, name FROM T7") as cursor:
            teachers = await cursor.fetchall()
    builder = InlineKeyboardBuilder()
    if not teachers:
        builder.button(text="Список пуст", callback_data="ignore")
    else:
        for t_id, t_name in teachers:
            builder.button(text=f"{t_name}", callback_data=f"{callback_prefix}_{t_id}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="➕ Добавить", callback_data="add_teacher_manual"))
    builder.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data="back_main"))
    return builder.as_markup()


@dp.callback_query(F.data == "menu_teachers")
async def teach_menu(c: types.CallbackQuery):
    kb = await get_teachers_keyboard_list("teach_v")
    await c.message.edit_text("Список преподавателей:", reply_markup=kb)


# --- ДОБАВЛЕНИЕ ПРЕПОДА (НОВЫЙ ПРОЦЕСС) ---
@dp.callback_query(F.data == "add_teacher_manual")
async def teach_add(c: types.CallbackQuery, state: FSMContext):
    await c.message.edit_text("Введите ID нового преподавателя (число):")
    await state.set_state(TeacherAddStates.waiting_for_new_id)


@dp.message(TeacherAddStates.waiting_for_new_id)
async def teach_add_id(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Число!")
    tid = int(m.text)
    # Проверка на дубль
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT 1 FROM T7 WHERE id=?", (tid,)) as cur:
            if await cur.fetchone(): return await m.answer("Такой ID препода уже есть!")

    await state.update_data(tid=tid)
    await m.answer("Введите ИМЯ преподавателя:")
    await state.set_state(TeacherAddStates.waiting_for_name)


@dp.message(TeacherAddStates.waiting_for_name)
async def teach_add_name(m: types.Message, state: FSMContext):
    await state.update_data(name=m.text)
    await m.answer("Введите СТАВКУ преподавателя (за 1 час):")
    await state.set_state(TeacherAddStates.waiting_for_rate)


@dp.message(TeacherAddStates.waiting_for_rate)
async def teach_add_fin(m: types.Message, state: FSMContext):
    try:
        rate = float(m.text)
    except:
        return await m.answer("Число!")

    d = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Добавляем в инфо (T7)
        await db.execute("INSERT INTO T7 (id, name, rate) VALUES (?,?,?)", (d['tid'], d['name'], rate))
        # 2. Создаем кошелек (T4)
        await db.execute("INSERT OR IGNORE INTO T4 (teacher_id, teacher_earnings) VALUES (?, 0)", (d['tid'],))
        await db.commit()

    await m.answer(f"✅ Преподаватель {d['name']} добавлен.\nСтавка: {rate} р/час.", reply_markup=main_menu_kb())
    await state.clear()


# --- ПРОСМОТР / ВЫПЛАТА ---
@dp.callback_query(F.data.startswith("teach_v_"))
async def teach_view(c: types.CallbackQuery):
    tid = int(c.data.split("_")[2])
    async with aiosqlite.connect(DB_NAME) as db:
        # Баланс
        async with db.execute("SELECT teacher_earnings FROM T4 WHERE teacher_id=?", (tid,)) as cur:
            row = await cur.fetchone()
        bal = row[0] if row else 0.0
        # Инфо
        async with db.execute("SELECT name, rate FROM T7 WHERE id=?", (tid,)) as cur:
            row2 = await cur.fetchone()

    if row2:
        name, rate = row2
        info_txt = f"👤 {name}\n🆔 ID: {tid}\n💵 Ставка: {rate}\n💰 Баланс: {bal} руб."
    else:
        info_txt = f"ID: {tid} (Нет данных в T7)\nБаланс: {bal}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Выплатить все", callback_data=f"pay_{tid}_{bal}")],
        [InlineKeyboardButton(text="✏️ Изменить долг", callback_data=f"edit_tbal_{tid}")],  # <--- ВСТАВИТЬ ЭТУ СТРОКУ
        [InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_t_{tid}")],
        [InlineKeyboardButton(text="Назад", callback_data="menu_teachers")]
    ])
    await c.message.edit_text(info_txt, reply_markup=kb)


# --- РУЧНОЕ РЕДАКТИРОВАНИЕ ДОЛГА ПРЕПОДАВАТЕЛЮ ---

@dp.callback_query(F.data.startswith("edit_tbal_"))
async def edit_teacher_balance_start(c: types.CallbackQuery, state: FSMContext):
    tid = int(c.data.split("_")[2])
    await state.update_data(tid=tid)

    await c.message.edit_text("Введите новую сумму долга преподавателю (число):")
    await state.set_state(TeacherEditStates.waiting_for_balance)


@dp.message(TeacherEditStates.waiting_for_balance)
async def edit_teacher_balance_finish(m: types.Message, state: FSMContext):
    try:
        new_balance = float(m.text)
    except ValueError:
        return await m.answer("Ошибка! Введите число.")

    d = await state.get_data()
    tid = d['tid']

    async with aiosqlite.connect(DB_NAME) as db:
        # Обновляем долг в таблице T4
        await db.execute("UPDATE T4 SET teacher_earnings = ? WHERE teacher_id = ?", (new_balance, tid))
        await db.commit()

    await m.answer(f"✅ Баланс (долг) преподавателя успешно изменен на {new_balance} руб.", reply_markup=main_menu_kb())
    await state.clear()

@dp.callback_query(F.data.startswith("del_t_"))
async def teach_del(c: types.CallbackQuery):
    tid = int(c.data.split("_")[2])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM T4 WHERE teacher_id=?", (tid,))
        await db.execute("DELETE FROM T7 WHERE id=?", (tid,))  # Удаляем и инфо
        await db.commit()
    await c.message.edit_text(f"✅ Преподаватель удален", reply_markup=main_menu_kb())


@dp.callback_query(F.data.startswith("pay_"))
async def teach_pay(c: types.CallbackQuery):
    _, tid, amt = c.data.split("_")
    amt = float(amt)
    if amt <= 0: return await c.answer("Баланс 0")

    # Получим имя для лога
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT name FROM T7 WHERE id=?", (tid,)) as cur:
            res = await cur.fetchone()
            t_name = res[0] if res else str(tid)

        await db.execute("UPDATE T4 SET teacher_earnings=0 WHERE teacher_id=?", (tid,))
        await db.execute("INSERT INTO T1 (student_id, amount, teacher_share, tax, description) VALUES (0,?,?,0,?)",
                         (-amt, -amt, f"Выплата: {t_name}"))
        await db.commit()
    await c.answer("Выплачено")
    await teach_menu(c)


async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    print("BOT STARTED V5...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass