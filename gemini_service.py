"""
ارتباط با هوش مصنوعی — اولویت مطلق با GapGPT (پولی)، پشتیبان با مدل‌های رایگان
"""

import os
import json
import base64
import logging
import time
import random
import requests

import config

logger = logging.getLogger(__name__)

# ===========================================
# پرامپت
# ===========================================
SUBTITLE_PROMPT = """
تو یک متخصص مترجم و زمان‌بندِ دقیق زیرنویس هستی. 
مهم‌ترین وظیفه تو، همگام‌سازی دقیق و لحظه‌ای (Sync) زیرنویس با صدای گوینده است.

ورودی تو یک فایل صوتی انگلیسی است. باید این مراحل را دقیقاً طی کنی:
۱. به صدا گوش کن و سرعت و مکث‌های گوینده (Pauses) را پیدا کن.
۲. متن هر بخش را به فارسی روان و محاوره‌ای ترجمه کن.
۳. زمان شروع و پایان هر بخش را دقیقاً بر اساس همان مکث‌ها تنظیم کن.

⚠️ قوانین سخت‌گیرانه (تخطی ممنوع):
- سرعت و زمان‌بندی: اگر گوینده سریع صحبت می‌کند (مثل ریلزهای اینستاگرام)، زیرنویس نیز باید با همان سرعت بالا عوض شود. هیچ حداقل زمانی برای نمایش خطوط وجود ندارد (حتی اگر ۰.۵ ثانیه باشد).
- عدم تداخل: زمان شروع خط جدید (start_time) هرگز نباید از زمان پایان خط قبلی (end_time) کمتر باشد.
- عدم کشیدگی: هیچ خطی نباید بیشتر از ۳ ثانیه روی صفحه بماند.
- طول خطوط: هر خط زیرنویس باید حداکثر ۶ کلمه باشد (برای خوانایی در اینستاگرام).
- ترجمه: اصطلاحات تخصصی و اسامی خاص را به همان انگلیسی بنویس.

فرمت خروجی: فقط و فقط یک آرایه JSON معتبر، بدون هیچ متن اضافه، بدون ```json:

{
  "subtitles": [
    {
      "line_index": 1,
      "start_time": "00:00:01,000",
      "end_time": "00:00:02,500",
      "text": "متن فارسی"
    }
  ]
}

فقط JSON خالص برگردان.
""".strip()

MAX_INLINE_BYTES = 20 * 1024 * 1024

def _encode_audio(audio_path: str) -> str:
    size = os.path.getsize(audio_path)
    if size > MAX_INLINE_BYTES:
        raise ValueError(f"حجم فایل صوتی ({size / 1024 / 1024:.1f}MB) از حد مجاز بیشتره.")
    with open(audio_path, "rb") as f:
        data = f.read()
    return base64.b64encode(data).decode("utf-8")

def _detect_mime(audio_path: str) -> str:
    ext = os.path.splitext(audio_path)[1].lower()
    return {
        ".wav": "audio/wav", ".mp3": "audio/mp3",
        ".m4a": "audio/m4a", ".aac": "audio/aac",
        ".ogg": "audio/ogg", ".flac": "audio/flac",
        ".webm": "audio/webm",
    }.get(ext, "audio/wav")

def _extract_subtitles_from_text(text: str) -> dict:
    """استخراج فوق مقاوم JSON از پاسخ هوش مصنوعی"""
    if not text:
        raise ValueError("پاسخ خالی از هوش مصنوعی دریافت شد.")

    # 👈 ۱. تمیزکاری اولیه: حذف مارک‌داون و کاراکترهای اضافی
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0]
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0]

    cleaned = cleaned.strip()

    # 👈 ۲. تلاش اول: پارس عادی با نادیده گرفتن کاراکترهای کنترلی
    try:
        parsed = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        # 👈 ۳. تلاش دوم: پیدا کردن اولین { و آخرین } (حتی اگر متن اضافه وجود داشته باشه)
        start_idx = cleaned.find('{')
        end_idx = cleaned.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
            json_substring = cleaned[start_idx:end_idx+1]
            try:
                parsed = json.loads(json_substring, strict=False)
            except json.JSONDecodeError as e:
                logger.error(f"خطای JSON پس از استخراج: {e}")
                logger.error(f"متن خام دریافتی: {text[:500]}")
                raise ValueError("فرمت پاسخ هوش مصنوعی نامعتبر است.")
        else:
            logger.error(f"هیچ آکولادی در پاسخ پیدا نشد. متن خام: {text[:500]}")
            raise ValueError("پاسخ هوش مصنوعی حاوی JSON نیست.")

    # 👈 ۴. بررسی ساختار داخلی
    subtitles = parsed.get("subtitles")
    if not isinstance(subtitles, list) or not subtitles:
        raise ValueError("پاسخ حاوی کلید subtitles معتبر نیست.")
        
    return parsed

# ===========================================
# تابع ارتباط با GapGPT (پولی - اولویت اول)
# ===========================================
def _try_gapgpt(audio_b64: str, mime_type: str) -> dict | None:
    if not getattr(config, "GAPGPT_API_KEY", None) or "YOUR_GAPGPT" in config.GAPGPT_API_KEY:
        logger.error("کلید API گپ‌جی‌پی‌تی تنظیم نشده است.")
        return None

    logger.info("💰 اولویت اول: تلاش با GapGPT (پولی)...")
    api_url = "https://api.gapgpt.app/v1/chat/completions"
    model_name = "gemini-3.8-flash"
    headers = {"Authorization": f"Bearer {config.GAPGPT_API_KEY}", "Content-Type": "application/json"}
    
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": SUBTITLE_PROMPT},
                    {"type": "input_audio", "input_audio": {"data": audio_b64, "format": mime_type.split("/")[-1]}}
                ]
            }
        ],
        "response_format": {"type": "json_object"}
    }

    try:
        # تایم‌اوت ۱۲۰ ثانیه برای گپ‌جی‌پی‌تی
        response = requests.post(api_url, headers=headers, json=payload, timeout=350)
        if response.status_code == 200:
            text = response.json()["choices"][0]["message"]["content"]
            result = _extract_subtitles_from_text(text)
            logger.info(f"✅ موفق با GapGPT ({model_name}) ({len(result['subtitles'])} خط)")
            result["used_model"] = model_name
            result["api_source"] = "GapGPT (پولی)"
            return result
        else:
            logger.error(f"خطای GapGPT: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"خطای شبکه یا تایم‌اوت در GapGPT: {e}")
        
    return None

# ===========================================
# تابع ارتباط با Google (رایگان - پشتیبان)
# ===========================================
def _try_google_free_model(model_name: str, audio_b64: str, mime_type: str, duration: float = 0) -> dict | None:
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": SUBTITLE_PROMPT}, {"inline_data": {"mime_type": mime_type, "data": audio_b64}}]}],
        "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"},
    }

    for attempt in range(1, 3): # ۲ تلاش
        logger.info(f"[{model_name}] تلاش {attempt}/2 — گوگل (رایگان - پشتیبان)...")
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=20)
            
            if response.status_code == 200:
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                result = _extract_subtitles_from_text(text)
                
                # اعتبارسنجی
                if duration > 15 and len(result['subtitles']) < 3:
                    logger.warning(f"⚠️ خروجی {model_name} ناقص است. رد می‌شود.")
                    return None

                result["used_model"] = model_name
                result["api_source"] = "گوگل (رایگان)"
                return result
                
            logger.warning(f"⚠️ {model_name} → HTTP {response.status_code}")
        except requests.RequestException as e:
            logger.warning(f"خطای شبکه ({model_name}): {e}")
            
        if attempt < 2:
            _sleep_backoff(attempt)
            
    return None

# ===========================================
# تابع اصلی
# ===========================================
def transcribe_audio(audio_path: str, is_regen: bool = False, duration: float = 0) -> dict:
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"فایل صوتی پیدا نشد: {audio_path}")

    audio_b64 = _encode_audio(audio_path)
    mime_type = _detect_mime(audio_path)

    # 👈 ۱. اولویت اول: گپ‌جی‌پی‌تی (برای همه درخواست‌ها، چه عادی چه تولید مجدد)
    result = _try_gapgpt(audio_b64, mime_type)
    if result:
        return result
    
    logger.warning("⚠️ گپ‌جی‌پی‌تی پاسخ نداد یا قطع بود. تلاش با مدل‌های رایگان...")

    # 👈 ۲. اولویت‌های بعدی: مدل‌های رایگان گوگل
    free_models = [
        "gemini-3.8-flash",       
        "gemini-3.6-flash",       
        "gemini-3.5-flash-lite"   
    ]
    
    for model in free_models:
        result = _try_google_free_model(model, audio_b64, mime_type, duration)
        if result: 
            return result

    # 👈 ۳. اگر همه خراب بودند، خطای اختصاصی بده تا ربات پیام مناسب بفرسته
    raise RuntimeError("ALL_AI_FAILED")
