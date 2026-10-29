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

# --- Dummy Web Server (Render 24/7 Port Binding) ---
async def handle_ping(request):
    return web.Response(text="Tiger SMS Bot running live 24/7 on Render!")

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
    admin_set_max_price = State()
    admin_set_bad_prefix = State()

# --- Tiger SMS API Client ---
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
    async def get_price_and_stock(api_key: str, country_code: int):
        params = {"action": "getPrices", "api_key": api_key, "service": "tg", "country": country_code}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=10) as resp:
                    data = await resp.json()
                    c_str = str(country_code)
                    if c_str in data and "tg" in data[c_str]:
                        cost = data[c_str]["tg"].get("cost", "N/A")
                        count = data[c_str]["tg"].get("count", 0)
                        return cost, count
            except Exception:
                pass
        return "N/A", 0

    @staticmethod
    async def buy_number(api_key: str, country_code: int, max_price: float = None):
        params = {
            "action": "getNumberV2",
            "api_key": api_key,
            "service": "tg",
            "country": country_code
        }
        if max_price and max_price > 0:
            params["maxPrice"] = max_price

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=15) as resp:
                    data = await resp.json()
                    if "activationId" in data:
                        return True, data
                    return False, data.get("message", "OUT_OF_STOCK")
            except Exception as e:
                return False, str(e)

    @staticmethod
    async def get_status(api_key: str, activation_id: str):
        params = {"action": "getStatusV2", "api_key": api_key, "id": activation_id}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=8) as resp:
                    return await resp.json()
            except Exception:
                return None

    @staticmethod
    async def set_status(api_key: str, activation_id: str, status: int):
        params = {"action": "setStatusV2", "api_key": api_key, "id": activation_id, "status": status}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(TigerSMS.BASE_URL, params=params, timeout=8) as resp:
                    return await resp.json()
            except Exception:
                return None

# --- Database Operations ---
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

async def get_setting(key: str, default: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

# --- Main Reply Keyboard[cite: 1] ---
def get_main_reply_keyboard(is_admin: bool = False):
    builder = ReplyKeyboardBuilder()
    builder.add(
        types.KeyboardButton(text="⚡ Buy Numbers", style="success"),
        types.KeyboardButton(text="💰 Balance", style="primary")
    )
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

# --- Bot & Dispatcher Setup ---
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN)

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        user_id = message.from_user.id
        api_key = await get_user_key(user_id)
        countries = await get_all_countries()

        if user_id == ADMIN_ID and not countries:
            text = (
                "👑 <b>Welcome Owner!</b>\n\n"
                "⚠️ <b>Status:</b> No countries added to database yet.\n"
                "Please click <b>👑 Admin Console</b> or run /admin to configure."
            )
            await message.answer(text, reply_markup=get_main_reply_keyboard(True), parse_mode="HTML")
            return

        if not api_key:
            text = (
                "🔑 <b>Welcome to Tiger SMS Ultra Bot!</b>\n\n"
                "🔴 <b>Status:</b> API Key Missing\n\n"
                "Please click <b>🔄 Change API Key</b> below to connect your Tiger SMS account."
            )
        else:
            text = (
                "✈️ <b>Tiger SMS Telegram Terminal</b>\n\n"
                "🟢 <b>Status:</b> Ready & Connected\n"
                "Select an option from the menu below:"
            )

        await message.answer(
            text, 
            reply_markup=get_main_reply_keyboard(user_id == ADMIN_ID), 
            parse_mode="HTML"
        )

# --- Admin Console (/admin) ---
@dp.message(Command("admin"))
@dp.message(F.text == "👑 Admin Console")
async def cmd_admin(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id != ADMIN_ID:
        return

    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        countries = await get_all_countries()
        max_p = await get_setting("max_price", "None")
        bad_pref = await get_setting("bad_prefix", "None")

        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Add Country", callback_data="admin_add_country")
        if countries:
            builder.button(text="➖ Remove Country", callback_data="admin_rem_country")
        builder.button(text="💵 Set Max Price Limit", callback_data="admin_set_max_p")
        builder.button(text="🚫 Set Bad Number Prefix", callback_data="admin_set_bad_p")
        builder.button(text="❌ Close Console", callback_data="admin_close")
        builder.adjust(1)

        text = (
            "👑 <b>Tiger SMS Admin Control Center</b>\n\n"
            f"🌐 <b>Active Countries:</b> <code>{len(countries)}</code>\n"
            f"💵 <b>Max Price Guard:</b> <code>${max_p}</code>\n"
            f"🚫 <b>Blocked Prefixes:</b> <code>{bad_pref}</code>\n\n"
            "<i>Numbers with blocked prefixes are automatically cancelled and refunded immediately.</i>"
        )
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_close")
async def close_admin(call: types.CallbackQuery):
    await call.message.delete()
    await call.message.answer("🟢 Admin console closed.")

@dp.callback_query(F.data == "admin_set_max_p")
async def set_max_p_prompt(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Enter maximum price cap in USD (e.g. <code>0.25</code>) or type <code>0</code> to disable:", parse_mode="HTML")
    await state.set_state(Form.admin_set_max_price)
    await call.answer()

@dp.message(Form.admin_set_max_price)
async def process_max_price(message: types.Message, state: FSMContext):
    val = message.text.strip()
    try:
        f_val = float(val)
        await set_setting("max_price", str(f_val) if f_val > 0 else "0")
        await state.clear()
        await message.answer(f"🟢 Max purchase price limit set to: <b>${f_val}</b>", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ Please enter a valid decimal number (e.g. 0.20):")

@dp.callback_query(F.data == "admin_set_bad_p")
async def set_bad_p_prompt(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer(
        "Enter comma-separated prefixes to block (e.g. <code>5511, 5521, 1873</code>) or type <code>clear</code> to remove:",
        parse_mode="HTML"
    )
    await state.set_state(Form.admin_set_bad_prefix)
    await call.answer()

@dp.message(Form.admin_set_bad_prefix)
async def process_bad_prefix(message: types.Message, state: FSMContext):
    val = message.text.strip().replace(" ", "")
    if val.lower() == "clear":
        await set_setting("bad_prefix", "None")
        await message.answer("🟢 Blocked prefix list cleared.")
    else:
        await set_setting("bad_prefix", val)
        await message.answer(f"🟢 Blocked prefixes updated: <code>{val}</code>", parse_mode="HTML")
    await state.clear()

# --- API Key Setup ---
@dp.message(F.text == "🔄 Change API Key")
async def ask_api_key_msg(message: types.Message, state: FSMContext):
    await message.answer("🔑 Send your <b>Tiger SMS API Key</b> here:", parse_mode="HTML")
    await state.set_state(Form.waiting_for_api_key)

@dp.message(Form.waiting_for_api_key)
async def save_api_key(message: types.Message, state: FSMContext):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        key = message.text.strip()
        balance = await TigerSMS.get_balance(key)

        if balance is None:
            await message.answer(
                "🔴 <b>Authentication Failed!</b>\nCould not verify key with Tiger SMS. Please try again:",
                parse_mode="HTML"
            )
            return

        await set_user_key(message.from_user.id, key)
        await state.clear()
        await message.answer(
            f"🟢 <b>API Key Saved!</b>\n\n💵 <b>Balance:</b> <code>${balance}</code>",
            reply_markup=get_main_reply_keyboard(message.from_user.id == ADMIN_ID),
            parse_mode="HTML"
        )

# --- Balance & Profile ---
@dp.message(F.text == "💰 Balance")
async def view_balance_msg(message: types.Message):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        api_key = await get_user_key(message.from_user.id)
        if not api_key:
            await message.answer("🔴 Please set your API key first.")
            return

        balance = await TigerSMS.get_balance(api_key)
        await message.answer(
            f"💵 <b>Tiger SMS Balance:</b> <code>${balance}</code>\n🟢 <b>Provider:</b> Online",
            parse_mode="HTML"
        )

@dp.message(F.text == "👤 Profile")
async def view_profile_msg(message: types.Message):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        api_key = await get_user_key(message.from_user.id)
        status_text = "🟢 Connected" if api_key else "🔴 Missing API Key"
        text = (
            "👤 <b>Account Overview</b>\n\n"
            f"🆔 <b>Telegram ID:</b> <code>{message.from_user.id}</code>\n"
            f"🔑 <b>API Status:</b> {status_text}\n"
            f"👑 <b>Access:</b> {'Administrator' if message.from_user.id == ADMIN_ID else 'Client'}"
        )
        await message.answer(text, parse_mode="HTML")

# --- Buy Flow: Real-time Price & Stock Display ---
@dp.message(F.text == "⚡ Buy Numbers")
async def show_countries_with_prices(message: types.Message):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        api_key = await get_user_key(message.from_user.id)
        if not api_key:
            await message.answer("🔴 Please set your API key first.")
            return

        countries = await get_all_countries()
        if not countries:
            await message.answer("🔴 No countries configured by admin yet.")
            return

        # Fetch prices concurrently for zero lag
        price_tasks = [TigerSMS.get_price_and_stock(api_key, cid) for cid, _ in countries]
        price_results = await asyncio.gather(*price_tasks)

        builder = InlineKeyboardBuilder()
        for (cid, name), (cost, count) in zip(countries, price_results):
            btn_text = f"🌐 {name} (${cost} | Stock: {count})"
            builder.button(text=btn_text, callback_data=f"sel_c_{cid}_{name}_{cost}")
        builder.adjust(1)

        await message.answer(
            "✈️ <b>Telegram Numbers Stock & Live Rates:</b>\n<i>Select country to proceed:</i>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("sel_c_"))
async def ask_quantity(call: types.CallbackQuery, state: FSMContext):
    _, _, cid, cname, cost = call.data.split("_")
    await state.update_data(selected_cid=int(cid), selected_cname=cname, selected_cost=cost)

    builder = InlineKeyboardBuilder()
    builder.button(text="1 Number", callback_data="qty_1")
    builder.button(text="2 Numbers", callback_data="qty_2")
    builder.button(text="5 Numbers", callback_data="qty_5")
    builder.button(text="10 Numbers", callback_data="qty_10")
    builder.adjust(2, 2)

    await call.message.edit_text(
        f"🌐 <b>Country:</b> {cname} (ID: {cid})\n"
        f"💵 <b>Rate:</b> ~${cost}/sms\n\n"
        "👉 <b>Select quantity or send custom count (1-10):</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_amount)
    await call.answer()

@dp.callback_query(Form.waiting_for_amount, F.data.startswith("qty_"))
async def process_qty_btn(call: types.CallbackQuery, state: FSMContext):
    amount = int(call.data.split("_")[1])
    await execute_bulk_buy(call.message, call.from_user.id, state, amount)
    await call.answer()

@dp.message(Form.waiting_for_amount)
async def process_qty_txt(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("⚠️ Please provide a valid numeric value (1 to 10):")
        return
    amount = int(message.text)
    if amount > 10:
        await message.answer("⚠️ Maximum 10 numbers allowed per batch.")
        return
    await execute_bulk_buy(message, message.from_user.id, state, amount)

# --- Execution, Prefix Filter, and Multi-SMS Tracking ---
async def execute_bulk_buy(message: types.Message, user_id: int, state: FSMContext, amount: int):
    async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
        data = await state.get_data()
        cid = data["selected_cid"]
        cname = data["selected_cname"]
        await state.clear()

        api_key = await get_user_key(user_id)
        max_p_str = await get_setting("max_price", "0")
        max_price = float(max_p_str) if max_p_str != "0" else None

        bad_p_str = await get_setting("bad_prefix", "None")
        bad_prefixes = [p.strip() for p in bad_p_str.split(",") if p.strip()] if bad_p_str != "None" else []

        status_msg = await message.answer(f"⏳ <b>Allocating {amount}x numbers for {cname}...</b>", parse_mode="HTML")

        purchased = []
        for _ in range(amount):
            success, res = await TigerSMS.buy_number(api_key, cid, max_price)
            if success:
                phone = str(res.get("phoneNumber", ""))
                act_id = str(res.get("activationId", ""))

                # Automatic Prefix Blacklist Check
                is_bad = any(phone.startswith(bad_p.lstrip("+")) for bad_p in bad_prefixes)
                if is_bad:
                    # Instantly cancel and refund bad serial
                    await TigerSMS.set_status(api_key, act_id, 8)
                    # Retry once with another number
                    retry_success, retry_res = await TigerSMS.buy_number(api_key, cid, max_price)
                    if retry_success:
                        purchased.append(retry_res)
                else:
                    purchased.append(res)
            await asyncio.sleep(0.1)

        if not purchased:
            await status_msg.edit_text(
                "🔴 <b>Purchase Failed:</b> Out of stock or exceeded maximum price cap.",
                parse_mode="HTML"
            )
            return

        await status_msg.edit_text(
            f"🟢 <b>Allocated {len(purchased)} number(s)!</b>\n"
            "Tracking SMS delivery live below (20 min expiry with auto-refund)...",
            parse_mode="HTML"
        )

        for item in purchased:
            act_id = item["activationId"]
            phone = item["phoneNumber"]
            cost = item.get("activationCost", "N/A")
            can_multi = item.get("canGetAnotherSms", False)

            sub_msg = await message.answer(
                f"⏳ <b>Waiting for Telegram SMS...</b>\n\n"
                f"📞 <b>Phone:</b> <code>+{phone}</code>\n"
                f"🌐 <b>Country:</b> {cname}\n"
                f"💵 <b>Price:</b> ${cost}\n"
                f"🔄 <b>Multi-SMS:</b> {'Yes' if can_multi else 'No'}",
                parse_mode="HTML"
            )
            asyncio.create_task(poll_sms_loop(sub_msg, api_key, act_id, phone, cost, cname, can_multi))

async def poll_sms_loop(msg: types.Message, api_key: str, act_id: str, phone: str, cost, country_name: str, can_multi: bool):
    # 20 minutes activation window: 240 polls * 5s = 1200s (20 mins)
    for _ in range(240):
        await asyncio.sleep(5)
        status_data = await TigerSMS.get_status(api_key, act_id)

        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Cancel & Refund", callback_data=f"cancel_act_{act_id}")

        if status_data and status_data.get("sms"):
            code = status_data["sms"][0]["code"]
            
            # If number supports multiple SMS, give Retry button; else complete activation
            multi_btn = InlineKeyboardBuilder()
            if can_multi:
                multi_btn.button(text="🔄 Request Another SMS", callback_data=f"retry_act_{act_id}")
            multi_btn.button(text="✅ Complete Activation", callback_data=f"finish_act_{act_id}")
            multi_btn.adjust(1)

            await msg.edit_text(
                f"🟢 <b>OTP RECEIVED!</b>\n\n"
                f"📞 <b>Phone:</b> <code>+{phone}</code>\n"
                f"💬 <b>Telegram Code:</b> <code>{code}</code>\n"
                f"🌐 <b>Country:</b> {country_name}\n"
                f"💵 <b>Cost:</b> ${cost}\n\n"
                "<i>Click below to finalize or request another SMS if needed:</i>",
                reply_markup=multi_btn.as_markup(),
                parse_mode="HTML"
            )
            return

        try:
            await msg.edit_text(
                f"⏳ <b>Awaiting SMS Code...</b>\n\n"
                f"📞 <b>Phone:</b> <code>+{phone}</code>\n"
                f"🌐 <b>Country:</b> {country_name}\n"
                f"🆔 <b>Activation ID:</b> <code>{act_id}</code>\n"
                f"💵 <b>Cost:</b> ${cost}\n\n"
                "<i>Auto-updating every 5 seconds...</i>",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    # 20-minute timeout: Auto-cancel and refund (status=8)
    await TigerSMS.set_status(api_key, act_id, 8)
    await msg.edit_text(
        f"🔴 <b>AUTO-REFUNDED (20 MIN TIMEOUT)</b>\n"
        f"Phone: <code>+{phone}</code> received no code. Balance refunded to Tiger SMS.",
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("retry_act_"))
async def retry_sms(call: types.CallbackQuery):
    act_id = call.data.split("_")[2]
    api_key = await get_user_key(call.from_user.id)
    await TigerSMS.set_status(api_key, act_id, 3) # Request retry SMS
    await call.answer("Requested another SMS! Waiting for new code...", show_alert=True)

@dp.callback_query(F.data.startswith("finish_act_"))
async def finish_activation(call: types.CallbackQuery):
    act_id = call.data.split("_")[2]
    api_key = await get_user_key(call.from_user.id)
    await TigerSMS.set_status(api_key, act_id, 6) # Complete
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Activation marked as completed!", show_alert=True)

@dp.callback_query(F.data.startswith("cancel_act_"))
async def cancel_activation(call: types.CallbackQuery):
    act_id = call.data.split("_")[2]
    api_key = await get_user_key(call.from_user.id)
    await TigerSMS.set_status(api_key, act_id, 8)
    await call.message.edit_text(
        "🔴 <b>Activation Cancelled!</b>\nFunds refunded to your Tiger SMS balance.",
        parse_mode="HTML"
    )

# --- Admin Country Add/Remove ---
@dp.callback_query(F.data == "admin_add_country")
async def start_add_country(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.answer("Enter numeric Country ID (e.g. Russia=0, Brazil=7, USA=187):", parse_mode="HTML")
    await state.set_state(Form.admin_add_country_id)
    await call.answer()

@dp.message(Form.admin_add_country_id)
async def process_cid(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Enter a valid number:")
        return
    await state.update_data(cid=int(message.text))
    await message.answer("Enter Country Name (e.g. Brazil, USA):", parse_mode="HTML")
    await state.set_state(Form.admin_add_country_name)

@dp.message(Form.admin_add_country_name)
async def process_cname(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cid = data["cid"]
    cname = message.text.strip()
    await add_country(cid, cname)
    await state.clear()
    await message.answer(
        f"🟢 <b>Country Added!</b>\n{cname} (ID: {cid})",
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
    await call.message.edit_text("Select country to delete:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_c_"))
async def execute_remove_country(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    cid = int(call.data.split("_")[2])
    await remove_country(cid)
    await call.answer("Country removed!", show_alert=True)
    await call.message.delete()

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
    logging.info(f"Render web server listening on port {port}")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Starting Telegram Bot Polling...")
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
