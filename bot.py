import os
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

TOKEN = os.getenv('BOT_TOKEN')
WEBAPP_URL = os.getenv('WEBAPP_URL')

if not TOKEN:
    raise ValueError('BOT_TOKEN not set in .env')
if not WEBAPP_URL:
    raise ValueError('WEBAPP_URL not set in .env')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or 'there'
    keyboard = [[InlineKeyboardButton('Open Mini App', web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        f'Hello {name}! Welcome to the Test Mini App. Click the button below to start taking tests!',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Test Mini App Help\n\n'
        'Use /start to launch the Mini App\n'
        'Enter a test code to begin\n'
        'Answer questions or skip them\n'
        'View your results and detailed report\n\n'
        'Contact the admin for test codes.'
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_cmd))
    print('Bot is running...')
    app.run_polling()

if __name__ == '__main__':
    main()
