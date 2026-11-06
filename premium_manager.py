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

def to_jalali(date_str: str) -> str:
    """تبدیل تاریخ میلادی (YYYY-MM-DD) به شمسی بدون نیاز به کتابخانه جانبی"""
    if not date_str:
        return "نامشخص"
    try:
        # تاریخ باید به شکل YYYY-MM-DD باشد
        gy, gm, gd = map(int, str(date_str).strip().split("-"))
        
        g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
        if gy > 1600:
            jy = 979
            gy -= 1600
        else:
            jy = 0
            gy -= 621
            
        gy2 = (gy + 1) if gm > 2 else gy
        days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
        
        jy += 33 * (days // 12053)
        days %= 12053
        jy += 4 * (days // 1461)
        days %= 1461
        
        if days > 365:
            jy += (days - 1) // 365
            days = (days - 1) % 365
            
        jm = 1 + (days // 31) if days < 186 else 7 + ((days - 186) // 30)
        jd = 1 + (days % 31) if jm <= 6 else 1 + ((days - 186) % 30)
        
        return f"{jy}/{jm:02d}/{jd:02d}"
    except Exception as e:
        print(f"Date conversion error: {e}")
        return str(date_str)



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
    
    if data.get("status") == "success":
        raw_expiry = data.get('expiry')
        jalali_expiry = to_jalali(raw_expiry)
        logger.info(f"✅ Premium activated for {telegram_id} until {raw_expiry}")
        return True, f"لایسنس شما فعال شد. اعتبار تا: {jalali_expiry}"
    
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
