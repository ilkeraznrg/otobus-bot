import logging
import re
import uuid
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from datetime import datetime
from telegram import (
    Update, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    WebAppInfo, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    BotCommand
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from supabase import create_client, Client
import config

# Logging Yapılandırması
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logger = logging.getLogger(__name__)

# --- RENDER HEALTH CHECK SERVER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot 7/24 Aktif!")

    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Render Portu Dinleniyor: {port}")
    server.serve_forever()
# ----------------------------------

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

PLAKA, ISLEM, GARANTI, FOTO = range(4)

def format_plaka(plaka: str) -> str:
    return re.sub(r"\s+", " ", plaka.strip().upper())


# --- BOT AÇILIŞINDA TELEGRAM MENÜSÜNE KOMUTLARI EKLEME ---
async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Botu Başlat ve Menüyü Aç"),
        BotCommand("yeni_kayit", "Yeni Servis Kaydı Oluştur"),
        BotCommand("iptal", "Devam Eden İşlemi İptal Et")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot komut menüsü Telegram'a kaydedildi.")


# --- KOMUT VE GENEL MESAJ HANDLERLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    
    keyboard = [
        [KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))],
        [KeyboardButton(text="➕ Yeni Servis Kaydı")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    welcome_text = (
        f"Merhaba {user.first_name}! 🚌\n\n"
        f"Belediye Otobüs Teknik Takip Botuna hoş geldiniz.\n\n"
        f"• Tam plaka (`46 H 0123`) veya kısmi numara (`0123`) yazarak son servis kayıtlarını sorgulayabilirsiniz.\n"
        f"• Yeni servis kaydı açmak için /yeni_kayit yazabilir veya **'➕ Yeni Servis Kaydı'** butonuna basabilirsiniz.\n"
        f"• Tüm geçmiş servis kayıtlarını ve fotoğrafları görmek için **'📱 Servis Panelini Aç'** butonunu kullanabilirsiniz."
    )
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")


async def otobus_detay_goster(message_or_query, plaka: str):
    """Bulunan plakanın verilerini Supabase'den çekip mesaj atar."""
    try:
        otobus_res = supabase.table("otobusler").select("*").eq("plaka", plaka).execute()
        
        if not otobus_res.data:
            await message_or_query.reply_text(f"⚠️ **{plaka}** plakalı otobüs veritabanında bulunamadı.", parse_mode="Markdown")
            return

        otobus = otobus_res.data[0]
        servis_res = (
            supabase.table("servis_kayitlari")
            .select("*")
            .eq("plaka", plaka)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )

        msg = f"🚌 ARAÇ BİLGİSİ\n"
        msg += f"Plaka: {otobus['plaka']}\n"
        msg += f"Hat No: {otobus.get('hat_no') or 'Belirtilmedi'}\n"
        msg += f"Sürücü İletişim: {otobus.get('sofor_iletisim') or 'Belirtilmedi'}\n"
        msg += f"───────────────────\n\n"

        if servis_res.data:
            msg += f"🛠 SON SERVİS KAYITLARI:\n\n"
            for kayit in servis_res.data:
                msg += f"📅 Tarih: {kayit['tarih']}\n"
                msg += f"🔧 İşlem: {kayit['yapilan_islem']}\n"
                msg += f"🛡 Garanti Bitiş: {kayit.get('garanti_bitis') or 'Yok'}\n"
                if kayit.get('foto_url'):
                    msg += f"🖼 Servis Fotoğrafı: {kayit['foto_url']}\n"
                msg += f"---------------------\n"
        else:
            msg += "ℹ️ Bu araca ait henüz bir servis kaydı bulunmuyor."

        await message_or_query.reply_text(msg)
    except Exception as e:
        logger.error(f"Sorgu hatası: {e}")
        await message_or_query.reply_text("❌ Sorgulama yapılırken bir hata oluştu.")


async def genel_mesaj_fonsiyonu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_text = update.message.text.strip()

    if raw_text in ["📱 Servis Panelini Aç", "➕ Yeni Servis Kaydı"]:
        return

    clean_text = re.sub(r"\s+", "", raw_text.upper())

    # 1. KONTROL: Tam Plaka mı? (Örn: 46 H 0123, 46H0123)
    plaka_regex = r"^(0[1-9]|[1-8][0-9])([A-Z]{1,3})(\d{2,4})$"
    match = re.match(plaka_regex, clean_text)

    if match:
        il, harf, rakam = match.groups()
        plaka = f"{il} {harf} {rakam}"
        await otobus_detay_goster(update.message, plaka)
        return

    # 2. KONTROL: Kısmi Arama mı? (Örn: "0123", "46 H", "12")
    if len(clean_text) >= 2 and any(char.isdigit() for char in clean_text):
        try:
            res = supabase.table("otobusler").select("plaka").ilike("plaka", f"%{clean_text}%").limit(5).execute()
            
            if res.data:
                keyboard = []
                for item in res.data:
                    p = item['plaka']
                    keyboard.append([InlineKeyboardButton(f"🚌 {p}", callback_data=f"plaka_sec_{p}")])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(
                    f"🔍 **'{raw_text}'** aramasıyla eşleşen otobüsler bulundu:\nLütfen bir plaka seçin:",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return
        except Exception as e:
            logger.error(f"Kısmi arama hatası: {e}")

    # 3. KONTROL: Geçersiz Metinler İçin Rehber Menü
    keyboard = [
        [KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))],
        [KeyboardButton(text="➕ Yeni Servis Kaydı")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    rehber_mesaj = (
        "🤖 **Nasıl Yardımcı Olabilirim?**\n\n"
        "🔍 **Eski Kayıt Sorgulama:** Tam plaka (`46 H 0123`) veya kısmi numara (`0123`) yazabilirsiniz.\n\n"
        "📝 **Yeni Kayıt Ekleme:** `/yeni_kayit` yazın veya aşağıdaki **'➕ Yeni Servis Kaydı'** butonuna basın.\n\n"
        "🌐 **Tüm Liste & Web Panel:** Tüm geçmiş servis verilerini görmek için **'📱 Servis Panelini Aç'** butonunu kullanın.\n\n"
        "❌ **İşlem İptali:** Devam eden bir kaydı iptal etmek için `/iptal` yazabilirsiniz."
    )

    await update.message.reply_text(rehber_mesaj, reply_markup=reply_markup, parse_mode="Markdown")


async def plaka_buton_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kısmi arama sonucu çıkan butonlara tıklanınca çalışır."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("plaka_sec_"):
        plaka = data.replace("plaka_sec_", "")
        await otobus_detay_goster(query.message, plaka)


# --- SERVİS KAYDI CONVERSATION HANDLER ---

async def kayit_baslat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    
    if update.message.photo:
        context.user_data['photo'] = update.message.photo[-1]
        await update.message.reply_text("📸 Fotoğraf alındı!\n\nLütfen işlem yapılan Otobüs Plakasını girin:")
    else:
        await update.message.reply_text("📝 Yeni Servis Kaydı\n\nLütfen otobüs plakasını girin (Örn: 46 H 0123):")
    
    return PLAKA


async def kayit_plaka_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_text = update.message.text.strip()
    clean_text = re.sub(r"\s+", "", raw_text.upper())
    
    plaka_regex = r"^(0[1-9]|[1-8][0-9])([A-Z]{1,3})(\d{2,4})$"
    match = re.match(plaka_regex, clean_text)
    
    if match:
        il, harf, rakam = match.groups()
        plaka = f"{il} {harf} {rakam}"
    else:
        plaka = format_plaka(raw_text)

    context.user_data['plaka'] = plaka

    try:
        res = supabase.table("otobusler").select("plaka").eq("plaka", plaka).execute()
        if not res.data:
            supabase.table("otobusler").insert({"plaka": plaka}).execute()
    except Exception as e:
        logger.error(f"Plaka kontrol/ekleme hatası: {e}")

    await update.message.reply_text(f"✅ Plaka: {plaka}\n\nYapılan teknik işlemi / tamiri detaylıca yazın:")
    return ISLEM


async def kayit_islem_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['islem'] = update.message.text
    await update.message.reply_text(
        "Garanti bitiş tarihi var mı?\n"
        "Varsa YYYY-AA-GG formatında yazın (Örn: 2026-12-31).\n"
        "Yoksa Pas yazın veya aşağıdaki butona basın.",
        reply_markup=ReplyKeyboardMarkup([["Pas"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GARANTI


async def kayit_garanti_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    
    if text.lower() == "pas":
        context.user_data['garanti'] = None
    else:
        try:
            valid_date = datetime.strptime(text, "%Y-%m-%d").date()
            context.user_data['garanti'] = str(valid_date)
        except ValueError:
            await update.message.reply_text("⚠️ Geçersiz tarih formatı! Lütfen YYYY-AA-GG şeklinde girin veya 'Pas' yazın:")
            return GARANTI

    if 'photo' not in context.user_data:
        await update.message.reply_text(
            "📷 Servis işlemiyle ilgili bir fotoğraf gönderin veya fotoğraf yoksa Pas yazın:",
            reply_markup=ReplyKeyboardMarkup([["Pas"]], resize_keyboard=True, one_time_keyboard=True)
        )
        return FOTO
    else:
        return await kayit_tamamla(update, context)


async def kayit_foto_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        context.user_data['photo'] = update.message.photo[-1]
    
    return await kayit_tamamla(update, context)


async def kayit_tamamla(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("⏳ Kayıt işleniyor, lütfen bekleyin...")
    
    foto_url = None
    photo_item = context.user_data.get('photo')

    if photo_item:
        try:
            file = await context.bot.get_file(photo_item.file_id)
            file_bytes = await file.download_as_bytearray()
            
            file_path = f"{context.user_data['plaka']}_{uuid.uuid4().hex[:8]}.jpg"
            
            supabase.storage.from_("servis-fotolari").upload(
                file_path, 
                bytes(file_bytes),
                file_options={"content-type": "image/jpeg"}
            )
            
            foto_url = supabase.storage.from_("servis-fotolari").get_public_url(file_path)
        except Exception as e:
            logger.error(f"Fotoğraf yükleme hatası: {e}")

    try:
        kayit_payload = {
            "plaka": context.user_data['plaka'],
            "yapilan_islem": context.user_data['islem'],
            "tarih": str(datetime.now().date())
        }
        
        if context.user_data.get('garanti'):
            kayit_payload["garanti_bitis"] = context.user_data['garanti']
            
        if foto_url:
            kayit_payload["foto_url"] = foto_url

        supabase.table("servis_kayitlari").insert(kayit_payload).execute()
        
        keyboard = [
            [KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))],
            [KeyboardButton(text="➕ Yeni Servis Kaydı")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text("🎉 Servis kaydı başarıyla eklendi!", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"DB Kayıt hatası: {e}")
        await update.message.reply_text(f"❌ Servis kaydı oluşturulurken hata oluştu: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def kayit_iptal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    keyboard = [
        [KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))],
        [KeyboardButton(text="➕ Yeni Servis Kaydı")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("❌ İşlem iptal edildi.", reply_markup=reply_markup)
    return ConversationHandler.END


# --- MAIN ---

def main():
    Thread(target=run_health_check_server, daemon=True).start()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    kayit_handler = ConversationHandler(
        entry_points=[
            CommandHandler("yeni_kayit", kayit_baslat),
            MessageHandler(filters.Regex("^➕ Yeni Servis Kaydı$"), kayit_baslat),
            MessageHandler(filters.PHOTO, kayit_baslat)
        ],
        states={
            PLAKA: [MessageHandler(filters.TEXT & ~filters.COMMAND, kayit_plaka_al)],
            ISLEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, kayit_islem_al)],
            GARANTI: [MessageHandler(filters.TEXT & ~filters.COMMAND, kayit_garanti_al)],
            FOTO: [
                MessageHandler(filters.PHOTO, kayit_foto_al),
                MessageHandler(filters.Regex("^PAS$|^Pas$"), kayit_tamamla)
            ],
        },
        fallbacks=[CommandHandler("iptal", kayit_iptal)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(kayit_handler)
    app.add_handler(CallbackQueryHandler(plaka_buton_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, genel_mesaj_fonsiyonu))

    logger.info("Bot ve Sunucu başlatılıyor...")
    app.run_polling(drop_pending_updates=True, poll_interval=1.0)


if __name__ == "__main__":
    main()
