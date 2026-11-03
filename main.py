import logging
from bot import run_bot

# 👈 این دو خط باعث می‌شوند لاگ‌ها در Render دیده شوند
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if __name__ == "__main__":
    run_bot()
