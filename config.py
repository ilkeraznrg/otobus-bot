import os
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://your-webapp-url.com") # TWA URL

if not all([TELEGRAM_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Eksik ortam değişkeni! Lütfen .env dosyanızı kontrol edin.")