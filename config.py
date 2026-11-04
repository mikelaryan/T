"""
فایل تنظیمات مرکزی پروژه
کلیدها و مقادیر ثابت اینجا نگهداری میشن
"""

import os

# ===========================================
# کلیدهای اصلی (خوانده شده از Environment Variables)
# ===========================================
# این ساختار میگه: اول برو دنبال متغیر محیطی (توی Render)
# اگه پیدا نکرد (مثل سیستم خودت)، از این مقدار پیش‌فرض استفاده کن

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", 
    "YOUR_GEMINI_API_KEY_HERE" # 👈 توکن خودت رو اینجا بذار
)

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY", 
    "YOUR_GEMINI_API_KEY_HERE" # 👈 کلید جمینای خودت رو اینجا بذار
)


# ===========================================
# تنظیمات اختیاری
# ===========================================

# لیست مدل‌های جمینای به ترتیب اولویت
# اگر مدل اول شلوغ بود (503)، خودکار میره سراغ مدل بعدی
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash"
]

# ===========================================
# تنظیمات کلادفلر (نسخه پرمیوم)
# ===========================================
PREMIUM_WORKER_URL = "https://tolider-premium-api.mikelshayan.workers.dev/"
PREMIUM_SECRET_KEY = "tolider_premium_secret_2026_x9y8z7"


# سطح لاگ (DEBUG / INFO / WARNING / ERROR)
LOG_LEVEL = "INFO"


