import os
import logging
import hashlib
import psycopg2
from flask import Flask, request, abort
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
import datetime

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY")
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Gagal terhubung ke database Neon: {e}")
        return None

@app.route('/webhook/midtrans', methods=['POST'])
def midtrans_webhook():
    data = request.json
    order_id = data.get('order_id')
    transaction_status = data.get('transaction_status')
    
    logger.info(f"Menerima webhook untuk order_id: {order_id} dengan status: {transaction_status}")

    signature = hashlib.sha512(f"{order_id}{data.get('status_code')}{data.get('gross_amount')}{MIDTRANS_SERVER_KEY}".encode()).hexdigest()
    if signature != data.get('signature_key'):
        logger.warning(f"Signature key tidak valid untuk order_id: {order_id}")
        abort(403)

    if transaction_status in ['settlement', 'capture']:
        conn = get_db_connection()
        if not conn: abort(500, "Database connection failed")
        
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT user_id, status FROM transactions WHERE order_id = %s", (order_id,))
                result = cursor.fetchone()
                
                if result and result[1] == 'pending':
                    user_id = result[0]
                    expire_date = datetime.datetime.now() + datetime.timedelta(days=1)
                    invite_link = bot.create_chat_invite_link(
                        chat_id=TARGET_CHAT_ID, expire_date=expire_date, member_limit=1
                    )
                    
                    bot.send_message(
                        chat_id=user_id,
                        text=f"✅ Pembayaran berhasil!\n\n"
                             f"Terima kasih. Silakan gunakan tombol di bawah untuk bergabung ke grup.\n\n"
                             f"<i>Link ini hanya berlaku untuk 1 kali klik dan akan kedaluwarsa dalam 24 jam.</i>",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Gabung Grup", url=invite_link.invite_link)]]),
                        parse_mode='HTML'
                    )
                    
                    cursor.execute("UPDATE transactions SET status = 'paid' WHERE order_id = %s", (order_id,))
                    conn.commit()
                    logger.info(f"Link grup terkirim ke user {user_id} untuk order_id {order_id}")
                else:
                    logger.warning(f"Transaksi {order_id} sudah diproses atau tidak ditemukan.")
        except Exception as e:
            logger.error(f"Error saat proses webhook di database: {e}")
        finally:
            conn.close()

    return "OK", 200

application = app