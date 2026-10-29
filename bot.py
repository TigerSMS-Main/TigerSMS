import os
import asyncio
import logging
import aiohttp
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- কনফিগারেশন ---
BOT_TOKEN = "9026120942:AAFwZJ_q-fBM2g_KjTykECNkKmPA0eI6F2c"
ADMIN_ID = 7266067201
DB_PATH = "bot_database.db"

# বর্তমানে স্ট্যান্ডার্ড ইমোজি ব্যবহার করা হয়েছে যাতে ক্র্যাশ না করে।
# পরবর্তীতে প্রিমিয়াম আইডি পেলে: '<tg-emoji id="YOUR_ID">🟢</tg-emoji>' ফরম্যাটে বসাবে
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

# --- Render Port Binding Web Server ---
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

# --- Keyboards ---
def main_menu_keyboard(has_key: bool, is_admin: bool):
    builder = InlineKeyboardBuilder()
    if has_key:
        builder.button(text="⚡ Buy Telegram Number", callback_data="buy_menu")
        builder.button(text="💰 Check Balance", callback_data="check_balance")
        builder.button(text="🔄 Change API Key", callback_data="change_api_key")
    else:
        builder.button(text="🔑 Set API Key", callback_data="change_api_key")
    
    if is_admin:
        builder.button(text="⚙️ Admin Panel", callback_data="admin_panel")
        
    builder.adjust(1)
    return builder.as_markup()

# --- Dispatcher & Bot ---
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
            f"{EMOJI['admin']} <b>Owner Dashboard</b>\n\n"
            f"{EMOJI['danger']} <b>Status:</b> [🔴 NO COUNTRIES ADDED]\n\n"
            "ডাটাবেসে কোনো কান্ট্রি নেই। প্রথমে কান্ট্রি যোগ করুন।"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Add Country Now", callback_data="admin_add_country")
        builder.button(text="🏠 Main Menu", callback_data="main_menu")
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        return

    if not api_key:
        text = (
            f"{EMOJI['key']} <b>Tiger SMS Telegram Bot</b>\n\n"
            f"{EMOJI['danger']} <b>Status:</b> [🔴 API KEY MISSING]\n\n"
            "বটটি ব্যবহার করতে আপনার Tiger SMS API Key সেট করতে হবে।"
        )
    else:
        text = (
            f"{EMOJI['tg']} <b>Tiger SMS Telegram Bot</b>\n\n"
            f"{EMOJI['success']} <b>Status:</b> [🟢 SYSTEM READY]\n\n"
            "টেলিগ্রাম নম্বর কিনতে নিচের মেনু সিলেক্ট করুন:"
        )

    await message.answer(
        text, 
        reply_markup=main_menu_keyboard(bool(api_key), user_id == ADMIN_ID), 
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "main_menu")
async def back_main_menu(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    api_key = await get_user_key(call.from_user.id)
    text = (
        f"{EMOJI['tg']} <b>Tiger SMS Telegram Hub</b>\n\n"
        f"{EMOJI['success']} <b>Status:</b> [🟢 ACTIVE]"
    )
    await call.message.edit_text(
        text, 
        reply_markup=main_menu_keyboard(bool(api_key), call.from_user.id == ADMIN_ID), 
        parse_mode="HTML"
    )

# --- API Key Setup ---
@dp.callback_query(F.data == "change_api_key")
async def ask_api_key(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer(
        f"{EMOJI['key']} আপনার <b>Tiger SMS API Key</b> পেস্ট করে পাঠান:",
        parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_api_key)
    await call.answer()

@dp.message(Form.waiting_for_api_key)
async def save_api_key(message: types.Message, state: FSMContext):
    key = message.text.strip()
    balance = await TigerSMS.get_balance(key)
    
    if balance is None:
        await message.answer(
            f"{EMOJI['danger']} <b>Invalid API Key! [🔴 FAILED]</b>\n"
            "ব্যালেন্স ভেরিফাই করা যায়নি। সঠিক কী সেন্ড করুন:",
            parse_mode="HTML"
        )
        return

    await set_user_key(message.from_user.id, key)
    await state.clear()
    await message.answer(
        f"{EMOJI['success']} <b>API Key Saved! [🟢 SUCCESS]</b>\n\n"
        f"{EMOJI['money']} <b>Balance:</b> <code>${balance}</code>",
        reply_markup=main_menu_keyboard(True, message.from_user.id == ADMIN_ID),
        parse_mode="HTML"
    )

# --- Balance Check ---
@dp.callback_query(F.data == "check_balance")
async def view_balance(call: types.CallbackQuery):
    api_key = await get_user_key(call.from_user.id)
    balance = await TigerSMS.get_balance(api_key)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Back", callback_data="main_menu")
    
    text = (
        f"{EMOJI['money']} <b>Account Overview</b>\n\n"
        f"{EMOJI['primary']} <b>Status:</b> [🔵 CONNECTED]\n"
        f"💵 <b>Current Balance:</b> <code>${balance}</code>"
    )
    await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- Buy Telegram Number ---
@dp.callback_query(F.data == "buy_menu")
async def show_countries(call: types.CallbackQuery):
    countries = await get_all_countries()
    if not countries:
        await call.answer("কোনো দেশ যোগ করা হয়নি!", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for cid, name in countries:
        builder.button(text=f"🌐 {name}", callback_data=f"buy_num_{cid}_{name}")
    builder.button(text="🔙 Back", callback_data="main_menu")
    builder.adjust(2)

    await call.message.edit_text(
        f"{EMOJI['tg']} <b>টেলিগ্রাম সার্ভিসের জন্য দেশ নির্বাচন করুন:</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("buy_num_"))
async def process_buy_number(call: types.CallbackQuery):
    _, _, country_id, country_name = call.data.split("_")
    api_key = await get_user_key(call.from_user.id)

    await call.message.edit_text(
        f"{EMOJI['pending']} <b>Processing Order... [🔵 PENDING]</b>\n"
        f"Country: {country_name}",
        parse_mode="HTML"
    )
    
    success, res = await TigerSMS.buy_number(api_key, int(country_id))
    if not success:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Try Again", callback_data="buy_menu")
        builder.button(text="🔙 Menu", callback_data="main_menu")
        await call.message.edit_text(
            f"{EMOJI['danger']} <b>ক্রয় ব্যর্থ হয়েছে! [🔴 NO STOCK]</b>\n"
            f"কারণ: <code>{res}</code>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        return

    act_id = res["activationId"]
    phone = res["phoneNumber"]
    cost = res.get("activationCost", "N/A")

    asyncio.create_task(poll_sms_loop(call.message, api_key, act_id, phone, cost, country_name))

async def poll_sms_loop(msg: types.Message, api_key: str, act_id: str, phone: str, cost, country_name: str):
    for _ in range(30):
        await asyncio.sleep(4)
        status_data = await TigerSMS.get_status(api_key, act_id)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Cancel & Refund", callback_data=f"cancel_act_{act_id}")

        if status_data and status_data.get("sms"):
            code = status_data["sms"][0]["code"]
            await TigerSMS.set_status(api_key, act_id, 6) # সম্পন্ন
            await msg.edit_text(
                f"{EMOJI['success']} <b>OTP DELIVERED! [🟢 SUCCESS]</b>\n\n"
                f"📞 <b>Phone:</b> <code>+{phone}</code>\n"
                f"💬 <b>OTP Code:</b> <code>{code}</code>\n"
                f"🏷 <b>Country:</b> {country_name}\n"
                f"💰 <b>Cost:</b> ${cost}\n\n"
                f"<i>কোডটি টেলিগ্রামে সাবমিট করুন।</i>",
                parse_mode="HTML"
            )
            return

        try:
            await msg.edit_text(
                f"{EMOJI['pending']} <b>WAITING FOR SMS... [⏳ PENDING]</b>\n\n"
                f"📞 <b>Phone:</b> <code>+{phone}</code>\n"
                f"🌐 <b>Country:</b> {country_name}\n"
                f"🆔 <b>Activation ID:</b> <code>{act_id}</code>\n"
                f"💰 <b>Cost:</b> ${cost}\n\n"
                "<i>টেলিগ্রামে কোড সেন্ড করুন, এটি স্বয়ংক্রিয়ভাবে রিফ্রেশ হচ্ছে...</i>",
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        except Exception:
            pass

    await TigerSMS.set_status(api_key, act_id, 8)
    await msg.edit_text(
        f"{EMOJI['danger']} <b>সময় শেষ! [🔴 TIMEOUT]</b>\n"
        "কোনো কোড না আসায় ব্যালেন্স রিফান্ড হয়েছে।",
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("cancel_act_"))
async def cancel_activation(call: types.CallbackQuery):
    act_id = call.data.split("_")[2]
    api_key = await get_user_key(call.from_user.id)
    await TigerSMS.set_status(api_key, act_id, 8)
    await call.message.edit_text(
        f"{EMOJI['danger']} <b>বাতিল করা হয়েছে! [🔴 CANCELLED]</b>\n"
        "টাকা আপনার Tiger SMS অ্যাকাউন্টে রিফান্ড হয়েছে।",
        reply_markup=main_menu_keyboard(True, call.from_user.id == ADMIN_ID),
        parse_mode="HTML"
    )

# --- Admin Panel ---
@dp.callback_query(F.data == "admin_panel")
async def open_admin(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    countries = await get_all_countries()
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Add Country", callback_data="admin_add_country")
    if countries:
        builder.button(text="➖ Remove Country", callback_data="admin_rem_country")
    builder.button(text="🔙 Back", callback_data="main_menu")
    builder.adjust(1)
    
    text = (
        f"{EMOJI['admin']} <b>Owner Admin Console</b>\n\n"
        f"{EMOJI['primary']} <b>Status:</b> [🔵 ADMIN ACTIVE]\n"
        f"মোট কান্ট্রি সংখ্যা: {len(countries)}"
    )
    await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_add_country")
async def start_add_country(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.answer("Tiger SMS-এর <b>Country ID</b> লিখুন (যেমন: Russia=0, USA=187):", parse_mode="HTML")
    await state.set_state(Form.admin_add_country_id)
    await call.answer()

@dp.message(Form.admin_add_country_id)
async def process_cid(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("দয়া করে সংখ্যায় ID দিন:")
        return
    await state.update_data(cid=int(message.text))
    await message.answer("এবার দেশের <b>নাম</b> লিখুন (যেমন: Russia):", parse_mode="HTML")
    await state.set_state(Form.admin_add_country_name)

@dp.message(Form.admin_add_country_name)
async def process_cname(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cid = data["cid"]
    cname = message.text.strip()
    await add_country(cid, cname)
    await state.clear()
    await message.answer(
        f"{EMOJI['success']} <b>[🟢 ADDED]</b> {cname} (ID: {cid}) যুক্ত হয়েছে!",
        reply_markup=main_menu_keyboard(True, True),
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
    builder.button(text="🔙 Back", callback_data="admin_panel")
    builder.adjust(2)
    await call.message.edit_text("যেটি ডিলিট করতে চান সিলেক্ট করুন:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_c_"))
async def execute_remove_country(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    cid = int(call.data.split("_")[2])
    await remove_country(cid)
    await call.answer("কান্ট্রি রিমুভ হয়েছে!", show_alert=True)
    await open_admin(call)

# --- Emoji Catch Handler ---
@dp.message(F.text)
async def catch_custom_emoji(message: types.Message):
    if message.entities:
        for entity in message.entities:
            if entity.type == "custom_emoji":
                await message.reply(
                    f"✨ <b>Custom Emoji Found!</b>\n\n"
                    f"ID: <code>{entity.custom_emoji_id}</code>\n"
                    f"Code: <code>&lt;tg-emoji id=\"{entity.custom_emoji_id}\"&gt;⭐&lt;/tg-emoji&gt;</code>",
                    parse_mode="HTML"
                )
                return

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

    # Start Polling with drop_pending_updates to clear previous crash logs
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
