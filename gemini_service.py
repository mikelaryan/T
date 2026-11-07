"""
ارتباط با هوش مصنوعی — اولویت با مدل‌های رایگان، آخرین سنگر GapGPT (پولی)
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

SUBTITLE_PROMPT = """
تو یک متخصص زیرنویس‌نویسی حرفه‌ای هستیتی و یک مترجم و ویراستار تخصصی محتوای آموزشی هنر و نقاشی .
ورودی تو فقط یک فایل صوتی است. وظیفه‌ات این است:

۱. گفتار انگلیسی داخل فایل صوتی را با دقت بشنوی و متن اصلی را استخراج کنی.
۲. هر جمله را به فارسی روان، کوتاه و خوانا برای مخاطب اینستاگرام ترجمه کنی.
۳. زمان شروع و پایان هر جمله را نسبت به ابتدای فایل صوتی (ثانیه) مشخص کنی (خیلی مهم).

واژه‌نامه تخصصی نقاشی و طراحی (حتما رعایت شود):
- Soft edge = لبه محو / لبه نرم
- Lost edge =  لبه محو
- Value = تیرگی‌روشنی / تن سایه
- Contrast = کنتراست / تضاد
- Side of the pencil = پهلوی مداد
- Lifting the pencil = کم کردن فشار مداد / برداشتن تدریجی مداد

استاندارد زیرنویس:
- هر خط حداکثر ۴ تا ۵ کلمه باشد.
- زمان‌بندی دقیقاً با شروع و پایان صحبت هم‌خوانی داشته باشد(فوقلعاده مهم).
- ترجمه باید محاوره‌ای، طبیعی و بدون کلمات اضافه باشد.

فرمت خروجی: فقط و فقط یک آرایه JSON معتبر، بدون هیچ متن اضافه، بدون ```:

{
  "subtitles": [
    {
      "line_index": 1,
      "start_time": "00:00:01,000",
      "end_time": "00:00:03,500",
      "text": "متن فارسی این خط"
    }
  ]
}

فقط JSON خالص برگردان، بدون هیچ توضیح اضافه.
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
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        cleaned = text.strip().strip("`").removeprefix("json").strip()
        parsed = json.loads(cleaned)
    subtitles = parsed.get("subtitles")
    if not isinstance(subtitles, list) or not subtitles:
        raise ValueError("پاسخ حاوی کلید subtitles معتبر نیست.")
    return parsed

def _sleep_backoff(attempt: int, base: float = 2.0, max_wait: float = 10.0):
    wait = min(base * (2 ** (attempt - 1)), max_wait)
    total = wait + random.uniform(0, 1.0)
    logger.info(f"⏳ {total:.1f} ثانیه صبر می‌کنم...")
    time.sleep(total)

def _try_google_free_model(model_name: str, audio_b64: str, mime_type: str) -> dict | None:
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": SUBTITLE_PROMPT}, {"inline_data": {"mime_type": mime_type, "data": audio_b64}}]}],
        "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"},
    }

    for attempt in range(1, 4):
        logger.info(f"[{model_name}] تلاش {attempt}/3 — گوگل (رایگان)...")
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=120)
            if response.status_code == 200:
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                result = _extract_subtitles_from_text(text)
                result["used_model"] = model_name
                result["api_source"] = "گوگل (رایگان)"
                return result
            logger.warning(f"⚠️ {model_name} → HTTP {response.status_code}")
        except requests.RequestException as e:
            logger.warning(f"خطای شبکه ({model_name}): {e}")
        if attempt < 3:
            _sleep_backoff(attempt)
    return None

def _try_gapgpt(audio_b64: str, mime_type: str) -> dict | None:
    if not getattr(config, "GAPGPT_API_KEY", None) or "YOUR_GAPGPT" in config.GAPGPT_API_KEY:
        logger.error("کلید API گپ‌جی‌پی‌تی تنظیم نشده است.")
        return None

    logger.info("💰 تلاش با GapGPT (پولی)...")
    api_url = "https://api.gapgpt.app/v1/chat/completions"
    model_name = "gemini-3.8-flash"
    headers = {"Authorization": f"Bearer {config.GAPGPT_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": [{"type": "text", "text": SUBTITLE_PROMPT}, {"type": "input_audio", "input_audio": {"data": audio_b64, "format": mime_type.split("/")[-1]}}]}],
        "response_format": {"type": "json_object"}
    }

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=300)
        if response.status_code == 200:
            text = response.json()["choices"][0]["message"]["content"]
            result = _extract_subtitles_from_text(text)
            result["used_model"] = model_name
            result["api_source"] = "GapGPT (پولی)"
            return result
        else:
            logger.error(f"خطای GapGPT: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"خطای شبکه در GapGPT: {e}")
    return None

def transcribe_audio(audio_path: str, is_regen: bool = False) -> dict:
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"فایل صوتی پیدا نشد: {audio_path}")

    audio_b64 = _encode_audio(audio_path)
    mime_type = _detect_mime(audio_path)

    if is_regen:
        logger.info("🔄 درخواست تولید مجدد: مستقیم به API پولی می‌رود.")
        result = _try_gapgpt(audio_b64, mime_type)
        if result: return result
        logger.warning("⚠️ گپ‌جی‌پی‌تی جواب نداد، تلاش با مدل‌های رایگان...")

    free_models = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    for model in free_models:
        result = _try_google_free_model(model, audio_b64, mime_type)
        if result: return result

    logger.warning("⚠️ تمام مدل‌های رایگان ناموفق بودند. سوییچ به آخرین سنگر (GapGPT)...")
    result = _try_gapgpt(audio_b64, mime_type)
    if result: return result

    raise RuntimeError("همه مدل‌ها ناموفق بودند. لطفاً بعداً تلاش کنید.")
