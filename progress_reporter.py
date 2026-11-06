"""
گزارشگر پیشرفت مرحله‌ای — با ظاهر حرفه‌ای برای تلگرام
"""

import time
import threading
import logging

logger = logging.getLogger(__name__)

class ProgressReporter:
    # 👈 فقط ۳ مرحله اصلی
    STAGES = [
        "پردازش ویدیو",
        "تولید زیرنویس",
        "آماده ارسال"
    ]

    def __init__(self, bot, chat_id):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = None
        self._lock = threading.Lock()
        self._last_update = 0.0
        self._min_interval = 2.0      # ثانیه بین آپدیت‌های تلگرام
        self._current_text = ""
        self.start_time = time.time()
        
        self.current_stage = 0
        self.pct = 0.0

        try:
            # 👈 پیام اولیه با متن جدید
            sent = bot.send_message(
                chat_id, 
                "⏳ <b>در حال شروع پردازش...</b>\n<i>(ممکن است چند دقیقه طول بکشد)</i>", 
                parse_mode="HTML"
            )
            self.message_id = sent.message_id
        except Exception as e:
            logger.warning(f"ارسال پیام اولیه ناموفق بود: {e}")

    def _make_bar(self, pct: float, width: int = 10) -> str:
        pct = max(0.0, min(100.0, pct))
        filled = int(width * pct / 100)
        return "🟩" * filled + "⬜" * (width - filled)

    def _render_message(self) -> str:
        # 👈 اضافه شدن خط دوم با فونت کج
        lines = [
            "🚀 <b>عملیات ساخت زیرنویس</b>\n",
            "<i>(ممکن است چند دقیقه طول بکشد)</i>\n"
        ]
        
        for i, stage_name in enumerate(self.STAGES):
            if i < self.current_stage:
                lines.append(f"✅ مرحله {i+1}: {stage_name}")
            elif i == self.current_stage:
                lines.append(f"⏳ مرحله {i+1}: {stage_name}")
            else:
                lines.append(f"⬜ مرحله {i+1}: {stage_name}")
        
        lines.append("")
        bar = self._make_bar(self.pct)
        lines.append(f"📊 <b>پیشرفت:</b> {bar} {self.pct:.0f}%")
        
        elapsed = int(time.time() - self.start_time)
        lines.append(f"⏱ <b>زمان:</b> {elapsed} ثانیه")
        return "\n".join(lines)

    def update(self, stage: int, sub_text: str = "", pct: float = None, extra: str = "", force: bool = False):
        """آپدیت وضعیت مراحل (پارامترهای sub_text و extra نادیده گرفته می‌شوند)"""
        with self._lock:
            self.current_stage = stage
            if pct is not None:
                self.pct = pct

            now = time.time()
            if not force and (now - self._last_update) < self._min_interval:
                return
            self._last_update = now

            if not self.message_id:
                return

            try:
                text = self._render_message()
                if text == self._current_text:
                    return
                self.bot.edit_message_text(text, self.chat_id, self.message_id, parse_mode="HTML")
                self._current_text = text
            except Exception as e:
                if "not modified" not in str(e).lower():
                    logger.debug(f"خطا در ادیت پیام تلگرام: {e}")

    def finish(self, text: str):
        with self._lock:
            if not self.message_id: return
            try:
                self.bot.edit_message_text(text, self.chat_id, self.message_id, parse_mode="HTML")
            except Exception:
                pass

    def error(self, error_text: str, reply_markup=None):
        with self._lock:
            if not self.message_id: return
            try:
                self.bot.edit_message_text(error_text, self.chat_id, self.message_id, parse_mode="HTML", reply_markup=reply_markup)
            except Exception:
                pass
