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
from aiogram.utils.chat_action import ChatActionSender

# --- Configuration ---
BOT_TOKEN = "9026120942:AAFwZJ_q-fBM2g_KjTykECNkKmPA0eI6F2c"
ADMIN_ID = 7266067201
DB_PATH = "bot_database.db"

# Fixed: Brazil Telegram (+55)
FIXED_COUNTRY_ID = 7
FIXED_SERVICE = "tg"

# --- Render 24/7 Web Server ---
async def handle_ping(request):
    return web.Response(text="Tiger SMS Bot is running live 24/7.")

def create_web_app():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    return app

# --- FSM States ---
class Form(StatesGroup):
    waiting_for_api_key = State()
    waiting_for_amount = State()
    admin_set_bad_prefix = State()

# --- Tiger SMS API (Pure Lowest Rate Buy Engine) ---
class TigerSMS:
    BASE_URL = "https://api.tiger-sms.com/stubs/handler_api.php"

    @staticmethod
    async def get_balance(api_key: str):
        params = {"action": "getBalance", "api_key": api_key, "format": "json"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=10) as resp:
                    data = await resp.json()
                    return data.get("balance")
            except Exception:
                return None

    @staticmethod
    async def buy_number(api_key: str):
        # Kono maxPrice parameter nai -> API auto lowest price-e number nibe
        params = {
            "action": "getNumber",
            "api_key": api_key.strip(),
            "service": FIXED_SERVICE,
            "country": str(FIXED_COUNTRY_ID)
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=15) as resp:
                    raw_text = (await resp.text()).strip()

                    # 1. Standard Success Response: ACCESS_NUMBER:$id:$number
                    if "ACCESS_NUMBER" in raw_text:
                        parts = raw_text.split(":")
                        return True, {
                            "activationId": parts[1],
                            "phoneNumber": parts[2]
                        }

                    # 2. JSON check (jodi kono error JSON hishebe ashe)
                    try:
                        import json
                        data = json.loads(raw_text)
                        if "activationId" in data:
                            return True, {
                                "activationId": str(data["activationId"]),
                                "phoneNumber": str(data["phoneNumber"])
                            }
                        err_msg = data.get("details") or data.get("message") or data.get("title") or str(data)
                        return False, err_msg
                    except Exception:
                        pass

                    # 3. Plain text error checks
                    if raw_text == "NO_NUMBERS":
                        return False, "Stock temporary empty. Please try again in 1-2 minutes."
                    elif raw_text == "NO_BALANCE":
                        return False, "Insufficient balance in your Tiger SMS account."
                    elif raw_text == "BAD_KEY":
                        return False, "Invalid API Key. Please re-enter your key."

                    return False, raw_text
            except Exception as e:
                return False, str(e)

    @staticmethod
    async def get_status(api_key: str, activation_id: str):
        params = {"action": "getStatusV2", "api_key": api_key.strip(), "id": str(activation_id)}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=8) as resp:
                    return await resp.json()
            except Exception:
                return None

    @staticmethod
    async def set_status(api_key: str, activation_id: str, status: int):
        params = {"action": "setStatusV2", "api_key": api_key.strip(), "id": str(activation_id), "status": status}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=8) as resp:
                    return await resp.json()
            except Exception:
                return None

# --- Database ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                api_key TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
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

async def get_setting(key: str, default: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

# --- Main Navigation Keyboard ---
def get_main_reply_keyboard(is_admin: bool = False):
    builder = ReplyKeyboardBuilder()
    builder.add(
        types.KeyboardButton(text="Buy Numbers", style="success"),
        types.KeyboardButton(text="Balance", style="primary")
    )
    builder.add(
        types.KeyboardButton(text="Change API Key", style="primary"),
        types.KeyboardButton(text="Profile", style="success")
    )
    if is_admin:
        builder.add(
            types.KeyboardButton(text="Admin Console", style="danger")
        )
    builder.adjust(2, 2, 1 if is_admin else 0)
    return builder.as_markup(resize_keyboard=True)

# --- Dispatcher & Bot ---
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN)

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        user_id = message.from_user.id
        api_key = await get_user_key(user_id)

        if not api_key:
            text = (
                "<b>Welcome to Tiger SMS Service</b>\n\n"
                "Target: <b>Brazil Telegram (+55)</b>\n"
                "Status: No API Key linked.\n"
                "Please click <b>Change API Key</b> below to connect your account."
            )
        else:
            text = (
                "<b>Tiger SMS Telegram Terminal</b>\n\n"
                "Service: <b>Brazil Telegram (+55)</b>\n"
                "Mode: <b>Lowest Rate Auto-Capture</b>\n"
                "Status: System Ready\n\n"
                "Click <b>Buy Numbers</b> to purchase."
            )

        await message.answer(
            text, 
            reply_markup=get_main_reply_keyboard(user_id == ADMIN_ID), 
            parse_mode="HTML"
        )

# --- Admin Panel (/admin) ---
@dp.message(Command("admin"))
@dp.message(F.text == "Admin Console")
async def cmd_admin(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID:
        return

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        bad_pref = await get_setting("bad_prefix", "None")

        builder = InlineKeyboardBuilder()
        builder.button(text="Set Blocked Prefixes", callback_data="admin_set_bad_p")
        builder.button(text="Close Console", callback_data="admin_close")
        builder.adjust(1)

        text = (
            "<b>Admin Configuration Center</b>\n\n"
            "Active Country: <b>Brazil (ID: 7)</b>\n"
            "Service: <b>Telegram (tg)</b>\n"
            f"Blocked Prefixes: <code>{bad_pref}</code>"
        )
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_close")
async def close_admin(call: types.CallbackQuery):
    await call.message.delete()
    await call.message.answer("Returned to main terminal.")

@dp.callback_query(F.data == "admin_set_bad_p")
async def set_bad_p_prompt(call: types.CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data="admin_back")
    await call.message.edit_text(
        "Enter comma-separated prefixes to auto-refund (e.g. <code>5511, 5521</code>) or send <code>clear</code> to disable:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(Form.admin_set_bad_prefix)
    await call.answer()

@dp.message(Form.admin_set_bad_prefix)
async def process_bad_prefix(message: types.Message, state: FSMContext):
    val = message.text.strip().replace(" ", "")
    if val.lower() == "clear":
        await set_setting("bad_prefix", "None")
        await message.answer("Blocked prefix list cleared.")
    else:
        await set_setting("bad_prefix", val)
        await message.answer(f"Blocked prefixes updated: <code>{val}</code>", parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data == "admin_back")
async def admin_back_btn(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    bad_pref = await get_setting("bad_prefix", "None")

    builder = InlineKeyboardBuilder()
    builder.button(text="Set Blocked Prefixes", callback_data="admin_set_bad_p")
    builder.button(text="Close Console", callback_data="admin_close")
    builder.adjust(1)

    await call.message.edit_text(
        "<b>Admin Configuration Center</b>\n\n"
        "Active Country: <b>Brazil (ID: 7)</b>\n"
        "Service: <b>Telegram (tg)</b>\n"
        f"Blocked Prefixes: <code>{bad_pref}</code>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

# --- API Key Management ---
@dp.message(F.text == "Change API Key")
async def ask_api_key_msg(message: types.Message, state: FSMContext):
    await message.answer("Send your Tiger SMS API Key below:", parse_mode="HTML")
    await state.set_state(Form.waiting_for_api_key)

@dp.message(Form.waiting_for_api_key)
async def save_api_key(message: types.Message, state: FSMContext):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        key = message.text.strip()
        balance = await TigerSMS.get_balance(key)

        if balance is None:
            await message.answer("Authentication Failed. Check key and try again:", parse_mode="HTML")
            return

        await set_user_key(message.from_user.id, key)
        await state.clear()
        await message.answer(
            f"API Key Saved Successfully.\nBalance: <code>${balance}</code>",
            reply_markup=get_main_reply_keyboard(message.from_user.id == ADMIN_ID),
            parse_mode="HTML"
        )

# --- Balance & Profile ---
@dp.message(F.text == "Balance")
async def view_balance_msg(message: types.Message):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        api_key = await get_user_key(message.from_user.id)
        if not api_key:
            await message.answer("Please configure your API Key first.")
            return

        balance = await TigerSMS.get_balance(api_key)
        await message.answer(f"Tiger SMS Balance: <code>${balance}</code>", parse_mode="HTML")

@dp.message(F.text == "Profile")
async def view_profile_msg(message: types.Message):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        api_key = await get_user_key(message.from_user.id)
        status_text = "Connected" if api_key else "Not Configured"
        text = (
            "<b>Account Details</b>\n\n"
            f"User ID: <code>{message.from_user.id}</code>\n"
            f"API Status: {status_text}\n"
            f"Role: {'Administrator' if message.from_user.id == ADMIN_ID else 'Standard User'}"
        )
        await message.answer(text, parse_mode="HTML")

# --- Direct Buy Flow: Brazil Telegram (+55) ---
@dp.message(F.text == "Buy Numbers")
async def direct_ask_quantity(message: types.Message, state: FSMContext):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        api_key = await get_user_key(message.from_user.id)
        if not api_key:
            await message.answer("Please configure your API Key first.")
            return

        builder = InlineKeyboardBuilder()
        builder.button(text="1 Number", callback_data="qty_1")
        builder.button(text="2 Numbers", callback_data="qty_2")
        builder.button(text="5 Numbers", callback_data="qty_5")
        builder.button(text="10 Numbers", callback_data="qty_10")
        builder.button(text="Cancel", callback_data="order_close")
        builder.adjust(2, 2, 1)

        await message.answer(
            "Service: <b>Brazil Telegram (+55)</b>\n"
            "Pricing: <b>Lowest Rate Auto-Selected</b>\n\n"
            "Select quantity or send custom count (1-10):",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await state.set_state(Form.waiting_for_amount)

@dp.callback_query(F.data == "order_close")
async def close_order_menu(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.delete()

@dp.callback_query(Form.waiting_for_amount, F.data.startswith("qty_"))
async def process_qty_btn(call: types.CallbackQuery, state: FSMContext):
    amount = int(call.data.split("_")[1])
    await execute_bulk_buy(call.message, call.from_user.id, state, amount)
    await call.answer()

@dp.message(Form.waiting_for_amount)
async def process_qty_txt(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("Please specify a valid numeric amount (1 to 10):")
        return
    amount = int(message.text)
    if amount > 10:
        await message.answer("Maximum limit is 10 numbers per batch.")
        return
    await execute_bulk_buy(message, message.from_user.id, state, amount)

# --- Order Execution Engine ---
async def execute_bulk_buy(message: types.Message, user_id: int, state: FSMContext, amount: int):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        await state.clear()
        api_key = await get_user_key(user_id)

        bad_p_str = await get_setting("bad_prefix", "None")
        bad_prefixes = [p.strip() for p in bad_p_str.split(",") if p.strip()] if bad_pref_active(bad_p_str) else []

        status_msg = await message.answer(f"Connecting to Tiger SMS at lowest rate for {amount}x number(s)...", parse_mode="HTML")

        purchased = []
        last_error = ""

        for _ in range(amount):
            success, res = await TigerSMS.buy_number(api_key)
            if success:
                phone = str(res.get("phoneNumber", ""))
                act_id = str(res.get("activationId", ""))

                # Check Prefix Blacklist
                is_bad = any(phone.startswith(bad_p.lstrip("+")) for bad_p in bad_prefixes)
                if is_bad:
                    # Cancel bad prefix instantly
                    await TigerSMS.set_status(api_key, act_id, 8)
                    # Retry allocation once
                    retry_success, retry_res = await TigerSMS.buy_number(api_key)
                    if retry_success:
                        purchased.append(retry_res)
                else:
                    purchased.append(res)
            else:
                last_error = str(res)
            await asyncio.sleep(0.2)

        if not purchased:
            await status_msg.edit_text(f"Purchase Failed: <code>{last_error or 'Out of stock'}</code>", parse_mode="HTML")
            return

        await status_msg.edit_text(
            f"Successfully allocated {len(purchased)} Brazil Telegram number(s).\n"
            "SMS codes will be tracked below (20 min window with instant refund):"
        )

        for item in purchased:
            act_id = item["activationId"]
            phone = item["phoneNumber"]

            sub_msg = await message.answer(
                f"Waiting for SMS\n\n"
                f"Phone: <code>+{phone}</code>\n"
                f"Country: Brazil (+55)\n"
                f"Status: Active",
                parse_mode="HTML"
            )
            asyncio.create_task(poll_sms_loop(sub_msg, api_key, act_id, phone))

def bad_pref_active(s: str) -> bool:
    return s not in ("None", "", "clear", "none")

async def poll_sms_loop(msg: types.Message, api_key: str, act_id: str, phone: str):
    # 20 minutes window: 240 polls * 5 sec = 1200 sec
    for _ in range(240):
        await asyncio.sleep(5)
        status_data = await TigerSMS.get_status(api_key, act_id)

        builder = InlineKeyboardBuilder()
        builder.button(text="Cancel & Refund", callback_data=f"cancel_act_{act_id}")

        if status_data and status_data.get("sms"):
            code = status_data["sms"][0]["code"]
            multi_btn = InlineKeyboardBuilder()
            multi_btn.button(text="Request Another SMS", callback_data=f"retry_act_{act_id}")
            multi_btn.button(text="Complete Activation", callback_data=f"finish_act_{act_id}")
            multi_btn.adjust(1)

            await msg.edit_text(
                f"<b>SMS Code Received</b>\n\n"
                f"Phone: <code>+{phone}</code>\n"
                f"Code: <code>{code}</code>\n"
                f"Country: Brazil (+55)",
                reply_markup=multi_btn.as_markup(),
                parse_mode="HTML"
            )
            return

        try:
            await msg.edit_text(
                f"Waiting for SMS\n\n"
                f"Phone: <code>+{phone}</code>\n"
                f"Country: Brazil (+55)\n"
                f"ID: <code>{act_id}</code>\n\n"
                "Auto-refreshing status...",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    # 20 minute auto-cancel and refund
    await TigerSMS.set_status(api_key, act_id, 8)
    await msg.edit_text(f"Activation Expired. Phone +{phone} was auto-refunded to balance.")

@dp.callback_query(F.data.startswith("retry_act_"))
async def retry_sms(call: types.CallbackQuery):
    act_id = call.data.split("_")[2]
    api_key = await get_user_key(call.from_user.id)
    await TigerSMS.set_status(api_key, act_id, 3)
    await call.answer("Requested next SMS.", show_alert=True)

@dp.callback_query(F.data.startswith("finish_act_"))
async def finish_activation(call: types.CallbackQuery):
    act_id = call.data.split("_")[2]
    api_key = await get_user_key(call.from_user.id)
    await TigerSMS.set_status(api_key, act_id, 6)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Activation complete.", show_alert=True)

@dp.callback_query(F.data.startswith("cancel_act_"))
async def cancel_activation(call: types.CallbackQuery):
    act_id = call.data.split("_")[2]
    api_key = await get_user_key(call.from_user.id)
    await TigerSMS.set_status(api_key, act_id, 8)
    await call.message.edit_text("Activation cancelled. Credits refunded to balance.")

# --- Main Entrypoint ---
async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)

    port = int(os.environ.get("PORT", 10000))
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Render web server running on port {port}")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Telegram Bot Polling started...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Bot error: {e}", exc_info=True)
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
