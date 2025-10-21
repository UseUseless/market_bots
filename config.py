import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

# --- API Токены ---
# Эти переменные будут импортированы в trade_client.py
TOKEN_REAL = os.getenv("TINKOFF_TOKEN_REAL")
TOKEN_SANDBOX = os.getenv("TINKOFF_TOKEN_SANDBOX")
ACCOUNT_ID = os.getenv("TINKOFF_ACCOUNT_ID")