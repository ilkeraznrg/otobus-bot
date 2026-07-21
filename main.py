import logging
import re
import uuid
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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

# Supabase İstemcisi
supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

# Conversation Handler Durumları (States)
PLAKA, ISLEM, GARANTI, FOTO = range(4)


# Helper: Plaka Formatlama
def format_plaka(plaka: str) -> str:
    """Plakayı büyük harfe çevirir ve boşlukları düzenler."""
    return re.sub(r"\s+", " ", plaka.strip().upper())


# --- KOMUT HANDLERLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start komutu ve TWA Butonu."""
    user = update.effective_user
    
    # Persistent Keyboard ile TWA Butonu
    keyboard = [
        [KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    welcome_text = (
        f"Merhaba {user.first_name}! 🚌\n\n"
        f"Belediye Otobüs Teknik Takip Botuna hoş geldiniz.\n\n"
        f"• Doğrudan bir plaka yazarak (Örn: 46 H 0123) son servis kayıtlarını sorgulayabilirsiniz.\n"
        f"• Yeni servis kaydı açmak için bir fotoğraf atabilir veya /yeni_kayit komutunu kullanabilirsiniz.\n"
        f"• Alt menüden Web Paneline erişebilirsiniz."
    )
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)


async def plaka_sorgula(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Metin olarak girilen plakayı arar."""
    raw_text = update.message.text
    
    # Eğer gelen metin buton tetiklemesiyse pas geç
    if raw_text == "📱 Servis Panelini Aç":
        return

    plaka = format_plaka(raw_text)

    try:
        # Otobüs bilgisi al
        otobus_res = supabase.table("otobusler").select("*").eq("plaka", plaka).execute()
        
        if not otobus_res.data:
            text = f"⚠️ **{plaka}** plakalı otobüs sistemde bulunamadı."
            # Inline keyboard ile ekleme seçeneği
            keyboard = [[InlineKeyboardButton("➕ Bu Plakayı Kaydet", callback_data=f"add_{plaka}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
            return

        otobus = otobus_res.data[0]
        
        # Son servis kayıtlarını al (En güncel 3 kayıt)
        servis_res = (
            supabase.table("servis_kayitlari")
            .select("*")
            .eq("plaka", plaka)
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )

        msg = f"🚌 **ARAÇ BİLGİSİ**\n"
        msg += f"**Plaka:** `{otobus['plaka']}`\n"
        msg += f"**Hat No:** {otobus.get('hat_no') or 'Belirtilmedi'}\n"
        msg += f"**Sürücü İletişim:** {otobus.get('sofor_iletisim') or 'Belirtilmedi'}\n"
        msg += f"───────────────────\n\n"

        if servis_res.data:
            msg += f"🛠 **SON SERVİS KAYITLARI:**\n\n"
            for kayit in servis_res.data:
                msg += f"📅 **Tarih:** {kayit['tarih']}\n"
                msg += f"🔧 **İşlem:** {kayit['yapilan_islem']}\n"
                msg += f"🛡 **Garanti Bitiş:** {kayit.get('garanti_bitis') or 'Yok'}\n"
                if kayit.get('foto_url'):
                    msg += f"🖼 [Servis Fotoğrafı]({kayit['foto_url']})\n"
                msg += f"---------------------\n"
        else:
            msg += "ℹ️ Bu araca ait henüz bir servis kaydı bulunmuyor."

        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=False)

    except Exception as e:
        logger.error(f"Sorgu hatası: {e}")
        await update.message.reply_text("❌ Sorgulama yapılırken bir hata oluştu.")


# --- SERVİS KAYDI CONVERSATION HANDLER ---

async def kayit_baslat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Süreç fotoğraf gönderilerek veya /yeni_kayit ile başlatılabilir."""
    context.user_data.clear() # Geçmiş veriyi temizle
    
    # Fotoğraf ile başlatıldıysa fotoğrafı hafızaya al
    if update.message.photo:
        context.user_data['photo'] = update.message.photo[-1] # En yüksek çözünürlük
        await update.message.reply_text("📸 Fotoğraf alındı!\n\nLütfen işlem yapılan **Otobüs Plakasını** girin:")
    else:
        await update.message.reply_text("📝 **Yeni Servis Kaydı**\n\nLütfen otobüs plakasını girin (Örn: 46 H 0123):")
    
    return PLAKA


async def kayit_plaka_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    plaka = format_plaka(update.message.text)
    context.user_data['plaka'] = plaka

    # Araç var mı kontrol et, yoksa otomatik oluştur
    try:
        res = supabase.table("otobusler").select("plaka").eq("plaka", plaka).execute()
        if not res.data:
            supabase.table("otobusler").insert({"plaka": plaka}).execute()
    except Exception as e:
        logger.error(f"Plaka kontrol/ekleme hatası: {e}")

    await update.message.reply_text(f"✅ Plaka: **{plaka}**\n\nYapılan **teknik işlemi / tamiri** detaylıca yazın:")
    return ISLEM


async def kayit_islem_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['islem'] = update.message.text
    await update.message.reply_text(
        "Garanti bitiş tarihi var mı?\n"
        "Varsa `YYYY-AA-GG` formatında yazın (Örn: `2025-12-31`).\n"
        "Yoksa **Pas** yazın veya aşağıdaki butona basın.",
        reply_markup=ReplyKeyboardMarkup([["Pas"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GARANTI


async def kayit_garanti_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    
    if text.lower() == "pas":
        context.user_data['garanti'] = None
    else:
        try:
            # Tarih formatı doğrulama
            valid_date = datetime.strptime(text, "%Y-%m-%d").date()
            context.user_data['garanti'] = str(valid_date)
        except ValueError:
            await update.message.reply_text("⚠️ Geçersiz tarih formatı! Lütfen `YYYY-AA-GG` şeklinde girin veya 'Pas' yazın:")
            return GARANTI

    # Fotoğraf daha önce yüklenmediyse iste
    if 'photo' not in context.user_data:
        await update.message.reply_text(
            "📷 Servis işlemiyle ilgili bir **fotoğraf gönderin** veya fotoğraf yoksa **Pas** yazın:",
            reply_markup=ReplyKeyboardMarkup([["Pas"]], resize_keyboard=True, one_time_keyboard=True)
        )
        return FOTO
    else:
        # Fotoğraf zaten başta verildiyse direkt kaydet
        return await kayit_tamamla(update, context)


async def kayit_foto_al(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        context.user_data['photo'] = update.message.photo[-1]
    
    return await kayit_tamamla(update, context)


async def kayit_tamamla(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("⏳ Kayıt işleniyor, lütfen bekleyin...")
    
    foto_url = None
    photo_item = context.user_data.get('photo')

    # Fotoğraf varsa Supabase Storage'a yükle
    if photo_item:
        try:
            file = await context.bot.get_file(photo_item.file_id)
            file_bytes = await file.download_as_bytearray()
            
            # Benzersiz dosya adı üret
            file_path = f"{context.user_data['plaka']}_{uuid.uuid4().hex[:8]}.jpg"
            
            # Storage'a Yükle
            supabase.storage.from_("servis-fotolari").upload(
                file_path, 
                bytes(file_bytes),
                file_options={"content-type": "image/jpeg"}
            )
            
            # Public URL Al
            foto_url = supabase.storage.from_("servis-fotolari").get_public_url(file_path)
        except Exception as e:
            logger.error(f"Fotoğraf yükleme hatası: {e}")

    # Veritabanına Servis Kaydını Ekle
    try:
        kayit_payload = {
            "plaka": context.user_data['plaka'],
            "yapilan_islem": context.user_data['islem'],
            "garanti_bitis": context.user_data.get('garanti'),
            "foto_url": foto_url
        }
        supabase.table("servis_kayitlari").insert(kayit_payload).execute()
        
        # Ana klavyeyi tekrar yükle
        keyboard = [[KeyboardButton(text="📱 Servis Panelini Aç", web_app=WebAppInfo(url=config.WEB_APP_URL))]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text("🎉 **Servis kaydı başarıyla eklendi!**", parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"DB Kayıt hatası: {e}")
        await update.message.reply_text("❌ Servis kaydı oluşturulurken bir hata oluştu.")

    context.user_data.clear()
    return ConversationHandler.END


async def kayit_iptal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ İşlem iptal edildi.")
    return ConversationHandler.END


# --- MAIN BOOTSTRAP ---

def main():
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Conversation Handler (Adım Adım Kayıt)
    kayit_handler = ConversationHandler(
        entry_points=[
            CommandHandler("yeni_kayit", kayit_baslat),
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
    # Metin aramaları için (Plaka Sorgulama)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plaka_sorgula))

    logger.info("Bot başlatılıyor...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
