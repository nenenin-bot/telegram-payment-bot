import os
import logging
import psycopg2
import requests
import datetime
import time
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY")
MIDTRANS_API_URL = os.getenv("MIDTRANS_API_URL")
PRICE = int(os.getenv("PRICE"))
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(admin_id) for admin_id in ADMIN_IDS_STR.split(',') if admin_id]

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Gagal terhubung ke database Neon: {e}")
        return None

def setup_database():
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS transactions (
                        order_id TEXT PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        amount INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
            conn.commit()
            logger.info("Database Neon berhasil disiapkan.")
        except Exception as e:
            logger.error(f"Gagal membuat tabel: {e}")
        finally:
            conn.close()

# --- FUNGSI INTERAKSI MIDTRANS YANG DIPERBAIKI ---
def create_midtrans_transaction(user_id: int, amount: int, payment_type: str) -> dict | None:
    order_id = f"TELEGRAM-{user_id}-{int(datetime.datetime.now().timestamp())}"
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": requests.auth._basic_auth_str(MIDTRANS_SERVER_KEY, ""),
    }
    
    payload = {
        "transaction_details": {
            "order_id": order_id,
            "gross_amount": amount
        }
    }

    if payment_type == "qris":
        payload["payment_type"] = "qris"
        payload["custom_expiry"] = {
            "order_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " +0700",
            "expiry_duration": 15,
            "unit": "minute"
        }
    elif payment_type in ["bca_va", "bni_va", "bri_va", "permata_va"]:
        # --- PERBAIKAN LOGIKA VIRTUAL ACCOUNT ---
        payload["payment_type"] = "bank_transfer"
        bank_code = payment_type.split('_')[0] # Mengambil kode bank, contoh: 'bca_va' -> 'bca'
        payload["bank_transfer"] = {
            "bank": bank_code
        }
        # ----------------------------------------
    elif payment_type == "echannel": # Mandiri Bill
        payload["payment_type"] = "echannel"
        payload["echannel"] = {
            "bill_info1": "Payment For:",
            "bill_info2": "Telegram Bot Access"
        }
    else:
        return None

    try:
        response = requests.post(MIDTRANS_API_URL, headers=headers, json=payload)
        response.raise_for_status() # Ini akan error jika status code bukan 2xx
        data = response.json()
        logger.info(f"RESPONS LENGKAP DARI MIDTRANS: {data}")

        conn = get_db_connection()
        if conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO transactions (order_id, user_id, amount, status) VALUES (%s, %s, %s, %s)",
                    (order_id, user_id, amount, 'pending')
                )
            conn.commit()
            conn.close()
            logger.info(f"Transaksi {payment_type} dibuat untuk order_id: {order_id}")
            return data
    except requests.exceptions.RequestException as e:
        logger.error(f"Error saat menghubungi Midtrans: {e}")
        return None
    return None

# --- SEMUA FUNGSI HANDLER TELEGRAM DI BAWAH INI TIDAK ADA PERUBAHAN ---

def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    keyboard = [[InlineKeyboardButton("✅ Beli Akses", callback_data='buy_access')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_html(
        f"👋 Halo <b>{user.first_name}</b>!\n\n"
        f"Selamat datang. Untuk mendapatkan akses ke grup eksklusif, silakan lakukan pembayaran sebesar "
        f"<b>Rp {PRICE:,.0f}</b>.\n\n"
        "Tekan tombol di bawah untuk memulai.",
        reply_markup=reply_markup
    )

def buy_button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    
    keyboard = [
        [InlineKeyboardButton("💳 Virtual Account", callback_data='choose_va')],
        [InlineKeyboardButton("📱 QRIS", callback_data='choose_qris')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text("Silakan pilih metode pembayaran:", reply_markup=reply_markup)

def choose_payment_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    payment_method = query.data

    if payment_method == 'choose_qris':
        query.edit_message_text("⏳ Sedang membuat QR Code, mohon tunggu...")
        transaction_data = create_midtrans_transaction(query.from_user.id, PRICE, "qris")
        
        if transaction_data:
            qr_code_url = next((action['url'] for action in transaction_data.get('actions', []) if action['name'] == 'generate-qr-code'), None)
            order_id = transaction_data.get('order_id')
            if qr_code_url and order_id:
                caption = (
                    f"✅ QRIS berhasil dibuat!\n\n"
                    f"Silakan pindai gambar ini untuk membayar Rp {PRICE:,.0f}.\n\n"
                    f"Link akses akan dikirim otomatis setelah pembayaran berhasil.\n\n"
                    f"<b>Order ID:</b> <code>{order_id}</code>"
                )
                context.bot.send_photo(chat_id=query.from_user.id, photo=qr_code_url, caption=caption, parse_mode='HTML')
                query.delete_message()
            else:
                query.edit_message_text("❌ Maaf, terjadi kesalahan saat memproses data QRIS.")
        else:
            query.edit_message_text("❌ Maaf, terjadi kesalahan. Gagal membuat QR Code.")

    elif payment_method == 'choose_va':
        keyboard = [
            [InlineKeyboardButton("BCA", callback_data='va_bca_va'), InlineKeyboardButton("BNI", callback_data='va_bni_va')],
            [InlineKeyboardButton("BRI", callback_data='va_bri_va'), InlineKeyboardButton("Mandiri", callback_data='va_echannel')],
            [InlineKeyboardButton("Permata / Bank Lain", callback_data='va_permata_va')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text("Silakan pilih bank tujuan Virtual Account:", reply_markup=reply_markup)

def va_bank_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()
    payment_type = query.data.replace('va_', '')
    bank_name = payment_type.split('_')[0].upper()

    query.edit_message_text(f"⏳ Sedang membuat nomor Virtual Account {bank_name}, mohon tunggu...")
    
    transaction_data = create_midtrans_transaction(query.from_user.id, PRICE, payment_type)

    if transaction_data:
        order_id = transaction_data.get('order_id')
        message = f"✅ Virtual Account {bank_name} berhasil dibuat!\n\n" \
                  f"Silakan lakukan pembayaran sebesar **Rp {PRICE:,.0f}** sebelum waktu kedaluwarsa.\n\n"
        
        if payment_type == 'echannel': # Mandiri Bill
            biller_code = transaction_data.get('biller_code')
            bill_key = transaction_data.get('bill_key')
            message += f"**Kode Perusahaan:** `{biller_code}`\n" \
                       f"**Nomor Pembayaran/Bill Key:** `{bill_key}`\n"
        else: # VA Banks
            va_number = transaction_data.get('va_numbers', [{}])[0].get('va_number')
            message += f"**Nomor Virtual Account:** `{va_number}`\n"
        
        message += f"\n**Order ID:** `{order_id}`\n\n" \
                   "Link akses akan dikirim otomatis setelah pembayaran berhasil."
        
        query.edit_message_text(message, parse_mode='Markdown')
    else:
        query.edit_message_text(f"❌ Maaf, terjadi kesalahan. Gagal membuat Virtual Account {bank_name}.")

def get_all_user_ids():
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT DISTINCT user_id FROM transactions")
                user_ids = [row[0] for row in cursor.fetchall()]
                return user_ids
        except Exception as e:
            logger.error(f"Gagal mengambil user_ids dari DB: {e}")
            return []
        finally:
            conn.close()
    return []

def broadcast_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("Anda tidak memiliki izin untuk menggunakan perintah ini.")
        return

    message_to_broadcast = " ".join(context.args)
    if not message_to_broadcast:
        update.message.reply_text("Silakan berikan pesan untuk di-broadcast.\nContoh: /broadcast Halo semua, ada info penting!")
        return

    user_ids = get_all_user_ids()
    if not user_ids:
        update.message.reply_text("Tidak ada pengguna yang ditemukan di database untuk di-broadcast.")
        return

    update.message.reply_text(f"Memulai broadcast ke {len(user_ids)} pengguna... Ini mungkin memakan waktu.")

    success_count = 0
    fail_count = 0

    for uid in user_ids:
        try:
            context.bot.send_message(chat_id=uid, text=message_to_broadcast, parse_mode='HTML')
            success_count += 1
            time.sleep(0.1)
        except Exception as e:
            logger.warning(f"Gagal mengirim broadcast ke user {uid}: {e}")
            fail_count += 1
    
    summary_message = (
        f"📢 **Broadcast Selesai!**\n\n"
        f"✅ Berhasil terkirim: **{success_count}** pengguna\n"
        f"❌ Gagal terkirim: **{fail_count}** pengguna (kemungkinan bot diblokir)"
    )
    update.message.reply_text(summary_message, parse_mode='Markdown')

def main() -> None:
    setup_database()
    
    updater = Updater(TELEGRAM_TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("broadcast", broadcast_command))
    dispatcher.add_handler(CallbackQueryHandler(buy_button_callback, pattern='^buy_access$'))
    dispatcher.add_handler(CallbackQueryHandler(choose_payment_callback, pattern='^choose_.*$'))
    dispatcher.add_handler(CallbackQueryHandler(va_bank_callback, pattern='^va_.*$'))

    updater.start_polling()
    logger.info("Bot Telegram (polling) berhasil dijalankan.")
    
    updater.idle()

if __name__ == '__main__':
    main()