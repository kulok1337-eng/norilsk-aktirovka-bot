import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton
)

from database import (
    init_db, register_user, get_categories, add_category, delete_category, rename_category,
    add_product_to_db, get_products_by_category,
    add_to_cart, get_user_cart, clear_user_cart,
    create_order, get_orders_by_status, get_order_by_id, get_user_taken_orders, take_order, complete_order,
    add_review, get_recent_reviews,
    get_user_info, get_all_users_ids, update_user_balance, set_courier_status,
    get_user_orders_count, get_stats
)

# Новый токен бота
BOT_TOKEN = "8924425283:AAHotLsrRz9Zp5luOFnVfTNSv0FQ4ll8uec"
ADMINS = [1979046241]

TECH_WORKS = False

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- FSM СОСТОЯНИЯ ---
class AdminSG(StatesGroup):
    add_cat_name = State()
    rename_cat_select = State()
    rename_cat_name = State()
    del_cat_select = State()
    add_prod_cat = State()
    add_prod_name = State()
    add_prod_desc = State()
    add_prod_price = State()
    add_prod_photo = State()
    give_balance_id = State()
    give_balance_amount = State()
    courier_add_id = State()
    courier_del_id = State()
    search_info_id = State()
    broadcast_msg = State()

class ReviewSG(StatesGroup):
    comment = State()

# --- КЛАВИАТУРЫ ---

def get_main_menu(user_id: int):
    keyboard = [
        [KeyboardButton(text="Каталог"), KeyboardButton(text="Профиль")],
        [KeyboardButton(text="Корзина"), KeyboardButton(text="💬 Отзывы")]
    ]
    user = get_user_info(user_id)
    is_courier = user[4] if user else 0
    
    if user_id in ADMINS or is_courier == 1:
        keyboard.append([KeyboardButton(text="📦 Доступные заказы"), KeyboardButton(text="🚚 Взятые заказы")])
        keyboard.append([KeyboardButton(text="🗄 История заказов")])
    
    if user_id in ADMINS:
        keyboard.append([KeyboardButton(text="🔧 Админка")])
        
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def admin_panel_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📁 Добавить категорию", callback_data="admin_add_cat"),
                InlineKeyboardButton(text="🗑 Удалить категорию", callback_data="admin_del_cat")
            ],
            [
                InlineKeyboardButton(text="✏️ Переименовать категорию", callback_data="admin_rename_cat")
            ],
            [
                InlineKeyboardButton(text="📦 Добавить товар", callback_data="admin_add_product"),
                InlineKeyboardButton(text="✏️ Редактирование", callback_data="admin_edit_product")
            ],
            [
                InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"),
                InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")
            ],
            [
                InlineKeyboardButton(text="📈 Статистика за день", callback_data="admin_stats_day")
            ],
            [
                InlineKeyboardButton(text="👤 Назначить курьера", callback_data="admin_add_courier"),
                InlineKeyboardButton(text="👤 Разжаловать курьера", callback_data="admin_del_courier")
            ],
            [
                InlineKeyboardButton(text="🔍 Поиск / Инфо", callback_data="admin_search_info"),
                InlineKeyboardButton(text="💰 Выдать баланс", callback_data="admin_give_balance")
            ],
            [
                InlineKeyboardButton(text="🔧 Технические работы", callback_data="admin_tech_work")
            ]
        ]
    )

def categories_menu():
    categories = get_categories()
    builder = []
    row = []
    for cat_id, cat_name in categories:
        row.append(InlineKeyboardButton(text=cat_name, callback_data=f"cat_{cat_id}"))
        if len(row) == 2:
            builder.append(row)
            row = []
    if row:
        builder.append(row)
    return InlineKeyboardMarkup(inline_keyboard=builder)

# --- МИДДЛВАРЬ ТЕХНИЧЕСКИХ РАБОТ ---

@dp.message.outer_middleware()
async def tech_work_middleware(handler, event: types.Message, data):
    if TECH_WORKS and event.from_user.id not in ADMINS:
        await event.answer("⚠️ В боте ведутся технические работы. Попробуйте зайти позже.")
        return
    return await handler(event, data)

# --- ОСНОВНЫЕ КОМАНДЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    username = message.from_user.username or message.from_user.first_name
    register_user(message.from_user.id, username)
    kb = get_main_menu(message.from_user.id)
    await message.answer("Привет! Выберите нужный раздел:", reply_markup=kb)

@dp.message(F.text == "Каталог")
async def show_catalog(message: types.Message):
    await message.answer("Выберите категорию из списка:", reply_markup=categories_menu())

@dp.message(F.text == "Профиль")
async def show_profile(message: types.Message):
    user = get_user_info(message.from_user.id)
    username = user[1] if user and user[1] else (message.from_user.username or "пользователь")
    balance = int(user[2]) if user else 0
    rating = user[3] if user else 5.0
    orders_count = get_user_orders_count(message.from_user.id)
    
    text = (
        f"👤 Профиль: {username}\n"
        f"🆔 ID: `{message.from_user.id}`\n"
        f"⭐ Рейтинг: {rating}\n"
        f"💰 Баланс: {balance}₽\n"
        f"📦 Всего заказов: {orders_count}"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "Корзина")
async def show_cart(message: types.Message):
    cart_items = get_user_cart(message.from_user.id)
    if not cart_items:
        await message.answer("🛒 Ваша корзина пока пуста.")
        return
    
    total_price = 0
    text = "🛒 **Ваша корзина:**\n\n"
    for name, price, count, _ in cart_items:
        item_total = price * count
        total_price += item_total
        text += f"▪️ **{name}** — {count} шт. x {price} руб. = **{item_total} руб.**\n"
        
    text += f"\n💳 **Итого к оплате:** {total_price} руб."
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")],
        [InlineKeyboardButton(text="✅ Оформить заказ", callback_data="checkout")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "clear_cart")
async def process_clear_cart(callback: types.CallbackQuery):
    clear_user_cart(callback.from_user.id)
    await callback.message.edit_text("🛒 Ваша корзина очищена.")

@dp.callback_query(F.data == "checkout")
async def process_checkout(callback: types.CallbackQuery):
    order_id = create_order(callback.from_user.id)
    if not order_id:
        await callback.message.edit_text("🛒 Ваша корзина пуста.")
        return
    
    await callback.message.edit_text(f"🎉 **Заказ #{order_id} успешно создан!**\nМенеджер и курьер уже получили уведомление.", parse_mode="Markdown")

@dp.message(F.text == "💬 Отзывы")
async def show_reviews(message: types.Message):
    reviews = get_recent_reviews()
    if not reviews:
        await message.answer("💬 Пока отзывов нет. Вы можете оставить отзыв после получения вашего первого заказа!")
        return
    
    text = "💬 **Последние отзывы о курьерах:**\n\n"
    for rating, comment, date_str, courier_name in reviews:
        c_name = f"@{courier_name}" if courier_name else "Курьер"
        stars = "⭐" * rating
        comment_text = f'"{comment}"' if comment else "Без текстового комментария"
        text += f"▪️ {stars} для **{c_name}**\n   {comment_text}\n\n"
        
    await message.answer(text, parse_mode="Markdown")

# --- РАБОТА С ЗАКАЗАМИ И ОЦЕНКА КУРЬЕРА ---

@dp.message(F.text.in_({"📦 Доступные заказы", "🚚 Взятые заказы", "🗄 История заказов", "🔧 Админка"}))
async def handle_admin_buttons(message: types.Message):
    user = get_user_info(message.from_user.id)
    is_courier = user[4] if user else 0
    
    if message.from_user.id not in ADMINS and is_courier == 0:
        kb = get_main_menu(message.from_user.id)
        await message.answer("⛔ У вас нет доступа к этому разделу.", reply_markup=kb)
        return

    if message.text == "📦 Доступные заказы":
        orders = get_orders_by_status('new')
        if not orders:
            await message.answer("📦 Новых доступных заказов нет.")
            return
        
        for oid, uid, items, total, date_str, username in orders:
            client_mention = f"@{username}" if username else f"`{uid}`"
            text = (
                f"📦 **Заказ #{oid}**\n"
                f"👤 Клиент: {client_mention} (ID: `{uid}`)\n"
                f"🛒 Состав: {items}\n"
                f"💰 Сумма: **{total} руб.**\n"
                f"🕒 Время: {date_str}"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚚 Взять заказ", callback_data=f"take_order_{oid}")]
            ])
            await message.answer(text, reply_markup=kb, parse_mode="Markdown")

    elif message.text == "🚚 Взятые заказы":
        orders = get_user_taken_orders(message.from_user.id)
        if not orders:
            await message.answer("🚚 У вас нет активных взятых заказов.")
            return
        
        for oid, uid, items, total, date_str, username in orders:
            client_mention = f"@{username}" if username else f"`{uid}`"
            text = (
                f"🚚 **Заказ в работе #{oid}**\n"
                f"👤 Клиент: {client_mention} (ID: `{uid}`)\n"
                f"🛒 Состав: {items}\n"
                f"💰 Сумма: **{total} руб.**"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Завершить заказ", callback_data=f"complete_order_{oid}")]
            ])
            await message.answer(text, reply_markup=kb, parse_mode="Markdown")

    elif message.text == "🗄 История заказов":
        orders = get_orders_by_status('completed')
        if not orders:
            await message.answer("🗄 История выполненных заказов пуста.")
            return
        
        text = "🗄 **Выполненные заказы:**\n\n"
        for oid, uid, items, total, date_str, username in orders[:10]:
            client_mention = f"@{username}" if username else f"`{uid}`"
            text += f"▪️ **Заказ #{oid}** | Клиент: {client_mention} | {total} руб.\n"
        await message.answer(text, parse_mode="Markdown")

    elif "Админка" in message.text:
        if message.from_user.id in ADMINS:
            await message.answer("🔧 **Панель Степы**", reply_markup=admin_panel_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("take_order_"))
async def process_take_order(callback: types.CallbackQuery):
    oid = int(callback.data.split("_")[2])
    take_order(oid, callback.from_user.id)
    await callback.message.edit_text(f"🚚 Вы взяли **Заказ #{oid}** в работу!", parse_mode="Markdown")

@dp.callback_query(F.data.startswith("complete_order_"))
async def process_complete_order(callback: types.CallbackQuery):
    oid = int(callback.data.split("_")[2])
    complete_order(oid)
    await callback.message.edit_text(f"✅ **Заказ #{oid}** помечен как выполненный!", parse_mode="Markdown")
    
    order = get_order_by_id(oid)
    if order:
        client_id = order[1]
        courier_id = callback.from_user.id
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ 1", callback_data=f"rate_{oid}_{courier_id}_1"),
                InlineKeyboardButton(text="⭐ 2", callback_data=f"rate_{oid}_{courier_id}_2"),
                InlineKeyboardButton(text="⭐ 3", callback_data=f"rate_{oid}_{courier_id}_3"),
                InlineKeyboardButton(text="⭐ 4", callback_data=f"rate_{oid}_{courier_id}_4"),
                InlineKeyboardButton(text="⭐ 5", callback_data=f"rate_{oid}_{courier_id}_5"),
            ]
        ])
        try:
            await bot.send_message(
                client_id, 
                f"🎉 **Ваш заказ #{oid} успешно доставлен!**\nПожалуйста, оцените работу курьера:", 
                reply_markup=kb,
                parse_mode="Markdown"
            )
        except Exception:
            pass

@dp.callback_query(F.data.startswith("rate_"))
async def process_rate_courier(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    oid = int(parts[1])
    courier_id = int(parts[2])
    rating = int(parts[3])
    
    await state.update_data(order_id=oid, courier_id=courier_id, rating=rating)
    await callback.message.edit_text(f"Спасибо за оценку ({'⭐' * rating})!\n\n**Напишите короткий отзыв о доставке** (или отправьте '-', чтобы пропустить):", parse_mode="Markdown")
    await state.set_state(ReviewSG.comment)

@dp.message(ReviewSG.comment)
async def process_review_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    comment = "" if message.text.strip() == "-" else message.text.strip()
    
    add_review(data['order_id'], message.from_user.id, data['courier_id'], data['rating'], comment)
    await message.answer("❤️ Спасибо за ваш отзыв! Он поможет сделать сервис лучше.")
    await state.clear()

# --- КАТАЛОГ И КОРЗИНА ---

@dp.callback_query(F.data.startswith("cat_"))
async def process_category_click(callback: types.CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    products = get_products_by_category(cat_id)
    
    if not products:
        await callback.message.answer("📦 В этой категории пока нет товаров.")
        await callback.answer()
        return

    await callback.message.answer("👇 **Список товаров в категории:**", parse_mode="Markdown")
    for prod_id, name, desc, price, photo_id in products:
        caption = f"📦 **{name}**\n\n{desc}\n\n💰 **Цена:** {price} руб."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 В корзину", callback_data=f"buy_{prod_id}")]
        ])
        if photo_id:
            await callback.message.answer_photo(photo=photo_id, caption=caption, reply_markup=kb, parse_mode="Markdown")
        else:
            await callback.message.answer(caption, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def process_add_to_cart(callback: types.CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    add_to_cart(callback.from_user.id, prod_id)
    await callback.answer("✅ Товар добавлен в корзину!", show_alert=True)

# --- АДМИН-ФУНКЦИИ ---

@dp.callback_query(F.data == "admin_add_cat")
async def start_add_cat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    await callback.message.edit_text("Введите **название новой категории**:")
    await state.set_state(AdminSG.add_cat_name)

@dp.message(AdminSG.add_cat_name)
async def process_add_cat(message: types.Message, state: FSMContext):
    add_category(message.text)
    await message.answer(f"✅ Категория **{message.text}** создана!", parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data == "admin_rename_cat")
async def start_rename_cat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    cats = get_categories()
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"rencat_{cid}")] for cid, name in cats]
    await callback.message.edit_text("Выберите категорию для переименования:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(AdminSG.rename_cat_select)

@dp.callback_query(F.data.startswith("rencat_"), AdminSG.rename_cat_select)
async def select_rename_cat(callback: types.CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    await state.update_data(cat_id=cat_id)
    await callback.message.edit_text("Введите **новое название категории**:")
    await state.set_state(AdminSG.rename_cat_name)

@dp.message(AdminSG.rename_cat_name)
async def process_rename_cat(message: types.Message, state: FSMContext):
    data = await state.get_data()
    rename_category(data['cat_id'], message.text)
    await message.answer(f"✅ Категория переименована в **{message.text}**!", parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data == "admin_del_cat")
async def start_del_cat(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    cats = get_categories()
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"delcat_{cid}")] for cid, name in cats]
    await callback.message.edit_text("Выберите категорию для **удаления**:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(AdminSG.del_cat_select)

@dp.callback_query(F.data.startswith("delcat_"), AdminSG.del_cat_select)
async def process_del_cat(callback: types.CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    delete_category(cat_id)
    await callback.message.edit_text("🗑 Категория и её товары удалены.")
    await state.clear()

@dp.callback_query(F.data == "admin_add_product")
async def start_add_product(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    cats = get_categories()
    buttons = [[InlineKeyboardButton(text=name, callback_data=f"addprodcat_{cid}")] for cid, name in cats]
    await callback.message.edit_text("Выберите категорию для нового товара:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await state.set_state(AdminSG.add_prod_cat)

@dp.callback_query(F.data.startswith("addprodcat_"), AdminSG.add_prod_cat)
async def process_prod_cat(callback: types.CallbackQuery, state: FSMContext):
    cat_id = int(callback.data.split("_")[1])
    await state.update_data(category_id=cat_id)
    await callback.message.edit_text("Введите **название товара**:")
    await state.set_state(AdminSG.add_prod_name)

@dp.message(AdminSG.add_prod_name)
async def process_prod_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите **описание товара**:")
    await state.set_state(AdminSG.add_prod_desc)

@dp.message(AdminSG.add_prod_desc)
async def process_prod_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("Введите **цену товара** (числом):")
    await state.set_state(AdminSG.add_prod_price)

@dp.message(AdminSG.add_prod_price)
async def process_prod_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text)
        await state.update_data(price=price)
        await message.answer("Отправьте **фотографию товара** (картинкой или документом):")
        await state.set_state(AdminSG.add_prod_photo)
    except ValueError:
        await message.answer("Пожалуйста, введите корректное число.")

@dp.message(AdminSG.add_prod_photo, F.photo | F.document)
async def process_prod_photo(message: types.Message, state: FSMContext):
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type.startswith('image/'):
        photo_id = message.document.file_id

    if not photo_id:
        await message.answer("Пожалуйста, отправьте изображение.")
        return

    data = await state.get_data()
    add_product_to_db(data['category_id'], data['name'], data['description'], data['price'], photo_id)
    await message.answer(f"✅ Товар **{data['name']}** успешно добавлен!", reply_markup=get_main_menu(message.from_user.id), parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data == "admin_give_balance")
async def start_give_balance(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    await callback.message.edit_text("Введите **Telegram ID** пользователя:")
    await state.set_state(AdminSG.give_balance_id)

@dp.message(AdminSG.give_balance_id)
async def process_give_balance_id(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text)
        await state.update_data(target_id=uid)
        await message.answer("Введите **сумму** пополнения:")
        await state.set_state(AdminSG.give_balance_amount)
    except ValueError:
        await message.answer("Введите корректный числовой ID.")

@dp.message(AdminSG.give_balance_amount)
async def process_give_balance_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text)
        data = await state.get_data()
        update_user_balance(data['target_id'], amount)
        await message.answer(f"✅ Баланс пользователя `{data['target_id']}` пополнен на **{amount} руб.**", parse_mode="Markdown")
        await state.clear()
    except ValueError:
        await message.answer("Введите корректное число.")

@dp.callback_query(F.data == "admin_add_courier")
async def start_add_courier(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    await callback.message.edit_text("Введите **Telegram ID** нового курьера:")
    await state.set_state(AdminSG.courier_add_id)

@dp.message(AdminSG.courier_add_id)
async def process_add_courier(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text)
        set_courier_status(uid, 1)
        await message.answer(f"✅ Пользователю `{uid}` назначены права курьера!", parse_mode="Markdown")
        await state.clear()
    except ValueError:
        await message.answer("Введите числовой ID.")

@dp.callback_query(F.data == "admin_del_courier")
async def start_del_courier(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    await callback.message.edit_text("Введите **Telegram ID** курьера для снятия прав:")
    await state.set_state(AdminSG.courier_del_id)

@dp.message(AdminSG.courier_del_id)
async def process_del_courier(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text)
        set_courier_status(uid, 0)
        await message.answer(f"✅ Права курьера у пользователя `{uid}` сняты!", parse_mode="Markdown")
        await state.clear()
    except ValueError:
        await message.answer("Введите числовой ID.")

@dp.callback_query(F.data == "admin_search_info")
async def start_search_info(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    await callback.message.edit_text("Введите **Telegram ID** пользователя для проверки:")
    await state.set_state(AdminSG.search_info_id)

@dp.message(AdminSG.search_info_id)
async def process_search_info(message: types.Message, state: FSMContext):
    try:
        uid = int(message.text)
        user = get_user_info(uid)
        if not user:
            await message.answer("❌ Пользователь не найден в базе.")
        else:
            orders_count = get_user_orders_count(uid)
            text = (
                f"🔍 **Информация о пользователе:**\n\n"
                f"👤 Username: @{user[1]}\n"
                f"🆔 ID: `{user[0]}`\n"
                f"💰 Баланс: {user[2]} руб.\n"
                f"⭐ Рейтинг: {user[3]}\n"
                f"🚚 Курьер: {'Да' if user[4] == 1 else 'Нет'}\n"
                f"📦 Заказов: {orders_count}"
            )
            await message.answer(text, parse_mode="Markdown")
        await state.clear()
    except ValueError:
        await message.answer("Введите корректный ID.")

@dp.callback_query(F.data == "admin_stats")
async def show_all_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMINS: return
    orders_count, total_sum, users_count = get_stats(day_only=False)
    text = (
        f"📊 **Общая статистика магазина:**\n\n"
        f"👥 Всего пользователей: **{users_count}**\n"
        f"📦 Выполнено заказов: **{orders_count}**\n"
        f"💰 Общая выручка: **{total_sum} руб.**"
    )
    await callback.message.edit_text(text, parse_mode="Markdown")

@dp.callback_query(F.data == "admin_stats_day")
async def show_day_stats(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMINS: return
    orders_count, total_sum, users_count = get_stats(day_only=True)
    text = (
        f"📈 **Статистика за сегодня:**\n\n"
        f"📦 Заказов за день: **{orders_count}**\n"
        f"💰 Выручка за день: **{total_sum} руб.**"
    )
    await callback.message.edit_text(text, parse_mode="Markdown")

@dp.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMINS: return
    await callback.message.edit_text("Отправьте сообщение (текст или фото с текстом) для рассылки всем пользователям:")
    await state.set_state(AdminSG.broadcast_msg)

@dp.message(AdminSG.broadcast_msg)
async def process_broadcast(message: types.Message, state: FSMContext):
    users = get_all_users_ids()
    sent, failed = 0, 0
    await message.answer("🚀 Рассылка запущена...")
    for uid in users:
        try:
            await message.copy_to(chat_id=uid)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await message.answer(f"✅ **Рассылка завершена!**\nУспешно: {sent}\nОшибок: {failed}", parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data == "admin_tech_work")
async def toggle_tech_work(callback: types.CallbackQuery):
    global TECH_WORKS
    if callback.from_user.id not in ADMINS: return
    TECH_WORKS = not TECH_WORKS
    status = "ВКЛЮЧЕНЫ ⛔" if TECH_WORKS else "ВЫКЛЮЧЕНЫ ✅"
    await callback.message.edit_text(f"🔧 Технические работы: **{status}**", parse_mode="Markdown")

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
