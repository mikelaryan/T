"""
فایل تنظیمات مرکزی پروژه
"""

import os

# ===========================================
# کلیدهای اصلی (خوانده شده از Environment Variables)
# ===========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE")
GAPGPT_API_KEY = os.getenv("GAPGPT_API_KEY", "YOUR_GAPGPT_API_KEY_HERE")

# ===========================================
# تنظیمات کلادفلر (نسخه پرمیوم)
# ===========================================
PREMIUM_WORKER_URL = os.getenv("PREMIUM_WORKER_URL", "https://tolider-premium-api.mikelshayan.workers.dev/")
PREMIUM_SECRET_KEY = os.getenv("PREMIUM_SECRET_KEY", "tolider_premium_secret_2026_x9y8z7")

# ===========================================
# آیدی عددی ادمین
# ===========================================
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "5080371184"))

# سطح لاگ
LOG_LEVEL = "INFO"
