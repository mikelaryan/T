"""
منطق اصلی ربات تلگرام — نسخه پرمیوم (Premium)
"""

import os
import re
import time
import logging
import threading
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
# مدیریت کاربران
# ===========================================
user_regenerate_state = {} # ذخیره وضعیت تولید مجدد برای هر کاربر
_busy_lock = threading.Lock()
_busy_users: set = set()
user_last_inputs = {}
# 👈 لیست کش برای کاربرانی که تایید شده‌اند (برای جلوگیری از ریکوئست تکراری به کلادفلر)
authenticated_users: set = set() 

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

# ===========================================
# هندلرها
# ===========================================
@bot.message_handler(commands=["start"])
def handle_start(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"[LOG] User {user_id} started the bot.")
    
    # چک کردن کش
    if user_id in authenticated_users:
        bot.send_message(
            message.chat.id, 
            f"سلام مجدد {message.from_user.first_name} 👋\n\n"
            "✅ اشتراک شما فعال است.\n\n"
            "🔗 لینک اینستاگرام یا فایل ویدیو خود را بفرستید."
        )
        return

    # چک کردن دیتابیس کلادفلر
    is_valid, data = premium_manager.check_premium_auth(user_id)
    if is_valid:
        authenticated_users.add(user_id)
        user_info = data  # دیکشنری اطلاعات کاربر
        bot.send_message(
            message.chat.id, 
            f"سلام مجدد {message.from_user.first_name} 👋\n\n"
            f"✅ اشتراک شما فعال است (تا تاریخ: {user_info.get('expiry_date')}).\n"
            f"📊 امروز: {user_info.get('used_today')} از {user_info.get('daily_limit')} استفاده شده.\n\n"
            "🔗 لینک اینستاگرام یا فایل ویدیو خود را بفرستید."
        )
    else:
        # کاربر جدید است
        bot.send_message(
            message.chat.id, 
            f"سلام {message.from_user.first_name} 👋\n\n"
            "🌟 به ربات پرمیوم **تولیدر** خوش آمدید!\n\n"
            "🔒 برای استفاده از ربات، لطفاً **کد لایسنس** خود را ارسال کنید.\n"
            "🎫 اگر کد لایسنس ندارید، می‌توانید آن را از ما تهیه کنید."
        )

@bot.message_handler(commands=["help"])
def handle_start(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"[LOG] User {user_id} started the bot.")
    
    # چک کردن کش
    if user_id in authenticated_users:
        bot.send_message(
            message.chat.id, 
            f"سلام مجدد {message.from_user.first_name} 👋\n\n"
            "✅ اشتراک شما فعال است.\n\n"
            "🔗 لینک اینستاگرام یا فایل ویدیو خود را بفرستید."
        )
        return

    # چک کردن دیتابیس کلادفلر
    is_valid, data = premium_manager.check_premium_auth(user_id)
    if is_valid:
        authenticated_users.add(user_id)
        user_info = data  # دیکشنری اطلاعات کاربر
        
        # 👈 تبدیل تاریخ میلادی به شمسی
        raw_expiry = user_info.get('expiry_date')
        jalali_expiry = premium_manager.to_jalali(raw_expiry)
        
        bot.send_message(
            message.chat.id, 
            f"سلام مجدد {message.from_user.first_name} 👋\n\n"
            f"✅ اشتراک شما فعال است (تا تاریخ: {jalali_expiry}).\n"
            f"📊 امروز: {user_info.get('used_today')} از {user_info.get('daily_limit')} استفاده شده.\n\n"
            "🔗 لینک اینستاگرام یا فایل ویدیو خود را بفرستید."
        )
    else:
        # کاربر جدید است
        bot.send_message(
            message.chat.id, 
            f"سلام {message.from_user.first_name} 👋\n\n"
            "🌟 به ربات پرمیوم **تولیدر** خوش آمدید!\n\n"
            "🔒 برای استفاده از ربات، لطفاً **کد لایسنس** خود را ارسال کنید.\n"
            "🎫 اگر کد لایسنس ندارید، می‌توانید آن را از ما تهیه کنید."
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
        result = transcribe_audio(audio_path)

        srt_path = str(WORK_DIR / f"subtitles_{message.from_user.id}.srt")
        save_srt(result["subtitles"], srt_path)
        reporter.update(stage=1, sub_text="", pct=100, force=True)

        # 👈 ساخت دکمه تولید مجدد (فقط اگر درخواست اول باشد، نه خود تولید مجدد)
        markup = None
        if not is_regen:
            markup = types.InlineKeyboardMarkup()
            regen_btn = types.InlineKeyboardButton("🔄 تولید مجدد (فقط یک‌بار)", callback_data=f"regen_{message.from_user.id}")
            markup.add(regen_btn)

        render_and_send(
            bot=bot,
            chat_id=message.chat.id,
            video_path=video_path,
            subtitles=result["subtitles"],
            progress=reporter,
            reply_markup=markup # 👈 ارسال دکمه
        )

        # 👈 کم کردن شارژ (اگر تولید مجدد نباشد)
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
    
    # 👈 استفاده از صف
    queue_manager.add_to_queue(user_id, message, is_link, is_regen=False)


@bot.callback_query_handler(func=lambda call: call.data.startswith("regen_"))
def handle_regenerate(call: types.CallbackQuery):
    user_id = int(call.data.split("_")[1])
    logger.info(f"[LOG] User {user_id} requested regeneration.")
    
    state = user_regenerate_state.get(user_id)
    
    # 👈 اگر قبلا استفاده کرده باشه
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
        
    # 👈 اضافه کردن به صف با پرچم is_regen=True
    queue_manager.add_to_queue(user_id, state["message"], state["is_link"], is_regen=True)


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
