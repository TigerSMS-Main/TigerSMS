import os
import asyncio
import logging
import aiohttp
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# --- Configuration ---
BOT_TOKEN = "9026120942:AAFwZJ_q-fBM2g_KjTykECNkKmPA0eI6F2c"
ADMIN_ID = 7266067201
DB_PATH = "bot_database.db"

# --- Emoji Icons ---
EMOJI = {
    "key": "🔑",
    "tg": "✈️",
    "money": "🪙",
    "success": "🟢",
    "pending": "⏳",
    "danger": "🔴",
    "primary": "🔵",
    "admin": "👑",
}

# --- Dummy Web Server (Render Port Binding) ---
async def handle_ping(request):
    return web.Response(text="Bot is running live 24/7 on Render!")

def create_web_app():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    return app

# --- FSM States ---
class Form(StatesGroup):
    waiting_for_api_key = State()
    waiting_for_amount = State()
    admin_add_country_id = State()
    admin_add_country_name = State()

# --- Tiger SMS API Client ---
class TigerSMS:
    BASE_URL = "https://api.tiger-sms.com/stubs/handler_api.php"

    @staticmethod
    async def get_balance(api_key: str):
        params = {"action": "getBalance", "api_key": api_key, "format": "json"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params) as resp:
                    data = await resp.json()
                    return data.get("balance")
            except Exception:
                return None

    @staticmethod
    async def buy_number(api_key: str, country_code: int):
        params = {
            "action": "getNumberV2",
            "api_key": api_key,
            "service": "tg",
            "country": country_code
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params) as resp:
                    data = await resp.json()
                    if "activationId" in data:
                        return True, data
                    return False, data.get("message", "NO_NUMBERS")
            except Exception as e:
                return False, str(e)

    @staticmethod
    async def get_status(api_key: str, activation_id: str):
        params = {"action": "getStatusV2", "api_key": api_key, "id": activation_id}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params) as resp:
                    return await resp.json()
            except Exception:
                return None

    @staticmethod
    async def set_status(api_key: str, activation_id: str, status: int):
        params = {"action": "setStatusV2", "api_key": api_key, "id": activation_id, "status": status}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params) as resp:
                    return await resp.json()
            except Exception:
                return None

# --- Database Layer ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                api_key TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS countries (
                country_id INTEGER PRIMARY KEY,
                name TEXT
            )
        """)
        await db.commit()

async def get_user_key(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT api_key FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_user_key(user_id: int, api_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO users (user_id, api_key) VALUES (?, ?)", (user_id, api_key))
        await db.commit()

async def get_all_countries():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT country_id, name FROM countries") as cursor:
            return await cursor.fetchall()

async def add_country(country_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO countries (country_id, name) VALUES (?, ?)", (country_id, name))
        await db.commit()

async def remove_country(country_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM countries WHERE country_id = ?", (country_id,))
        await db.commit()

# --- Colorful Reply Keyboard (Real Green, Blue, Red Buttons) ---
def get_main_reply_keyboard(is_admin: bool = False):
    builder = ReplyKeyboardBuilder()
    
    # Row 1: Green (Success) & Blue (Primary)
    builder.add(
        types.KeyboardButton(text="⚡ Buy Numbers", style="success"),
        types.KeyboardButton(text="💰 Balance", style="primary")
    )
    # Row 2: Blue (Primary) & Green (Success)
    builder.add(
        types.KeyboardButton(text="🔄 Change API Key", style="primary"),
        types.KeyboardButton(text="👤 Profile", style="success")
    )
    if is_admin:
        builder.add(
            types.KeyboardButton(text="👑 Admin Console", style="danger")
        )
    builder.adjust(2, 2, 1 if is_admin else 0)
    return builder.as_markup(resize_keyboard=True)

# --- Dispatcher Setup ---
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN)

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    api_key = await get_user_key(user_id)
    countries = await get_all_countries()

    if user_id == ADMIN_ID and not countries:
        text = (
            f"{EMOJI['admin']} <b>Welcome Owner!</b>\n\n"
            f"{EMOJI['danger']} <b>Status:</b> No countries added to database yet.\n"
            "Please type /admin or click <b>👑 Admin Console</b> below to add countries."
        )
        await message.answer(text, reply_markup=get_main_reply_keyboard(True), parse_mode="HTML")
        return

    if not api_key:
        text = (
            f"{EMOJI['key']} <b>Welcome to Tiger SMS Bot!</b>\n\n"
            f"{EMOJI['danger']} <b>Status:</b> API Key Missing\n\n"
            "Please click <b>🔄 Change API Key</b> below to set your Tiger SMS API Key."
        )
    else:
        text = (
            f"{EMOJI['tg']} <b>Tiger SMS Telegram Service</b>\n\n"
            f"{EMOJI['success']} <b>Status:</b> System Ready & Connected\n\n"
            "Select an action from the menu below:"
        )

    await message.answer(
        text, 
        reply_markup=get_main_reply_keyboard(user_id == ADMIN_ID), 
        parse_mode="HTML"
    )

# --- /admin Command & Menu ---
@dp.message(Command("admin"))
@dp.message(F.text == "👑 Admin Console")
async def cmd_admin(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID:
        return
    countries = await get_all_countries()
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Add New Country", callback_data="admin_add_country")
    if countries:
        builder.button(text="➖ Remove Country", callback_data="admin_rem_country")
    builder.button(text="❌ Close Console", callback_data="admin_close")
    builder.adjust(1)

    text = (
        f"{EMOJI['admin']} <b>Admin Control Console</b>\n\n"
        f"{EMOJI['primary']} <b>Status:</b> Admin Mode Active\n"
        f"🌐 <b>Configured Countries:</b> <code>{len(countries)}</code>"
    )
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_close")
async def close_admin(call: types.CallbackQuery):
    await call.message.delete()
    await call.message.answer(f"{EMOJI['success']} Admin console closed.")

# --- API Key Configuration ---
@dp.message(F.text == "🔄 Change API Key")
async def ask_api_key_msg(message: types.Message, state: FSMContext):
    await message.answer(
        f"{EMOJI['key']} Please send your <b>Tiger SMS API Key</b> here:",
        parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_api_key)

@dp.message(Form.waiting_for_api_key)
async def save_api_key(message: types.Message, state: FSMContext):
    key = message.text.strip()
    balance = await TigerSMS.get_balance(key)
    
    if balance is None:
        await message.answer(
            f"{EMOJI['danger']} <b>Authentication Failed!</b>\n"
            "Could not verify your API key with Tiger SMS. Please send a valid key:",
            parse_mode="HTML"
        )
        return

    await set_user_key(message.from_user.id, key)
    await state.clear()
    await message.answer(
        f"{EMOJI['success']} <b>API Key Saved Successfully!</b>\n\n"
        f"{EMOJI['money']} <b>Current Balance:</b> <code>${balance}</code>",
        reply_markup=get_main_reply_keyboard(message.from_user.id == ADMIN_ID),
        parse_mode="HTML"
    )

# --- Balance Check ---
@dp.message(F.text == "💰 Balance")
async def view_balance_msg(message: types.Message):
    api_key = await get_user_key(message.from_user.id)
    if not api_key:
        await message.answer(f"{EMOJI['danger']} Please set your API key first using <b>🔄 Change API Key</b>.")
        return

    balance = await TigerSMS.get_balance(api_key)
    text = (
        f"{EMOJI['money']} <b>Account Overview</b>\n\n"
        f"{EMOJI['primary']} <b>Provider:</b> Tiger SMS\n"
        f"💵 <b>Available Balance:</b> <code>${balance}</code>"
    )
    await message.answer(text, parse_mode="HTML")

# --- Profile ---
@dp.message(F.text == "👤 Profile")
async def view_profile_msg(message: types.Message):
    api_key = await get_user_key(message.from_user.id)
    status_badge = f"{EMOJI['success']} Connected" if api_key else f"{EMOJI['danger']} Not Connected"
    text = (
        f"👤 <b>User Profile Details</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{message.from_user.id}</code>\n"
        f"🔑 <b>API Status:</b> {status_badge}\n"
        f"👑 <b>Role:</b> {'Administrator' if message.from_user.id == ADMIN_ID else 'Standard User'}"
    )
    await message.answer(text, parse_mode="HTML")

# --- Step 1: Select Country ---
@dp.message(F.text == "⚡ Buy Numbers")
async def show_countries_msg(message: types.Message):
    api_key = await get_user_key(message.from_user.id)
    if not api_key:
        await message.answer(f"{EMOJI['danger']} Please set your API key first.")
        return

    countries = await get_all_countries()
    if not countries:
        await message.answer(f"{EMOJI['danger']} No countries available. Ask Admin to configure countries.")
        return

    builder = InlineKeyboardBuilder()
    for cid, name in countries:
        builder.button(text=f"🌐 {name}", callback_data=f"sel_c_{cid}_{name}")
    builder.adjust(2)

    await message.answer(
        f"{EMOJI['tg']} <b>Select Country for Telegram Activation:</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

# --- Step 2: Choose Quantity ---
@dp.callback_query(F.data.startswith("sel_c_"))
async def ask_quantity(call: types.CallbackQuery, state: FSMContext):
    _, _, cid, cname = call.data.split("_")
    await state.update_data(selected_cid=int(cid), selected_cname=cname)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="1 Number", callback_data="qty_1")
    builder.button(text="2 Numbers", callback_data="qty_2")
    builder.button(text="5 Numbers", callback_data="qty_5")
    builder.button(text="10 Numbers", callback_data="qty_10")
    builder.adjust(2, 2)

    await call.message.edit_text(
        f"{EMOJI['primary']} <b>Selected Country:</b> {cname} (ID: {cid})\n\n"
        f"👉 <b>How many numbers do you want to purchase?</b>\n"
        "Click a button below or type a custom number (e.g. 1, 3, 5):",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_amount)
    await call.answer()

@dp.callback_query(Form.waiting_for_amount, F.data.startswith("qty_"))
async def process_qty_button(call: types.CallbackQuery, state: FSMContext):
    amount = int(call.data.split("_")[1])
    await execute_bulk_buy(call.message, call.from_user.id, state, amount)
    await call.answer()

@dp.message(Form.waiting_for_amount)
async def process_qty_text(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer(f"{EMOJI['danger']} Please provide a valid number (e.g. 1, 2, 5):")
        return
    amount = int(message.text)
    if amount > 10:
        await message.answer(f"{EMOJI['danger']} Maximum 10 numbers allowed per order!")
        return
    await execute_bulk_buy(message, message.from_user.id, state, amount)

# --- Step 3: Purchase & Multi-Tracking ---
async def execute_bulk_buy(message: types.Message, user_id: int, state: FSMContext, amount: int):
    data = await state.get_data()
    cid = data["selected_cid"]
    cname = data["selected_cname"]
    await state.clear()

    api_key = await get_user_key(user_id)
    status_msg = await message.answer(
        f"{EMOJI['pending']} <b>Processing order for {amount}x number(s) ({cname})...</b>",
        parse_mode="HTML"
    )

    purchased = []
    for _ in range(amount):
        success, res = await TigerSMS.buy_number(api_key, cid)
        if success:
            purchased.append(res)
        await asyncio.sleep(0.3)

    if not purchased:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Try Again", callback_data="buy_menu_retry")
        await status_msg.edit_text(
            f"{EMOJI['danger']} <b>Purchase Failed: Out of stock or insufficient balance.</b>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        return

    await status_msg.edit_text(
        f"{EMOJI['success']} <b>Successfully allocated {len(purchased)} number(s)!</b>\n"
        "Tracking SMS codes live below...",
        parse_mode="HTML"
    )

    for item in purchased:
        act_id = item["activationId"]
        phone = item["phoneNumber"]
        cost = item.get("activationCost", "N/A")
        sub_msg = await message.answer(
            f"{EMOJI['pending']} <b>Waiting for SMS Code...</b>\n\n"
            f"📞 <b>Phone:</b> <code>+{phone}</code>\n"
            f"🌐 <b>Country:</b> {cname}\n"
            f"💰 <b>Cost:</b> ${cost}",
            parse_mode="HTML"
        )
        asyncio.create_task(poll_sms_loop(sub_msg, api_key, act_id, phone, cost, cname))

async def poll_sms_loop(msg: types.Message, api_key: str, act_id: str, phone: str, cost, country_name: str):
    for _ in range(30):
        await asyncio.sleep(4)
        status_data = await TigerSMS.get_status(api_key, act_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Cancel & Refund", callback_data=f"cancel_act_{act_id}")

        if status_data and status_data.get("sms"):
            code = status_data["sms"][0]["code"]
            await TigerSMS.set_status(api_key, act_id, 6)
            await msg.edit_text(
                f"{EMOJI['success']} <b>OTP DELIVERED!</b>\n\n"
                f"📞 <b>Phone:</b> <code>+{phone}</code>\n"
                f"💬 <b>OTP Code:</b> <code>{code}</code>\n"
                f"🏷 <b>Country:</b> {country_name}\n"
                f"💰 <b>Cost:</b> ${cost}\n\n"
                f"<i>Submit this code in your Telegram client.</i>",
                parse_mode="HTML"
            )
            return

        try:
            await msg.edit_text(
                f"{EMOJI['pending']} <b>WAITING FOR SMS...</b>\n\n"
                f"📞 <b>Phone:</b> <code>+{phone}</code>\n"
                f"🌐 <b>Country:</b> {country_name}\n"
                f"🆔 <b>Activation ID:</b> <code>{act_id}</code>\n"
                f"💰 <b>Cost:</b> ${cost}\n\n"
                "<i>Auto-refreshing status every few seconds...</i>",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await TigerSMS.set_status(api_key, act_id, 8)
    await msg.edit_text(
        f"{EMOJI['danger']} <b>ACTIVATION TIMEOUT!</b>\n"
        f"Phone: <code>+{phone}</code> was cancelled and refunded.",
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("cancel_act_"))
async def cancel_activation(call: types.CallbackQuery):
    act_id = call.data.split("_")[2]
    api_key = await get_user_key(call.from_user.id)
    await TigerSMS.set_status(api_key, act_id, 8)
    await call.message.edit_text(
        f"{EMOJI['danger']} <b>Activation Cancelled!</b>\n"
        "Credits refunded to your Tiger SMS balance.",
        parse_mode="HTML"
    )

# --- Admin Country Management ---
@dp.callback_query(F.data == "admin_add_country")
async def start_add_country(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.answer("Enter Tiger SMS <b>Country ID</b> (e.g. Russia=0, Brazil=7, USA=187):", parse_mode="HTML")
    await state.set_state(Form.admin_add_country_id)
    await call.answer()

@dp.message(Form.admin_add_country_id)
async def process_cid(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Please enter a valid numeric Country ID:")
        return
    await state.update_data(cid=int(message.text))
    await message.answer("Enter the Country <b>Name</b> (e.g. Brazil, USA):", parse_mode="HTML")
    await state.set_state(Form.admin_add_country_name)

@dp.message(Form.admin_add_country_name)
async def process_cname(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cid = data["cid"]
    cname = message.text.strip()
    await add_country(cid, cname)
    await state.clear()
    await message.answer(
        f"{EMOJI['success']} <b>Country Added!</b>\n{cname} (ID: {cid})",
        reply_markup=get_main_reply_keyboard(True),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "admin_rem_country")
async def list_remove_country(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    countries = await get_all_countries()
    builder = InlineKeyboardBuilder()
    for cid, name in countries:
        builder.button(text=f"🗑 {name}", callback_data=f"del_c_{cid}")
    builder.adjust(2)
    await call.message.edit_text("Select country to remove:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_c_"))
async def execute_remove_country(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    cid = int(call.data.split("_")[2])
    await remove_country(cid)
    await call.answer("Country deleted successfully!", show_alert=True)
    await call.message.delete()

# --- Main Entrypoint ---
async def main():
    await init_db()
    logging.info("Database initialized successfully.")

    # Render Port Binding
    port = int(os.environ.get("PORT", 10000))
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Render web server listening on port {port}")

    # Drop updates to avoid crash
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Starting Telegram Bot Polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Bot crashed: {e}", exc_info=True)
    finally:
        await runner.cleanup()
        await bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
    except Exception as e:
        print(f"FATAL ERROR: {e}")
