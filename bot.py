"""
منطق اصلی ربات تلگرام — نسخه پرمیوم (Premium)
"""

import os
import re
import time
import logging
import threading
import hashlib
import datetime
from zoneinfo import ZoneInfo

import telebot
from telebot import types
from flask import Flask, request

import config
import queue_manager
import premium_manager
from progress_reporter import ProgressReporter
from video_processor import process_incoming, save_srt, WORK_DIR, VideoTooLongError
from gemini_service import transcribe_audio
from renderer import render_and_send

logger = logging.getLogger(__name__)

# ساخت نمونه ربات با استفاده از config
bot = telebot.TeleBot(config.TELEGRAM_BOT_TOKEN, parse_mode=None, threaded=True)

# برای استفاده در وبهوک
app = Flask(__name__)
BOT_TOKEN = config.TELEGRAM_BOT_TOKEN
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# الگوی لینک اینستاگرام
INSTAGRAM_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:instagram\.com|instagr\.am)/(?:p|reel|reels|tv)/[A-Za-z0-9_-]+"
)

# ===========================================
# مدیریت کاربران و وضعیت‌ها
# ===========================================
user_regenerate_state = {} # ذخیره وضعیت تولید مجدد برای هر کاربر
_busy_lock = threading.Lock()
_busy_users: set = set()
user_last_inputs = {}
authenticated_users: set = set() # لیست کش کاربران تایید شده

def _is_busy(user_id: int) -> bool:
    with _busy_lock:
        return user_id in _busy_users

def _mark_busy(user_id: int) -> bool:
    with _busy_lock:
        if user_id in _busy_users:
            return False
        _busy_users.add(user_id)
        return True

def _mark_free(user_id: int) -> None:
    with _busy_lock:
        _busy_users.discard(user_id)

# ===========================================
# توابع کمکی
# ===========================================
def is_instagram_link(text: str) -> bool:
    if not text:
        return False
    return bool(INSTAGRAM_PATTERN.match(text.strip()))

def is_video_file(message: types.Message) -> bool:
    if message.video:
        return True
    if message.document and (message.document.mime_type or "").startswith("video/"):
        return True
    return False

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("👤 حساب کاربری"), types.KeyboardButton("📚 راهنما"))
    return markup

# ===========================================
# هندلرها
# ===========================================
@bot.message_handler(commands=["start"])
def handle_start(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"[LOG] User {user_id} started the bot.")
    
    # چک کردن کش (اگر قبلا احراز هویت شده باشد)
    if user_id in authenticated_users:
        bot.send_message(
            message.chat.id, 
            f"سلام مجدد {message.from_user.first_name} 👋\n\n"
            "✅ اشتراک شما فعال است.\n\n"
            "🔗 لینک اینستاگرام یا فایل ویدیو خود را بفرستید.",
            reply_markup=get_main_keyboard()
        )
        return

    # چک کردن دیتابیس کلادفلر
    is_valid, data = premium_manager.check_premium_auth(user_id)
    if is_valid:
        authenticated_users.add(user_id)
        user_info = data  
        
        # تبدیل تاریخ میلادی به شمسی
        raw_expiry = user_info.get('expiry_date')
        jalali_expiry = premium_manager.to_jalali(raw_expiry)
        
        bot.send_message(
            message.chat.id, 
            f"سلام مجدد {message.from_user.first_name} 👋\n\n"
            f"✅ اشتراک شما فعال است (تا تاریخ: {jalali_expiry}).\n"
            f"📊 امروز: {user_info.get('used_today')} از {user_info.get('daily_limit')} استفاده شده.\n\n"
            "🔗 لینک اینستاگرام یا فایل ویدیو خود را بفرستید.",
            reply_markup=get_main_keyboard()
        )
    else:
        # کاربر جدید است یا اشتراکش منقضی شده
        bot.send_message(
            message.chat.id, 
            f"سلام {message.from_user.first_name} 👋\n\n"
            "🌟 به ربات پرمیوم **تولیدر** خوش آمدید!\n\n"
            "🔒 برای استفاده از ربات، لطفاً **کد لایسنس** خود را ارسال کنید.\n"
            "🎫 اگر کد لایسنس ندارید، می‌توانید آن را از ما تهیه کنید.",
            reply_markup=get_main_keyboard()
        )

@bot.message_handler(commands=["help"])
def handle_help_command(message: types.Message):
    text = (
        "📌 راهنما (نسخه پرمیوم):\n\n"
        "۱. اگر لایسنس ندارید، ابتدا کد لایسنس خود را ارسال کنید.\n"
        "۲. لینک پست / ریلز / تیوی اینستاگرام یا فایل ویدیو خود را بفرستید.\n\n"
        "شما می‌توانید روزانه تعداد مشخصی ویدیو (طبق اشتراک خود) پردازش کنید.\n"
        "در صورت نیاز به پشتیبانی با ما در ارتباط باشید."
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "👤 حساب کاربری")
def handle_account(message: types.Message):
    user_id = message.from_user.id
    is_valid, data = premium_manager.check_premium_auth(user_id)
    
    if not is_valid:
        bot.reply_to(message, "❌ شما اشتراک فعالی ندارید. لطفاً ابتدا کد لایسنس خود را ارسال کنید.")
        return
        
    # تبدیل تاریخ‌ها به شمسی
    start_date = premium_manager.to_jalali(data.get('created_at'))
    expiry_date = premium_manager.to_jalali(data.get('expiry_date'))
    
    used = data.get('used_today', 0)
    limit = data.get('daily_limit', 0)
    remaining = limit - used
    
    # 👈 گرفتن اسم و یوزرنیم کاربر از تلگرام
    first_name = message.from_user.first_name or "کاربر"
    username = f"@{message.from_user.username}" if message.from_user.username else "ثبت نشده"
    
    text = (
        f"👤 <b>پروفایل کاربری شما</b>\n\n"
        f"📛 نام: <b>{first_name}</b>\n"  # 👈 اسم پروفایل اضافه شد
        f"🆔 آیدی عددی: <code>{user_id}</code>\n"
        f"🔗 یوزرنیم: {username}\n\n"      # 👈 یوزرنیم اضافه شد
        f"📅 تاریخ شروع: {start_date}\n"
        f"⏳ تاریخ انقضا: {expiry_date}\n\n"
        f"📊 <b>وضعیت امروز:</b>\n"
        f"مصرف شده: {used} از {limit}\n"
        f"✅ باقی‌مانده: {remaining} ویدیو"
    )
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "📚 راهنما")
def handle_help_button(message: types.Message):
    text = (
        "📚 <b>راهنمای استفاده از ربات:</b>\n\n"
        "۱. کافیست لینک ریلز اینستاگرام را بفرستید.\n"
        "۲. یا فایل ویدیوی خود را (حداکثر ۲ دقیقه) آپلود کنید.\n"
        "۳. ربات به صورت خودکار ویدیو را زیرنویس کرده و تحویل می‌دهد.\n\n"
        "۴. برای ارسال ویدیو از یوتیوب یا تیک تاک یا پلتفرم های دیگه میتونید از ربات @youtube_instagram_videobot استفاده کنید و ویدیو را فورفوارد کنید.\n\n"
        "⚠️ <b>نکات مهم:</b>\n"
        "- سقف مصرف روزانه شما در دکمه «حساب کاربری» قابل مشاهده است.\n"
        "- در صورت بروز خطا یا ترجمه ناقص، می‌توانید از دکمه «تلاش مجدد» استفاده کنید.\n\n"
        "💬 در صورت نیاز به پشتیبانی با ایدی در بایو ربات در تماس باشید."
    )
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(
    content_types=["text", "video", "document"],
    func=lambda m: not (m.text and m.text.startswith("/")) and m.text not in ["👤 حساب کاربری", "📚 راهنما"]
)
def handle_input(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    # ۱. بررسی احراز هویت (اگر کاربر توی رم نیست)
    if user_id not in authenticated_users:
        
        # اول بریم از دیتابیس کلادفلر چک کنیم (شاید ربات ری‌استارت شده باشه)
        is_auth, auth_data = premium_manager.check_premium_auth(user_id)
        
        if is_auth:
            # کاربر از قبل لایسنس داشته، فقط از رم پاک شده بود
            authenticated_users.add(user_id)
            logger.info(f"[LOG] User {user_id} re-authenticated from DB after restart.")
            # کد ادامه پیدا میکنه تا ویدیو/لینک کاربر پردازش بشه
        else:
            # کاربر واقعاً لایسنس فعال نداره
            if not message.text:
                bot.reply_to(message, "❌ لطفاً ابتدا کد لایسنس خود را به صورت متن ارسال کنید.")
                return
            
            # اگر کاربر لینک فرستاده بود (نه کد لایسنس)، بهش تذکر بدیم
            if is_instagram_link(message.text):
                bot.reply_to(message, "🔒 شما در سیستم احراز هویت نشده‌اید.\nلطفاً برای فعال‌سازی، ابتدا **کد لایسنس** خود را ارسال کنید.")
                return

            # بررسی لایسنس جدید و فعال‌سازی
            is_valid, msg = premium_manager.activate_premium(user_id, username, message.text)
            
            if is_valid:
                authenticated_users.add(user_id)
                logger.info(f"[LOG] User {user_id} activated premium.")
                bot.reply_to(message, f"✅ {msg}\n\nحالا لینک یا ویدیوی خود را بفرستید.")
            else:
                logger.warning(f"[LOG] User {user_id} failed activation.")
                bot.reply_to(message, f"❌ {msg}\n\nاگر لایسنس ندارید، از کانال ما تهیه کنید.")
            return

    # ۲. اگر کاربر احراز هویت شده، ورودی را بررسی می‌کنیم
    if message.text and is_instagram_link(message.text):
        is_link = True
    elif is_video_file(message):
        is_link = False
    else:
        logger.warning(f"[LOG] Invalid input from user {user_id}.")
        bot.reply_to(message, "❌ ورودی نامعتبره!\nلطفاً یه لینک اینستاگرام معتبر یا یه فایل ویدیو بفرست.")
        return

    # ۳. چک کردن محدودیت روزانه (قبل از صف)
    is_allowed, limit_msg = premium_manager.check_daily_limit(user_id)
    if not is_allowed:
        bot.reply_to(message, f"⚠️ {limit_msg}")
        return

    # ۴. اضافه کردن به صف
    if not _mark_busy(user_id):
        bot.reply_to(message, "⏳ درخواست شما در حال پردازش است.\nلطفاً صبر کنید.")
        return

    queue_position = queue_manager.add_to_queue(user_id, message, is_link, is_regen=False)
    logger.info(f"[LOG] User {user_id} added to queue. Position: {queue_position}")
    
    # پیام جدید بدون نمایش شماره صف
    bot.reply_to(
        message,
        f"✅ درخواست شما با موفقیت ثبت شد و در صف پردازش قرار گرفت.\n"
        f"📊 {limit_msg}"
    )

# ===========================================
# پردازش اصلی
# ===========================================
def _process(message: types.Message, is_link: bool, is_regen: bool = False):
    logger.info(f"[LOG] Starting main processing for user {message.from_user.id}.")
    reporter = ProgressReporter(bot, message.chat.id)

    try:
        video_path, audio_path = process_incoming(bot, message, is_link, progress=reporter)

        reporter.update(stage=1, sub_text="", pct=50, force=True)
        result = transcribe_audio(audio_path, is_regen=is_regen)
        
        # 👈 گرفتن مدل استفاده شده و منبع API از خروجی
        used_model = result.get("used_model", "نامشخص")
        api_source = result.get("api_source", "نامشخص")

        srt_path = str(WORK_DIR / f"subtitles_{message.from_user.id}.srt")
        save_srt(result["subtitles"], srt_path)
        reporter.update(stage=1, sub_text="", pct=100, force=True)

        # ساخت دکمه تولید مجدد (فقط اگر درخواست اول باشد، نه خود تولید مجدد)
        markup = None
        if not is_regen:
            markup = types.InlineKeyboardMarkup()
            regen_btn = types.InlineKeyboardButton("🔄 تولید مجدد (فقط یک‌بار)", callback_data=f"regen_{message.from_user.id}")
            markup.add(regen_btn)

        # ۱. ارسال ویدیو به کاربر و گرفتن file_id
        video_file_id = render_and_send(
            bot=bot,
            chat_id=message.chat.id,
            video_path=video_path,
            subtitles=result["subtitles"],
            progress=reporter,
            reply_markup=markup 
        )

        # ۲. کم کردن شارژ (اگر تولید مجدد نباشد)
        consume_msg = ""
        if not is_regen:
            consume_ok, consume_msg = premium_manager.consume_daily_limit(message.from_user.id)
            if consume_ok:
                logger.info(f"[LOG] Limit consumed successfully. {consume_msg}")
            
            # ذخیره اطلاعات برای دکمه تولید مجدد
            user_regenerate_state[message.from_user.id] = {
                "message": message,
                "is_link": is_link,
                "used": False
            }

        # ۳. ارسال کپی برای ادمین (خودت)
        try:
            user_name = message.from_user.first_name or "کاربر"
            user_uname = f"@{message.from_user.username}" if message.from_user.username else "ندارد"
            input_type = "لینک اینستاگرام" if is_link else "فایل آپلودی"
            regen_status = "بله (تولید مجدد)" if is_regen else "خیر"
            elapsed_time = int(time.time() - reporter.start_time)

            admin_caption = (
                f"📥 <b>ویدیوی جدید ساخته شد!</b>\n\n"
                f"👤 <b>نام کاربر:</b> {user_name}\n"
                f"🔗 <b>یوزرنیم:</b> {user_uname}\n"
                f"🆔 <b>آیدی عددی:</b> <code>{message.from_user.id}</code>\n\n"
                f"📊 <b>جزئیات پردازش:</b>\n"
                f"🎞 نوع ورودی: {input_type}\n"
                f"🔄 وضعیت: {regen_status}\n"
                f"⏱ زمان پردازش: {elapsed_time} ثانیه\n"
                f"🤖 <b>مدل:</b> <code>{used_model}</code>\n"
                f"🔌 <b>منبع:</b> {api_source}\n"
                f"📦 وضعیت شارژ: {consume_msg}"
            )
            
            # ارسال با استفاده از file_id (بدون آپلود مجدد)
            bot.send_video(
                config.ADMIN_CHAT_ID,
                video_file_id,
                caption=admin_caption,
                parse_mode="HTML"
            )
            logger.info(f"[LOG] Admin copy sent for user {message.from_user.id}.")
        except Exception as e:
            logger.error(f"[ERROR] Failed to send admin copy: {e}")

        # پیام نهایی موفقیت برای کاربر
        logger.info(f"[LOG] Processing for user {message.from_user.id} completed successfully.")
        reporter.finish(
            f"✅ <b>عملیات با موفقیت انجام شد!</b>\n\n"
            f"🎬 ویدیوی شما زیرنویس شد و در پیام پایین ارسال گردید.\n"
            f"⏱ زمان کل: {int(time.time() - reporter.start_time)} ثانیه\n"
            f"📊 {consume_msg}"
        )

    except Exception as e:
        logger.exception(f"[ERROR] Processing error for user {message.from_user.id}: {e}")
        user_last_inputs[message.from_user.id] = message
        
        markup = types.InlineKeyboardMarkup()
        retry_btn = types.InlineKeyboardButton("🔄 تلاش مجدد", callback_data=f"retry_{message.from_user.id}")
        markup.add(retry_btn)
        
        err_str = str(e)
        if "503" in err_str or "UNAVAILABLE" in err_str or "همه مدل‌ها ناموفق بودند" in err_str:
            friendly_error = "⚠️ <b>سرورهای هوش مصنوعی در حال حاضر با ترافیک بالا مواجه هستند.</b>\n\nلطفاً چند لحظه صبر کنید و دکمه تلاش مجدد را بزنید."
        else:
            friendly_error = err_str[:300].replace("<", "&lt;").replace(">", "&gt;")
            
        reporter.error(f"❌ <b>خطا در پردازش:</b>\n\n{friendly_error}", reply_markup=markup)

# ===========================================
# دکمه‌های شیشه‌ای (تلاش مجدد و تولید مجدد)
# ===========================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("retry_"))
def handle_retry(call: types.CallbackQuery):
    user_id = int(call.data.split("_")[1])
    logger.info(f"[LOG] User {user_id} requested retry.")
    
    if _is_busy(user_id):
        bot.answer_callback_query(call.id, "⏳ ربات در حال پردازش است. لطفاً صبر کنید.", show_alert=True)
        return
        
    message = user_last_inputs.get(user_id)
    if not message:
        bot.answer_callback_query(call.id, "❌ درخواست قبلی یافت نشد.", show_alert=True)
        return
        
    is_link = False
    if message.text and is_instagram_link(message.text):
        is_link = True
    elif is_video_file(message):
        is_link = False
    else:
        bot.answer_callback_query(call.id, "❌ ورودی نامعتبر است.", show_alert=True)
        return
        
    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
    except Exception:
        pass
        
    bot.answer_callback_query(call.id, "🔄 در حال تلاش مجدد...")
    
    if not _mark_busy(user_id):
        return
    
    queue_manager.add_to_queue(user_id, message, is_link, is_regen=False)

@bot.callback_query_handler(func=lambda call: call.data.startswith("regen_"))
def handle_regenerate(call: types.CallbackQuery):
    user_id = int(call.data.split("_")[1])
    logger.info(f"[LOG] User {user_id} requested regeneration.")
    
    state = user_regenerate_state.get(user_id)
    
    if not state or state["used"]:
        bot.answer_callback_query(call.id, "❌ شما قبلاً از فرصت تولید مجدد استفاده کرده‌اید.", show_alert=True)
        return
        
    if _is_busy(user_id):
        bot.answer_callback_query(call.id, "⏳ ربات در حال پردازش است. لطفاً صبر کنید.", show_alert=True)
        return

    # علامت‌گذاری به عنوان استفاده شده و حذف دکمه
    state["used"] = True
    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
    except Exception:
        pass
        
    bot.answer_callback_query(call.id, "🔄 در حال تولید مجدد...")
    
    if not _mark_busy(user_id):
        return
        
    queue_manager.add_to_queue(user_id, state["message"], state["is_link"], is_regen=True)

# ===========================================
# تردهای پس‌زمینه (نیمه‌شب و آپدیت)
# ===========================================
def midnight_reset_notifier():
    """ترد پس‌زمینه برای ارسال پیام ریست شارژ در ساعت ۱۲ شب به وقت ایران"""
    logger.info("[SCHEDULER] Midnight reset notifier thread started.")
    last_reset_date = None
    
    while True:
        try:
            now = datetime.datetime.now(ZoneInfo("Asia/Tehran"))
            today = now.date()

            if now.hour == 0 and now.minute == 0 and last_reset_date != today:
                logger.info("[SCHEDULER] It's midnight. Sending reset notifications...")
                users_to_notify = list(authenticated_users)
                
                for uid in users_to_notify:
                    try:
                        bot.send_message(
                            uid,
                            "🌅 <b>روز جدید مبارک!</b>\n\n"
                            "📊 شارژ استفاده روزانه شما به‌طور خودکار ریست شد.\n"
                            "🔗 حالا می‌توانید ویدیوهای جدید خود را ارسال کنید.",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.warning(f"[SCHEDULER] Could not send reset message to {uid}: {e}")
                
                last_reset_date = today
                logger.info("[SCHEDULER] All reset notifications sent.")

        except Exception as e:
            logger.error(f"[SCHEDULER] Error in midnight notifier: {e}")

        time.sleep(30)

def notify_users_about_update():
    """بررسی فایل changelog و ارسال پیام به کاربران در صورت وجود نسخه جدید"""
    try:
        if not os.path.exists("changelog.txt"):
            logger.warning("changelog.txt not found.")
            return
            
        with open("changelog.txt", "r", encoding="utf-8") as f:
            changelog_text = f.read().strip()

        if not changelog_text:
            return

        current_hash = hashlib.md5(changelog_text.encode('utf-8')).hexdigest()
        last_hash = premium_manager.get_setting("last_changelog_hash")
        
        if last_hash == current_hash:
            logger.info("[UPDATE] Changelog has not changed. Skipping broadcast.")
            return

        logger.info("[UPDATE] New changelog detected! Sending broadcast to users...")
        users = premium_manager.get_all_active_users()
        
        message_text = (
            f"🚀 <b>ربات تولیدر آپدیت شد!</b>\n\n"
            f"📝 <b>تغییرات جدید:</b>\n<code>{changelog_text}</code>\n\n"
            f"✨ <i>ربات آماده دریافت لینک‌ها و ویدیوهای جدید شماست.</i>"
        )

        for uid in users:
            try:
                bot.send_message(uid, message_text, parse_mode="HTML")
                time.sleep(0.1) 
            except Exception as e:
                logger.warning(f"Could not send update to {uid}: {e}")

        premium_manager.set_setting("last_changelog_hash", current_hash)
        logger.info("[UPDATE] Broadcast finished successfully.")

    except Exception as e:
        logger.error(f"[UPDATE] Error in broadcast: {e}")

# ===========================================
# اجرا و وبهوک
# ===========================================
@app.route("/")
def health_check():
    return "Premium Bot is alive!", 200

@app.route('/' + BOT_TOKEN, methods=['POST'])
def getMessage():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200

def run_bot():
    """اجرای ربات"""
    queue_manager.start_worker(_process, _mark_free)

    # شروع ترد زمان‌بند ریست
    reset_thread = threading.Thread(target=midnight_reset_notifier, daemon=True)
    reset_thread.start()

    # شروع ترد اطلاع‌رسانی آپدیت (با ۵ ثانیه تاخیر)
    def delayed_update():
        time.sleep(5)
        notify_users_about_update()
    
    update_thread = threading.Thread(target=delayed_update, daemon=True)
    update_thread.start()

    if RENDER_EXTERNAL_URL:
        print("[LOG] Running Premium on Render. Setting webhook...")
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=RENDER_EXTERNAL_URL + '/' + BOT_TOKEN)
        print(f"[LOG] Webhook set to: {RENDER_EXTERNAL_URL + '/' + BOT_TOKEN}")
        
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
    else:
        print("[LOG] Running Premium locally. Starting infinity polling...")
        bot.remove_webhook()
        bot.infinity_polling(timeout=30, long_polling_timeout=25)
