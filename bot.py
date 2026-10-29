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

# Brazil Country ID set to 73
FIXED_COUNTRY_ID = 73
FIXED_SERVICE = "tg"

# --- Render 24/7 Hosting Web Server ---
async def handle_ping(request):
    return web.Response(text="Bot is running live 24/7.")

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

# --- Tiger SMS API ---
class TigerSMS:
    BASE_URL = "https://api.tiger-sms.com/stubs/handler_api.php"

    @staticmethod
    async def get_balance(api_key: str):
        params = {"action": "getBalance", "api_key": api_key.strip(), "format": "json"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=10) as resp:
                    data = await resp.json()
                    return data.get("balance")
            except Exception:
                return None

    @staticmethod
    async def buy_number(api_key: str):
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

                    if raw_text.startswith("ACCESS_NUMBER"):
                        parts = raw_text.split(":")
                        return True, {
                            "activationId": parts[1],
                            "phoneNumber": parts[2]
                        }

                    if raw_text == "NO_NUMBERS":
                        return False, "Stock temporary empty. Try again in 1-2 minutes."
                    elif raw_text == "NO_BALANCE":
                        return False, "Insufficient balance."
                    elif raw_text == "BAD_KEY":
                        return False, "Invalid API key."

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
        params = {"action": "setStatus", "api_key": api_key.strip(), "id": str(activation_id), "status": str(status)}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=8) as resp:
                    return (await resp.text()).strip()
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

# --- Main Buttons Layout ---
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
                "<b>Welcome</b>\n\n"
                "Status: No API Key linked.\n"
                "Nicher <b>Change API Key</b> e click kore key add korun."
            )
        else:
            text = (
                "<b>Telegram Numbers Hub</b>\n\n"
                "Status: System Ready\n"
                "Nicher <b>Buy Numbers</b> e click korun."
            )

        await message.answer(
            text, 
            reply_markup=get_main_reply_keyboard(user_id == ADMIN_ID), 
            parse_mode="HTML"
        )

# --- Admin Console ---
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
            "<b>Admin Console</b>\n\n"
            f"Blocked Prefixes: <code>{bad_pref}</code>"
        )
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_close")
async def close_admin(call: types.CallbackQuery):
    await call.message.delete()

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
        f"<b>Admin Console</b>\n\nBlocked Prefixes: <code>{bad_pref}</code>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

# --- API Key Setup ---
@dp.message(F.text == "Change API Key")
async def ask_api_key_msg(message: types.Message, state: FSMContext):
    await message.answer("Tiger SMS API Key ekhane send korun:", parse_mode="HTML")
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
            await message.answer("Age API Key configure korun.")
            return

        balance = await TigerSMS.get_balance(api_key)
        await message.answer(f"Balance: <code>${balance}</code>", parse_mode="HTML")

@dp.message(F.text == "Profile")
async def view_profile_msg(message: types.Message):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        api_key = await get_user_key(message.from_user.id)
        status_text = "Connected" if api_key else "Not Configured"
        text = (
            "<b>Profile Details</b>\n\n"
            f"ID: <code>{message.from_user.id}</code>\n"
            f"Status: {status_text}"
        )
        await message.answer(text, parse_mode="HTML")

# --- Buy Flow ---
@dp.message(F.text == "Buy Numbers")
async def direct_ask_quantity(message: types.Message, state: FSMContext):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        api_key = await get_user_key(message.from_user.id)
        if not api_key:
            await message.answer("Age API Key configure korun.")
            return

        builder = InlineKeyboardBuilder()
        builder.button(text="1 Number", callback_data="qty_1")
        builder.button(text="2 Numbers", callback_data="qty_2")
        builder.button(text="5 Numbers", callback_data="qty_5")
        builder.button(text="10 Numbers", callback_data="qty_10")
        builder.button(text="Cancel", callback_data="order_close")
        builder.adjust(2, 2, 1)

        await message.answer(
            "Kotogula number buy korte chan select korun othoba quantity likhe pathan (1-10):",
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
        await message.answer("Please valid number amount pathan (1 to 10):")
        return
    amount = int(message.text)
    if amount > 10:
        await message.answer("Maximum limit holo 10 per batch.")
        return
    await execute_bulk_buy(message, message.from_user.id, state, amount)

# --- Buy Execution & SMS Loop ---
async def execute_bulk_buy(message: types.Message, user_id: int, state: FSMContext, amount: int):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        await state.clear()
        api_key = await get_user_key(user_id)

        bad_p_str = await get_setting("bad_prefix", "None")
        bad_prefixes = [p.strip() for p in bad_p_str.split(",") if p.strip()] if bad_pref_active(bad_p_str) else []

        status_msg = await message.answer(f"Allocating {amount}x number(s)...", parse_mode="HTML")

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
                    asyncio.create_task(delayed_refund(api_key, act_id))
                    retry_success, retry_res = await TigerSMS.buy_number(api_key)
                    if retry_success:
                        purchased.append(retry_res)
                else:
                    purchased.append(res)
            else:
                last_error = str(res)
            await asyncio.sleep(0.2)

        if not purchased:
            await status_msg.edit_text(f"Purchase Failed:\n<code>{last_error}</code>", parse_mode="HTML")
            return

        await status_msg.edit_text(f"Allocated {len(purchased)} number(s).")

        for item in purchased:
            act_id = item["activationId"]
            phone = item["phoneNumber"]

            builder = InlineKeyboardBuilder()
            builder.button(text="🔄 Check / Refresh", callback_data=f"ref_act_{act_id}_{phone}")
            builder.button(text="Cancel & Refund", callback_data=f"cancel_act_{act_id}")
            builder.adjust(1)

            sub_msg = await message.answer(
                f"Phone: <code>+{phone}</code>\n"
                "Status: Waiting for SMS...",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
            asyncio.create_task(poll_sms_loop(sub_msg, api_key, act_id, phone))

async def delayed_refund(api_key: str, act_id: str):
    await asyncio.sleep(125)
    await TigerSMS.set_status(api_key, act_id, 8)

def bad_pref_active(s: str) -> bool:
    return s not in ("None", "", "clear", "none")

async def poll_sms_loop(msg: types.Message, api_key: str, act_id: str, phone: str):
    for _ in range(240):
        await asyncio.sleep(5)
        status_data = await TigerSMS.get_status(api_key, act_id)

        if status_data and status_data.get("sms"):
            code = status_data["sms"]["code"]
            multi_btn = InlineKeyboardBuilder()
            multi_btn.button(text="Request Another SMS", callback_data=f"retry_act_{act_id}")
            multi_btn.button(text="Complete Activation", callback_data=f"finish_act_{act_id}")
            multi_btn.adjust(1)

            await msg.edit_text(
                f"Phone: <code>+{phone}</code>\n"
                f"OTP Code: <code>{code}</code>",
                reply_markup=multi_btn.as_markup(),
                parse_mode="HTML"
            )
            return

        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Check / Refresh", callback_data=f"ref_act_{act_id}_{phone}")
        builder.button(text="Cancel & Refund", callback_data=f"cancel_act_{act_id}")
        builder.adjust(1)

        try:
            await msg.edit_text(
                f"Phone: <code>+{phone}</code>\n"
                "Status: Waiting for SMS...",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await TigerSMS.set_status(api_key, act_id, 8)
    await msg.edit_text(f"Phone: <code>+{phone}</code> expired and refunded.", parse_mode="HTML")

# --- Manual Refresh Handler ---
@dp.callback_query(F.data.startswith("ref_act_"))
async def manual_refresh_sms(call: types.CallbackQuery):
    _, _, act_id, phone = call.data.split("_")
    api_key = await get_user_key(call.from_user.id)
    
    status_data = await TigerSMS.get_status(api_key, act_id)

    if status_data and status_data.get("sms"):
        code = status_data["sms"]["code"]
        multi_btn = InlineKeyboardBuilder()
        multi_btn.button(text="Request Another SMS", callback_data=f"retry_act_{act_id}")
        multi_btn.button(text="Complete Activation", callback_data=f"finish_act_{act_id}")
        multi_btn.adjust(1)

        await call.message.edit_text(
            f"Phone: <code>+{phone}</code>\n"
            f"OTP Code: <code>{code}</code>",
            reply_markup=multi_btn.as_markup(),
            parse_mode="HTML"
        )
        await call.answer("OTP code received!")
    else:
        await call.answer("SMS ekhono asheni, opekkha korun...", show_alert=False)

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
    res = await TigerSMS.set_status(api_key, act_id, 8)
    if res == "EARLY_CANCEL_DENIED":
        await call.answer("Early cancel denied! 2 minutes wait korte hobe.", show_alert=True)
    else:
        await call.message.edit_text("Cancelled and refunded.")

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
    logging.info(f"Render server running on port {port}")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Polling started...")
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
