"""
ارتباط با Gemini API برای دریافت زیرنویس از روی فایل صوتی
بدون کتابخانه‌ی رسمی — فقط requests
استراتژی جدید: ۱ بار تلاش با ۳.۸، ۳ بار با ۳.۱ پرو، ۳ بار با ۳.۵
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
# پرامپت (دست‌نخوردنی)
# ===========================================
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
        raise ValueError(
            f"حجم فایل صوتی ({size / 1024 / 1024:.1f}MB) از حد مجاز "
            f"Gemini ({MAX_INLINE_BYTES / 1024 / 1024}MB) بیشتره."
        )
    with open(audio_path, "rb") as f:
        data = f.read()
    logger.info(f"فایل صوتی خوانده شد: {size / 1024 / 1024:.2f}MB")
    return base64.b64encode(data).decode("utf-8")

def _detect_mime(audio_path: str) -> str:
    ext = os.path.splitext(audio_path)[1].lower()
    return {
        ".wav": "audio/wav", ".mp3": "audio/mp3",
        ".m4a": "audio/m4a", ".aac": "audio/aac",
        ".ogg": "audio/ogg", ".flac": "audio/flac",
        ".webm": "audio/webm",
    }.get(ext, "audio/wav")

def _build_payload(audio_b64: str, mime_type: str) -> dict:
    return {
        "contents": [{
            "parts": [
                {"text": SUBTITLE_PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": audio_b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json",
        },
    }

def _extract_subtitles(response_json: dict) -> dict:
    try:
        text = response_json["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"ساختار پاسخ غیرمنتظره: {response_json}") from e

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        cleaned = text.strip().strip("`").removeprefix("json").strip()
        parsed = json.loads(cleaned)

    subtitles = parsed.get("subtitles")
    if not isinstance(subtitles, list) or not subtitles:
        raise ValueError("پاسخ حاوی کلید subtitles معتبر نیست.")
    return parsed

def _sleep_backoff(attempt: int, base: float = 5.0, max_wait: float = 30.0):
    """تاخیر تصاعدی با جیتر"""
    wait = min(base * (2 ** (attempt - 1)), max_wait)
    jitter = random.uniform(0, 2.0)
    total = wait + jitter
    logger.info(f"⏳ {total:.1f} ثانیه صبر می‌کنم قبل از تلاش بعدی...")
    time.sleep(total)

# ===========================================
# تابع اصلی
# ===========================================
def transcribe_audio(audio_path: str) -> dict:
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"فایل صوتی پیدا نشد: {audio_path}")
    if not config.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY در config.py تنظیم نشده است.")

    audio_b64 = _encode_audio(audio_path)
    mime_type = _detect_mime(audio_path)
    payload = _build_payload(audio_b64, mime_type)

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": config.GEMINI_API_KEY,
    }

    # 👈 استراتژی جدید: ۳.۸ (۱ بار) -> ۳.۱ پرو (۳ بار) -> ۳.۵ (۳ بار)
    models_config = [
        {"name": "gemini-3.8-flash", "max_retries": 1},
        {"name": "gemini-3.1-pro-preview", "max_retries": 3},
        {"name": "gemini-3.5-flash", "max_retries": 3}
    ]

    last_error = None

    for config_item in models_config:
        model = config_item["name"]
        max_retries = config_item["max_retries"]
        
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        
        logger.info(f"📡 شروع پردازش با مدل: {model} (حداکثر {max_retries} تلاش)")

        for attempt in range(1, max_retries + 1):
            logger.info(f"[{model}] تلاش {attempt}/{max_retries} — در حال ارسال درخواست...")
            t0 = time.time()
            
            try:
                response = requests.post(api_url, headers=headers, json=payload, timeout=300)
            except requests.RequestException as e:
                last_error = e
                logger.warning(f"خطای شبکه ({model}, تلاش {attempt}): {e}")
                if attempt < max_retries:
                    _sleep_backoff(attempt)
                continue

            elapsed = time.time() - t0
            logger.info(f"⏱ {model} پاسخ داد در {elapsed:.1f}s (کد {response.status_code})")

            if response.status_code == 200:
                result = _extract_subtitles(response.json())
                logger.info(f"✅ زیرنویس دریافت شد با {model} ({len(result['subtitles'])} خط)")
                
                # 👈 اضافه کردن اسم مدل و منبع (برای گزارش به ادمین)
                result["used_model"] = model
                result["api_source"] = "API رایگان گوگل" # الان رایگانه، بعدا برای گپ‌جی‌پی‌تی عوض میشه
                
                return result

            # خطاهای موقت
            if response.status_code in (503, 429, 500, 502, 504):
                body = response.text[:300]
                logger.warning(f"⚠️ {model} → HTTP {response.status_code}: {body}")
                last_error = f"HTTP {response.status_code}: {body}"

                if attempt < max_retries:
                    _sleep_backoff(attempt)
                    continue
                else:
                    break # برو سراغ مدل بعدی

            # خطاهای غیرقابل جبران
            raise RuntimeError(f"Gemini API خطا داد (کد {response.status_code}): {response.text[:500]}")

        logger.warning(f"❌ مدل {model} جواب نداد. سوییچ به مدل بعدی...")

    # اگر از حلقه بیرون اومدیم یعنی همه مدل‌ها شکست خوردن
    raise RuntimeError(f"همه مدل‌ها ناموفق بودند. آخرین خطا: {last_error}")
