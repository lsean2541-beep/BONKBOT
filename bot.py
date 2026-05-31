import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from PIL import Image
import io
import tempfile
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import asyncio

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Store user photos temporarily (in production, use Redis or database)
user_photos = {}

# Bot token from environment variable
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message when /start is issued."""
    keyboard = [
        [InlineKeyboardButton("Generate PDF", callback_data='generate_pdf')],
        [InlineKeyboardButton("Clear Images", callback_data='clear_images')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = """
📸 *Welcome to PDF Creator Bot*

Convert your images into professional PDFs instantly.

*How to use:*
1️⃣ Send me one or more photos
2️⃣ Click *Generate PDF* when finished

*Supported formats:* JPG, PNG, WEBP
*Max files:* 20 photos per PDF
    """
    
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store photos when user sends them."""
    user_id = update.effective_user.id
    
    # Initialize user's photo list if not exists
    if user_id not in user_photos:
        user_photos[user_id] = []
    
    # Check limit
    if len(user_photos[user_id]) >= 20:
        await update.message.reply_text("⚠️ Maximum 20 photos reached. Please generate PDF or clear images.")
        return
    
    # Get the photo (highest quality)
    photo = update.message.photo[-1]
    file = await photo.get_file()
    
    # Download photo to memory
    image_bytes = await file.download_as_bytearray()
    
    # Store as bytes with timestamp
    user_photos[user_id].append({
        'bytes': image_bytes,
        'timestamp': datetime.now().strftime("%Y%m%d_%H%M%S")
    })
    
    count = len(user_photos[user_id])
    await update.message.reply_text(f"✅ Photo #{count} received!\nSend more or click 'Generate PDF'")

async def clear_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all stored images for the user."""
    user_id = update.effective_user.id
    
    if user_id in user_photos:
        user_photos[user_id] = []
        await update.callback_query.answer("Images cleared!")
        await update.callback_query.edit_message_text(
            "🗑️ All images have been cleared.\nSend new photos to create a PDF."
        )
    else:
        await update.callback_query.answer("No images to clear", show_alert=True)

async def generate_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate PDF from stored photos."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    # Check if user has photos
    if user_id not in user_photos or not user_photos[user_id]:
        await query.edit_message_text(
            "❌ No photos found!\n\nPlease send me photos first before generating PDF."
        )
        return
    
    # Send processing message
    processing_msg = await query.edit_message_text("📄 Processing images... Please wait.")
    
    try:
        # Create PDF
        pdf_bytes = await create_pdf_from_images(user_photos[user_id])
        
        # Send PDF back to user
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=io.BytesIO(pdf_bytes),
            filename=f"document_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            caption=f"✅ PDF created successfully!\n📸 {len(user_photos[user_id])} pages"
        )
        
        # Clear stored images after successful PDF generation
        user_photos[user_id] = []
        
        # Delete processing message
        await processing_msg.delete()
        
        # Show new buttons
        keyboard = [
            [InlineKeyboardButton("Generate PDF", callback_data='generate_pdf')],
            [InlineKeyboardButton("Clear Images", callback_data='clear_images')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✨ PDF sent! Ready for more conversions.\n\nSend photos to create another PDF.",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
        await query.edit_message_text(
            "❌ Error generating PDF. Please try again or send fewer photos."
        )

async def create_pdf_from_images(images):
    """Convert images to PDF bytes."""
    pdf_buffer = io.BytesIO()
    
    for i, img_data in enumerate(images):
        # Convert bytes to PIL Image
        image = Image.open(io.BytesIO(img_data['bytes']))
        
        # Convert RGBA to RGB if needed
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        
        # Create temporary file for this page
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_img:
            # Resize if too large (max 2000px)
            if max(image.size) > 2000:
                ratio = 2000 / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            
            image.save(temp_img.name, 'JPEG', quality=85, optimize=True)
            
            # Add to PDF
            if i == 0:
                c = canvas.Canvas(pdf_buffer, pagesize=letter)
            else:
                c.showPage()
            
            # Calculate image position to fit page
            page_width, page_height = letter
            img_width, img_height = image.size
            
            # Scale image to fit page
            ratio = min(page_width / img_width, page_height / img_height)
            new_width = img_width * ratio
            new_height = img_height * ratio
            
            # Center image on page
            x = (page_width - new_width) / 2
            y = (page_height - new_height) / 2
            
            c.drawImage(temp_img.name, x, y, width=new_width, height=new_height)
    
    c.save()
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle other documents (not photos)."""
    await update.message.reply_text(
        "📂 Please send photos (JPG, PNG, or WEBP) to create a PDF.\n\n"
        "I don't process documents or other file types."
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """Start the bot."""
    # Create application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(CallbackQueryHandler(generate_pdf, pattern='generate_pdf'))
    application.add_handler(CallbackQueryHandler(clear_images, pattern='clear_images'))
    application.add_error_handler(error_handler)
    
    # Start bot
    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
