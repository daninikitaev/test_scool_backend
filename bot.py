import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('BOT_TOKEN')
WEBAPP_URL = os.getenv('WEBAPP_URL')
APP_ENV = os.getenv('APP_ENV', 'production').lower()

if not TOKEN:
    raise ValueError('BOT_TOKEN not set in .env')
if not WEBAPP_URL:
    raise ValueError('WEBAPP_URL not set in .env')
if not WEBAPP_URL.startswith(('https://', 'http://localhost', 'https://localhost')):
    raise ValueError('WEBAPP_URL must be https for production (localhost is allowed for development)')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or 'there'
    keyboard = [[InlineKeyboardButton('Open Mini App', web_app=WebAppInfo(url=WEBAPP_URL))]]
    if not update.message:
        return
    await update.message.reply_text(
        f'Hello {name}! Welcome to the Test Mini App. Click the button below to start taking tests!',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(
        'Test Mini App Help\n\n'
        'Use /start to launch the Mini App\n'
        'Enter a test code to begin\n'
        'Answer questions or skip them\n'
        'View your results and detailed report\n\n'
        'Contact the admin for test codes.'
    )

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text('Unknown command. Use /start or /help.')

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception('Bot update failed: %s', context.error)

def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(20)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(on_error)

    logger.info('Bot is running in %s mode. Web app URL: %s', APP_ENV, WEBAPP_URL)
    app.run_polling()

if __name__ == '__main__':
    main()
