"""
ارتباط با Gladia AI — دریافت زیرنویس با زمان‌بندی دقیق در سطح کلمه
"""

import os
import time
import logging
import requests
import config

logger = logging.getLogger(__name__)

GLADIA_BASE_URL = "https://api.gladia.io/v2"

def _get_headers():
    return {
        "x-gladia-key": config.GLADIA_API_KEY,
        "Content-Type": "application/json"
    }

def transcribe_with_gladia(audio_path: str) -> list[dict]:
    """
    ارسال فایل صوتی به Gladia و دریافت لیست کلمات با زمان‌بندی دقیق.
    
    خروجی: لیستی از دیکشنری‌ها به شکل:
    [
        {"word": "Hello", "start": 0.12, "end": 0.45},
        {"word": "world", "start": 0.46, "end": 0.89},
        ...
    ]
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"فایل صوتی پیدا نشد: {audio_path}")

    if not config.GLADIA_API_KEY or "YOUR_GLADIA" in config.GLADIA_API_KEY:
        raise ValueError("کلید API گلدیا تنظیم نشده است.")

    # 👈 مرحله ۱: آپلود فایل صوتی به Gladia
    logger.info("[Gladia] در حال آپلود فایل صوتی...")
    
    try:
        with open(audio_path, "rb") as f:
            # برای آپلود، از multipart/form-data استفاده می‌کنیم
            files = {"audio": (os.path.basename(audio_path), f, "audio/wav")}
            # هدر Content-Type رو برای آپلود حذف می‌کنیم تا requests خودش multipart رو ست کنه
            upload_headers = {"x-gladia-key": config.GLADIA_API_KEY}
            
            upload_response = requests.post(
                f"{GLADIA_BASE_URL}/upload",
                headers=upload_headers,
                files=files,
                timeout=120
            )
            
            if upload_response.status_code != 200:
                logger.error(f"[Gladia] خطا در آپلود: {upload_response.status_code} - {upload_response.text[:300]}")
                raise RuntimeError("GLADIA_UPLOAD_FAILED")
                
            audio_url = upload_response.json().get("audio_url")
            logger.info(f"[Gladia] آپلود موفق. URL: {audio_url}")
            
    except requests.RequestException as e:
        logger.error(f"[Gladia] خطای شبکه در آپلود: {e}")
        raise RuntimeError("GLADIA_UPLOAD_FAILED")

    # 👈 مرحله ۲: درخواست شروع پردازش (Async)
    logger.info("[Gladia] ارسال درخواست پردازش...")
    
    payload = {
        "audio_url": audio_url,
        "language_config": {"languages": ["en"]},
        "toggle_accurate_words_timestamps": True # 👈 کلید دریافت زمان‌بندی دقیق
    }
    
    try:
        job_response = requests.post(
            f"{GLADIA_BASE_URL}/pre-recorded",
            headers=_get_headers(),
            json=payload,
            timeout=30
        )
        
        if job_response.status_code not in (200, 201):
            logger.error(f"[Gladia] خطا در شروع پردازش: {job_response.status_code} - {job_response.text[:300]}")
            raise RuntimeError("GLADIA_JOB_FAILED")
            
        job_data = job_response.json()
        job_id = job_data.get("id") or job_data.get("result", {}).get("id")
        
        if not job_id:
            logger.error(f"[Gladia] پاسخ نامعتبر: {job_data}")
            raise RuntimeError("GLADIA_JOB_FAILED")
            
        logger.info(f"[Gladia] پردازش شروع شد. Job ID: {job_id}")
        
    except requests.RequestException as e:
        logger.error(f"[Gladia] خطای شبکه در شروع پردازش: {e}")
        raise RuntimeError("GLADIA_JOB_FAILED")

    # 👈 مرحله ۳: منتظر ماندن و گرفتن نتیجه (Polling)
    logger.info("[Gladia] در حال انتظار برای تکمیل پردازش...")
    
    max_wait = 120 # حداکثر ۲ دقیقه صبر می‌کنیم
    wait_interval = 3 # هر ۳ ثانیه چک می‌کنیم
    elapsed = 0
    
    while elapsed < max_wait:
        time.sleep(wait_interval)
        elapsed += wait_interval
        
        try:
            result_response = requests.get(
                f"{GLADIA_BASE_URL}/pre-recorded/{job_id}",
                headers=_get_headers(),
                timeout=30
            )
            
            if result_response.status_code != 200:
                logger.warning(f"[Gladia] خطا در دریافت وضعیت: {result_response.status_code}")
                continue
                
            result_data = result_response.json()
            status = result_data.get("status")
            
            if status == "done":
                logger.info("[Gladia] پردازش با موفقیت به پایان رسید.")
                return _extract_words(result_data)
            elif status == "error":
                logger.error(f"[Gladia] پردازش با خطا مواجه شد: {result_data}")
                raise RuntimeError("GLADIA_TRANSCRIPTION_ERROR")
            else:
                logger.info(f"[Gladia] وضعیت: {status} (زمان سپری شده: {elapsed}s)")
                
        except requests.RequestException as e:
            logger.warning(f"[Gladia] خطای شبکه در Polling: {e}")
            
    logger.error("[Gladia] زمان انتظار به پایان رسید.")
    raise RuntimeError("GLADIA_TIMEOUT")


def _extract_words(result_data: dict) -> list[dict]:
    """استخراج لیست کلمات از پاسخ Gladia"""
    words = []
    
    transcription = result_data.get("result", {}).get("transcription", {})
    utterances = transcription.get("utterances", [])
    
    for utterance in utterances:
        # هر utterance ممکنه چند کلمه داشته باشه
        for word_info in utterance.get("words", []):
            words.append({
                "word": word_info.get("word", "").strip(),
                "start": word_info.get("start", 0.0),
                "end": word_info.get("end", 0.0)
            })
            
    if not words:
        logger.warning("[Gladia] هیچ کلمه‌ای در پاسخ پیدا نشد.")
        
    return words
