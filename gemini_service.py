"""
ارتباط با هوش مصنوعی — با پرامپت مخصوص سینک و مقاوم‌سازی کامل JSON
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
# پرامپت جدید (مخصوص سینک دقیق و ریلزهای تند)
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
- طول خطوط: هر خط زیرنویس باید حداکثر ۳۰ کاراکتر و حداکثر ۴ کلمه باشد (برای خوانایی در اینستاگرام).
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

# ===========================================
# تابع استخراج فوق مقاوم JSON (رفع مشکل Enter و فرمت‌های بد)
# ===========================================
def _extract_subtitles_from_text(text: str) -> dict:
    if not text: 
        raise ValueError("پاسخ خالی دریافت شد.")
    
    cleaned = text.strip()
    if "```json" in cleaned: 
        cleaned = cleaned.split("```json")[1].split("```")[0]
    elif "```" in cleaned: 
        cleaned = cleaned.split("```")[1].split("```")[0]
    
    # 👈 پاک کردن تمام Enter ها برای جلوگیری از خرابی JSON
    cleaned = cleaned.replace("\n", "").replace("\r", "")

    # پیدا کردن دقیق آکولاد باز و بسته
    start_idx = cleaned.find('{')
    end_idx = cleaned.rfind('}')
    if start_idx != -1 and end_idx != -1:
        cleaned = cleaned[start_idx:end_idx+1]

    try:
        parsed = json.loads(cleaned, strict=False)
    except json.JSONDecodeError as e:
        logger.error(f"خطای پارس JSON: {e} | متن خام: {text[:300]}")
        raise ValueError("فرمت پاسخ هوش مصنوعی نامعتبر است.")
    
    subtitles = parsed.get("subtitles")
    if not isinstance(subtitles, list) or not subtitles:
        raise ValueError("کلید subtitles معتبر نیست.")
    return parsed

# 👈 تابعی که جا افتاده بود
def _sleep_backoff(attempt: int, base: float = 2.0, max_wait: float = 10.0):
    wait = min(base * (2 ** (attempt - 1)), max_wait)
    total = wait + random.uniform(0, 1.0)
    logger.info(f"⏳ {total:.1f} ثانیه صبر می‌کنم...")
    time.sleep(total)

# ===========================================
# اولویت اول: GapGPT (پولی)
# ===========================================
def _try_gapgpt(audio_b64: str, mime_type: str) -> dict | None:
    if not getattr(config, "GAPGPT_API_KEY", None) or "YOUR_GAPGPT" in config.GAPGPT_API_KEY:
        return None

    logger.info("💰 اولویت اول: تلاش با GapGPT (پولی)...")
    api_url = "https://api.gapgpt.app/v1/chat/completions"
    model_name = "gemini-3.8-flash"
    headers = {"Authorization": f"Bearer {config.GAPGPT_API_KEY}", "Content-Type": "application/json"}
    
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": [{"type": "text", "text": SUBTITLE_PROMPT}, {"type": "input_audio", "input_audio": {"data": audio_b64, "format": mime_type.split("/")[-1]}}]}],
        "response_format": {"type": "json_object"}
    }

    try:
        # تایم‌اوت ۳۰۰ ثانیه (چون پولیه و نباید هدر بره)
        response = requests.post(api_url, headers=headers, json=payload, timeout=300)
        if response.status_code == 200:
            text = response.json()["choices"][0]["message"]["content"]
            result = _extract_subtitles_from_text(text)
            result["used_model"] = model_name
            result["api_source"] = "GapGPT (پولی)"
            logger.info(f"✅ موفق با GapGPT ({len(result['subtitles'])} خط)")
            return result
        else:
            logger.error(f"خطای GapGPT: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"خطای GapGPT: {e}")
    return None

# ===========================================
# اولویت دوم: مدل‌های رایگان گوگل
# ===========================================
def _try_google_free_model(model_name: str, audio_b64: str, mime_type: str, duration: float = 0) -> dict | None:
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": SUBTITLE_PROMPT}, {"inline_data": {"mime_type": mime_type, "data": audio_b64}}]}],
        "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"},
    }

    for attempt in range(1, 3):
        logger.info(f"[{model_name}] تلاش {attempt}/2 — گوگل (رایگان)...")
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=20)
            if response.status_code == 200:
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                result = _extract_subtitles_from_text(text)
                
                if duration > 15 and len(result['subtitles']) < 3:
                    logger.warning(f"⚠️ خروجی {model_name} ناقص است.")
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

    # ۱. اولویت اول: گپ‌جی‌پی‌تی (پولی)
    result = _try_gapgpt(audio_b64, mime_type)
    if result: 
        return result

    logger.warning("⚠️ گپ‌جی‌پی‌تی پاسخ نداد. تلاش با مدل‌های رایگان...")

    # ۲. اولویت‌های بعدی: رایگان‌ها
    free_models = [
        "gemini-3.8-flash",       
        "gemini-3.6-flash",       
        "gemini-3.5-flash-lite"   
    ]
    
    for model in free_models:
        result = _try_google_free_model(model, audio_b64, mime_type, duration)
        if result: 
            return result

    # ۳. خطای نهایی
    raise RuntimeError("ALL_AI_FAILED")
