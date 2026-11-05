"""
ماژول ارتباط با Cloudflare Worker (نسخه پرمیوم)
شامل: فعال‌سازی لایسنس، بررسی احراز هویت و مصرف اعتبار روزانه
"""

import logging
import requests
import config

logger = logging.getLogger(__name__)

# هدرهای مشترک برای ارتباط با کلادفلر
def _get_headers():
    return {
        "Authorization": f"Bearer {config.PREMIUM_SECRET_KEY}",
        "Content-Type": "application/json"
    }

def _send_request(payload: dict) -> dict:
    """ارسال درخواست به ورکر و مدیریت خطاها"""
    try:
        response = requests.post(
            config.PREMIUM_WORKER_URL,
            headers=_get_headers(),
            json=payload,
            timeout=15
        )
        if response.status_code not in (200, 400):
            logger.error(f"Cloudflare Premium HTTP Error {response.status_code}: {response.text}")
            return {"status": "error", "message": "خطا در ارتباط با سرور."}
            
        try:
            return response.json()
        except Exception as e:
            logger.error(f"خطا در خواندن JSON از کلادفلر: {e}")
            return {"status": "error", "message": "پاسخ نامعتبر از سرور."}
            
    except Exception as e:
        logger.error(f"خطای شبکه در ارتباط با کلادفلر (پرمیوم): {e}")
        return {"status": "error", "message": "خطا در اتصال به سیستم."}

# ===========================================
# ۱. فعال‌سازی لایسنس
# ===========================================
def activate_premium(telegram_id: int, username: str, license_key: str) -> tuple[bool, str]:
    payload = {
        "action": "activate",
        "telegram_id": str(telegram_id),
        "username": username or "NoUsername",
        "license_key": license_key.strip()
    }
    
    data = _send_request(payload)
    if data.get("status") == "success":
        logger.info(f"✅ Premium activated for {telegram_id} until {data.get('expiry')}")
        return True, f"لایسنس شما فعال شد. اعتبار تا: {data.get('expiry')}"
    
    return False, data.get("message", "خطای نامشخص در فعال‌سازی.")

# ===========================================
# ۲. بررسی احراز هویت
# ===========================================
def check_premium_auth(telegram_id: int) -> tuple[bool, dict | str]:
    payload = {
        "action": "auth",
        "telegram_id": str(telegram_id)
    }
    
    data = _send_request(payload)
    if data.get("status") == "success":
        return True, data.get("user", {})
    elif data.get("status") == "expired":
        return False, "اشتراک شما منقضی شده است. لطفاً برای تمدید اقدام کنید."
        
    return False, data.get("message", "شما لایسنس فعالی ندارید.")

# ===========================================
# ۳. چک کردن محدودیت روزانه (بدون کم کردن)
# ===========================================
def check_daily_limit(telegram_id: int) -> tuple[bool, str]:
    payload = {
        "action": "check_limit",
        "telegram_id": str(telegram_id)
    }
    
    data = _send_request(payload)
    if data.get("status") == "success":
        remaining = data.get("remaining", 0)
        return True, f"شما امروز {remaining} استفاده دیگر مجاز هستید."
    
    return False, data.get("message", "خطا در بررسی سقف روزانه.")

# ===========================================
# ۴. کم کردن شارژ (فقط بعد از موفقیت)
# ===========================================
def consume_daily_limit(telegram_id: int) -> tuple[bool, str]:
    payload = {
        "action": "consume",
        "telegram_id": str(telegram_id)
    }
    
    data = _send_request(payload)
    if data.get("status") == "success":
        remaining = data.get("remaining", 0)
        logger.info(f"User {telegram_id} consumed 1 limit. Remaining: {remaining}")
        return True, f"{remaining} استفاده باقی‌مانده."
    
    return False, data.get("message", "خطا در کم کردن سقف.")
