import logging
import re
import uuid
import os
import io
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from datetime import datetime
from PIL import Image
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
logger = logging.getLogger(__name__)

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

# Konuşma Adımları (States)
PLAKA, ISLEM, GARANTI, UCRET, SOFOR, FOTO = range(6)

def format_plaka_standart(raw_text: str) -> str:
    """
    Kullanıcının girdiği plakayı temizler ve harften sonraki rakamı 4 haneye tamamlar.
    Örn: '46h94' -> '46 H 0094', '46 h 123' -> '46 H 0123'
    """
    clean_text = re.sub(r"\s+", "", raw_text.upper())
    plaka_regex = r"^(0[1-9]|[1-8][0-9])([A-Z]{1,3})(\d{1,4})$"
    match = re.match(plaka_regex, clean_text)
    
    if match:
        il, harf, rakam = match.groups()
        rakam_padded = rakam.zfill(4) # Rakamın solunu 4 haneye tamamlayacak şekilde 0 ekler
        return f"{il} {harf} {rakam_padded}"
    return None

def format_date_for_display(date_str: str) -> str:
    """YYYY-MM-DD formatındaki tarihi GG.AA.YYYY yapar."""
    if not date_str:
        return ""
    if "." in date_str:
        return date_str
    try:
        parts = date_str.split("-")
        if len(parts) == 3:
            return f"{parts[2]}.{parts[1]}.{parts[0]}"
    except Exception:
        pass
    return date_str

def compress_image(image_bytes: bytes) -> bytes:
    """Fotoğrafı kalitesini bozmadan sıkıştırır ve maksimum boyutlandırır."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        img.thumbnail((1280, 1280))
        
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=75, optimize=True)
        return output.getvalue()
    except Exception as e:
        logger.error(f"Fotoğraf sıkıştırma hatası: {e}")
        return image_bytes

def kullanici_yetkili_mi(telegram_id: int) -> bool:
    try:
        res = supabase.table("yetkili_kullanicilar").select("id").eq("telegram_id", telegram_id).execute()
        return len(res.data) > 0
    except Exception as e:
        logger.error(f"Yetki kontrol hatası: {e}")
        return False


# --- BOT AÇILIŞINDA TELEGRAM MENÜSÜNE KOMUTLARI EKLEME ---
async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Botu Başlat ve Menüyü Gör"),
        BotCommand("giris", "Usta Girişi Yap"),
        BotCommand("cikis", "Oturumu Kapat"),
        BotCommand("yeni_kayit", "Yeni Servis Kaydı Oluştur"),
        BotCommand("iptal", "Devam Eden İşlemi İptal Et")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot komut menüsü Telegram'a kaydedildi.")


# --- KULLANICI GİRİŞ VE ÇIKIŞ İŞLEMLERİ ---

async def giris_yap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    
    if kullanici_yetkili_mi(telegram_id):
        await update.message.reply_text("✅ Zaten giriş yapmış durumdasınız. Kayıt oluşturabilirsiniz.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "🔒 **Usta / Yetkili Girişi:**\n\n"
            "Lütfen komutu kullanıcı adınız ve şifrenizle birlikte yazın:\n"
            "`/giris kullanici_adi sifre`\n\n"
            "Örnek: `/giris hasan hasan46.`",
            parse_mode="Markdown"
        )
        return

    k_adi = context.args[0].strip()
    sifre = context.args[1].strip()

    try:
        res = supabase.table("yetkili_kullanicilar").select("*").eq("kullanici_adi", k_adi).eq("sifre", sifre).execute()
        
        if res.data:
            user_rec = res.data[0]
            supabase.table("yetkili_kullanicilar").update({"telegram_id": telegram_id}).eq("id", user_rec["id"]).execute()
            
            keyboard = [
                [KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))],
                [KeyboardButton(text="➕ Yeni Servis Kaydı")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

            await update.message.reply_text(
                f"🎉 Hoş geldiniz **{user_rec.get('ad_soyad') or k_adi}**!\n"
                f"Giriş başarılı. Artık servis kaydı ekleyebilirsiniz.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ Hatalı kullanıcı adı veya şifre!")
    except Exception as e:
        logger.error(f"Giriş hatası: {e}")
        await update.message.reply_text("❌ Giriş yapılırken bir veritabanı hatası oluştu.")


async def cikis_yap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    try:
        supabase.table("yetkili_kullanicilar").update({"telegram_id": None}).eq("telegram_id", telegram_id).execute()
        await update.message.reply_text("🔒 Oturumunuz kapatıldı. Artık sadece sorgulama yapabilirsiniz.")
    except Exception as e:
        logger.error(f"Çıkış hatası: {e}")


# --- KOMUT VE GENEL MESAJ HANDLERLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    telegram_id = update.effective_user.id
    is_authorized = kullanici_yetkili_mi(telegram_id)

    # Alt Sabit Klavye (Reply Keyboard)
    reply_keyboard = [
        [KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))],
        [KeyboardButton(text="➕ Yeni Servis Kaydı")]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)

    # Mesaj İçi Tıklanabilir Butonlar (Inline Keyboard)
    inline_keyboard = [
        [InlineKeyboardButton("📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))]
    ]

    if is_authorized:
        inline_keyboard.append([InlineKeyboardButton("➕ Yeni Servis Kaydı Aç", callback_data="btn_yeni_kayit")])
        inline_keyboard.append([InlineKeyboardButton("🔒 Oturumu Kapat", callback_data="btn_cikis")])
        status_text = "✅ **Usta Girişi Yapılmış Durumda**"
    else:
        inline_keyboard.append([InlineKeyboardButton("🔑 Usta Girişi Yap (/giris)", callback_data="btn_giris_bilgi")])
        status_text = "ℹ️ **Misafir Modu** (Servis kaydı açmak için usta girişi gereklidir)"

    inline_markup = InlineKeyboardMarkup(inline_keyboard)

    welcome_text = (
        f"Merhaba **{user_name}**! 🚌\n\n"
        f"Belediye Otobüs Teknik Takip Botuna hoş geldiniz.\n\n"
        f"• **Plaka Sorgulama:** Sohbet alanına doğrudan tam plaka (`46 H 0123`) veya kısmi numara (`0123`) yazabilirsiniz.\n"
        f"• **Servis Paneli:** Tüm geçmiş verileri ve fotoğrafları görmek için **'📱 Servis Panelini Aç'** butonunu kullanabilirsiniz.\n\n"
        f"Durum: {status_text}\n\n"
        f"Hızlı işlem yapmak için aşağıdaki butonları kullanabilirsiniz 👇"
    )
    
    await update.message.reply_text(welcome_text, reply_markup=inline_markup, parse_mode="Markdown")
    # Kullanıcı ilk açtığında alt menüyü de gösterelim
    await update.message.reply_text("İşlem Menüsü:", reply_markup=reply_markup)


async def otobus_detay_goster(message_or_query, plaka: str):
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
        msg += f"───────────────────\n\n"

        if servis_res.data:
            msg += f"🛠 SON SERVİS KAYITLARI:\n\n"
            for kayit in servis_res.data:
                msg += f"📅 Tarih: {format_date_for_display(str(kayit['tarih']))}\n"
                msg += f"🔧 İşlem: {kayit['yapilan_islem']}\n"
                if kayit.get('ucret'):
                    msg += f"💰 Ücret: {kayit['ucret']}\n"
                if kayit.get('sofor_bilgi'):
                    msg += f"👤 Şoför / İletişim: {kayit['sofor_bilgi']}\n"
                garanti_display = format_date_for_display(str(kayit.get('garanti_bitis') or '')) or 'Yok'
                msg += f"🛡 Garanti Bitiş: {garanti_display}\n"
                if kayit.get('foto_url'):
                    msg += f"🖼 Servis Fotoğrafı: {kayit['foto_url']}\n"
                msg += f"---------------------\n"
        else:
            msg += "ℹ️ Bu araca ait henüz bir servis kaydı bulunmuyor."

        await message_or_query.reply_text(msg)
    except Exception as e:
        logger.error(f"Sorgu hatası: {e}")


async def genel_mesaj_fonsiyonu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_text = update.message.text.strip()

    if raw_text in ["📱 Servis Panelini Aç", "➕ Yeni Servis Kaydı"]:
        return

    clean_text = re.sub(r"\s+", "", raw_text.upper())

    # 1. Tam Plaka Kontrolü
    plaka_regex = r"^(0[1-9]|[1-8][0-9])([A-Z]{1,3})(\d{2,4})$"
    match = re.match(plaka_regex, clean_text)

    if match:
        il, harf, rakam = match.groups()
        plaka = f"{il} {harf} {rakam}"
        await otobus_detay_goster(update.message, plaka)
        return

    # 2. Kısmi Arama
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

    # 3. Rehber Menü
    keyboard = [
        [KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))],
        [KeyboardButton(text="➕ Yeni Servis Kaydı")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    rehber_mesaj = (
        "🤖 **Nasıl Yardımcı Olabilirim?**\n\n"
        "🔍 **Eski Kayıt Sorgulama:** Tam plaka (`46 H 0123`) veya kısmi numara (`0123`) yazabilirsiniz.\n\n"
        "🌐 **Tüm Liste & Web Panel:** Geçmiş tüm verileri görmek için **'📱 Servis Panelini Aç'** butonunu kullanın.\n\n"
        "📝 **Yeni Kayıt Ekleme:** Ustaların kayıt ekleyebilmesi için önce `/giris kullanici_adi sifre` yapması gerekir."
    )

    await update.message.reply_text(rehber_mesaj, reply_markup=reply_markup, parse_mode="Markdown")


async def buton_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("plaka_sec_"):
        plaka = data.replace("plaka_sec_", "")
        await otobus_detay_goster(query.message, plaka)

    elif data == "btn_giris_bilgi":
        await query.message.reply_text(
            "🔒 **Usta Girişi Nasıl Yapılır?**\n\n"
            "Sohbet alanına kullanıcı adınızı ve şifrenizi şu şekilde yazıp gönderin:\n"
            "`/giris kullanici_adi sifre`\n\n"
            "*(Örn: `/giris hasan hasan46.`)*",
            parse_mode="Markdown"
        )

    elif data == "btn_yeni_kayit":
        if kullanici_yetkili_mi(query.from_user.id):
            await query.message.reply_text("📝 Yeni Servis Kaydı\n\nLütfen otobüs plakasını girin (Örn: 46 H 0123):")
            # Conversation handler başlatmak için komut tetiklemesi yönlendirmesi
            return
        else:
            await query.message.reply_text("🔒 Kayıt ekleyebilmek için lütfen giriş yapın: `/giris kullanici_adi sifre`", parse_mode="Markdown")

    elif data == "btn_cikis":
        try:
            supabase.table("yetkili_kullanicilar").update({"telegram_id": None}).eq("telegram_id", query.from_user.id).execute()
            await query.message.reply_text("🔒 Oturumunuz kapatıldı. Artık misafir modundasınız.")
        except Exception as e:
            logger.error(f"Çıkış hatası: {e}")


# --- SERVİS KAYDI CONVERSATION HANDLER ---

async def kayit_baslat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not kullanici_yetkili_mi(update.effective_user.id):
        await update.message.reply_text(
            "🔒 **Yetkisiz İşlem!**\n\n"
            "Yeni servis kaydı oluşturabilmek için usta girişi yapmalısınız:\n"
            "`/giris kullanici_adi sifre`\n\n"
            "*(Örn: `/giris hasan hasan46.`)*", 
            parse_mode="Markdown"
        )
        return ConversationHandler.END

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
    
    if not match:
        await update.message.reply_text(
            "⚠️ **Geçersiz plaka formatı!**\n\n"
            "Lütfen geçerli bir otobüs plakası girin (Örn: `46 H 0123` veya `46H0123`):\n"
            "*(İşlemi iptal etmek için /iptal yazabilirsiniz)*",
            parse_mode="Markdown"
        )
        return PLAKA

    il, harf, rakam = match.groups()
    plaka = f"{il} {harf} {rakam}"
    context.user_data['plaka'] = plaka

    try:
        res = supabase.table("otobusler").select("plaka").eq("plaka", plaka).execute()
        if not res.data:
            supabase.table("otobusler").insert({"plaka": plaka}).execute()
    except Exception as e:
        logger.error(f"Plaka kontrol hatası: {e}")

    await update.message.reply_text(f"✅ Plaka: **{plaka}**\n\nYapılan teknik işlemi / tamiri detaylıca yazın:", parse_mode="Markdown")
    return ISLEM


async def kayit_islem_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['islem'] = update.message.text
    await update.message.reply_text(
        "Garanti bitiş tarihi var mı?\n"
        "Varsa GG.AA.YYYY formatında yazın (Örn: 31.12.2026).\n"
        "Yoksa Pas yazın veya aşağıdaki butona basın.",
        reply_markup=ReplyKeyboardMarkup([["Pas"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GARANTI


async def kayit_garanti_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    
    if text.lower() == "pas":
        context.user_data['garanti'] = None
    else:
        parsed_date = None
        for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                parsed_date = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue

        if parsed_date:
            context.user_data['garanti'] = str(parsed_date)
        else:
            await update.message.reply_text("⚠️ Geçersiz tarih formatı! Lütfen GG.AA.YYYY şeklinde girin (Örn: 31.12.2026) veya 'Pas' yazın:")
            return GARANTI

    await update.message.reply_text(
        "💰 Alınan servis ücretini yazın (Örn: 1500 TL).\nYoksa 'Pas' yazın veya aşağıdaki butona basın:",
        reply_markup=ReplyKeyboardMarkup([["Pas"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return UCRET


async def kayit_ucret_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() == "pas":
        context.user_data['ucret'] = None
    else:
        context.user_data['ucret'] = text

    await update.message.reply_text(
        "👤 Şoför Adı ve Telefon Numarasını yazın (Örn: Ahmet Yılmaz - 0532 000 0000).\nYoksa 'Pas' yazın:",
        reply_markup=ReplyKeyboardMarkup([["Pas"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return SOFOR


async def kayit_sofor_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() == "pas":
        context.user_data['sofor_bilgi'] = None
    else:
        context.user_data['sofor_bilgi'] = text

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
    await update.message.reply_text("⏳ Kayıt işleniyor ve fotoğraf optimize ediliyor, lütfen bekleyin...")
    
    foto_url = None
    photo_item = context.user_data.get('photo')

    if photo_item:
        try:
            file = await context.bot.get_file(photo_item.file_id)
            file_bytes = await file.download_as_bytearray()
            
            compressed_bytes = compress_image(bytes(file_bytes))
            file_path = f"{context.user_data['plaka']}_{uuid.uuid4().hex[:8]}.jpg"
            
            supabase.storage.from_("servis-fotolari").upload(
                file_path, 
                compressed_bytes,
                file_options={"content-type": "image/jpeg"}
            )
            
            foto_url = supabase.storage.from_("servis-fotolari").get_public_url(file_path)
        except Exception as e:
            logger.error(f"Fotoğraf yükleme hatası: {e}")

    try:
        bugun_db = datetime.now().strftime("%Y-%m-%d")

        kayit_payload = {
            "plaka": context.user_data['plaka'],
            "yapilan_islem": context.user_data['islem'],
            "tarih": bugun_db
        }
        
        if context.user_data.get('garanti'):
            kayit_payload["garanti_bitis"] = context.user_data['garanti']
        if context.user_data.get('ucret'):
            kayit_payload["ucret"] = context.user_data['ucret']
        if context.user_data.get('sofor_bilgi'):
            kayit_payload["sofor_bilgi"] = context.user_data['sofor_bilgi']
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
            UCRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, kayit_ucret_al)],
            SOFOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, kayit_sofor_al)],
            FOTO: [
                MessageHandler(filters.PHOTO, kayit_foto_al),
                MessageHandler(filters.Regex("^PAS$|^Pas$"), kayit_tamamla)
            ],
        },
        fallbacks=[CommandHandler("iptal", kayit_iptal)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("giris", giris_yap))
    app.add_handler(CommandHandler("cikis", cikis_yap))
    app.add_handler(kayit_handler)
    app.add_handler(CallbackQueryHandler(buton_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, genel_mesaj_fonsiyonu))

    logger.info("Bot ve Sunucu başlatılıyor...")
    app.run_polling(drop_pending_updates=True, poll_interval=1.0)


if __name__ == "__main__":
    main()
