# app_with_followup.py
# Telegram bot that processes brain tumour images with YOLOv8,
# explains them via Goog le Gemini API, and handles follow-up questions.

import os
import tempfile
import logging
import shutil
import io
import google.generativeai as genai 
from dotenv import load_dotenv # Import this to load .env locally

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from ultralytics import YOLO
from PIL import Image, ImageDraw, ImageFont

# Load environment variables (for local testing via .env file)
load_dotenv()

# --- Configuration ---
# 1. Telegram Token (Securely loaded from Env Var)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# 2. Gemini API Key (Securely loaded from Env Var)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 3. Model Paths & Settings
# CRITICAL FIX: Use os.path.join for Linux/Docker compatibility
# This assumes your Dockerfile copies the 'models' folder into the working directory
YOLO_MODEL_PATH = os.path.join(os.getcwd(), "models", "best.pt")
YOLO_CONF_THRESHOLD = 0.70

# Configure Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Using the flash model as requested
    gemini_model = genai.GenerativeModel('gemini-2.5-flash')
else:
    gemini_model = None
    print("!!!!!!! GEMINI_API_KEY is missing. AI explanations will not work. !!!!!!!")

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# Reduce noise from httpx
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Validation ---
if not TELEGRAM_BOT_TOKEN:
    logger.critical("!!!!!!! Telegram Bot Token is missing! Set TELEGRAM_BOT_TOKEN env var.")
    exit(1)

if not os.path.exists(YOLO_MODEL_PATH):
    logger.critical(f"!!!!!!! YOLO weights not found at: {YOLO_MODEL_PATH}")
    exit(1)



# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Basic Validation ---
if not TELEGRAM_BOT_TOKEN:
    logger.critical("!!!!!!! Telegram Bot Token is missing! Bot cannot start.")
    exit()

if not os.path.isfile(YOLO_MODEL_PATH):
    logger.critical(f"!!!!!!! YOLO weights not found at: {YOLO_MODEL_PATH}. Bot cannot start.")
    exit()

# Load YOLO Model
try:
    yolo_model = YOLO(YOLO_MODEL_PATH)
    logger.info(f"<<<<>>>> Loaded YOLO model with classes: {yolo_model.names} <<<<>>>>")
except Exception as e:
    logger.exception(f"!!!!!!! Failed to load YOLO model: {e} !!!!!!!")
    raise

# --- Helper Function for Annotation ---
def annotate_image(image_bytes: bytes, boxes_data: list, class_names: dict, font: ImageFont.FreeTypeFont) -> bytes:
    """Annotates an image in memory based on detection data."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)

        if not boxes_data:
            return image_bytes

        for *box, conf, cls in boxes_data:
            x1, y1, x2, y2 = map(int, box)
            class_id = int(cls)
            class_name = class_names.get(class_id, f"Unknown Class {class_id}")
            label = f"{class_name} {conf:.2f}"

            draw.rectangle([x1, y1, x2, y2], outline="red", width=3)
            try:
                text_bbox = font.getbbox(label)
                text_width = text_bbox[2] - text_bbox[0]
                text_height = text_bbox[3] - text_bbox[1]
                
                text_y = y1 - text_height - 2 if y1 > (text_height + 5) else y2 + 2
                text_x = x1
                
                draw.text((text_x, text_y), label, font=font, fill="red")
            except Exception as e:
                logger.error(f"Error drawing text: {e}")

        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        return img_byte_arr.getvalue()
    except Exception as e:
        logger.exception(f"Error during annotation: {e}")
        return image_bytes

# --- Telegram Bot Handlers ---

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        ":>) Hello! Send me a brain tumour image (like an MRI scan). "
        "I'll use YOLOv8 to detect tumours and AI to explain the findings."
    )
    context.chat_data.pop('last_analysis_context', None)


async def handle_image(update: Update, context: CallbackContext) -> None:
    """Handles incoming photos, runs YOLO, annotates, sends to AI, and stores context."""
    if not update.message.photo:
        return

    context.chat_data.pop('last_analysis_context', None)
    
    photo = update.message.photo[-1]
    photo_file_id = photo.file_id
    tmpdir = tempfile.mkdtemp()
    img_path = os.path.join(tmpdir, "input.jpg")

    try:
        # 1. Download Image
        file = await photo.get_file()
        await file.download_to_drive(img_path)
        
        with open(img_path, "rb") as f:
            original_image_bytes = f.read()

        # 2. Run YOLO Inference
        logger.info(f"Running YOLO detection...")
        results = yolo_model.predict(source=img_path, save=False, conf=YOLO_CONF_THRESHOLD)
        result = results[0]
        raw_boxes = result.boxes.data.tolist()

        # 3. Prepare Font
        try:
            font = ImageFont.load_default(size=15)
        except:
            font = ImageFont.load_default()

        # 4. Handle No Detections
        if not raw_boxes:
            await update.message.reply_text(
                f"<<<<>>>> Scan processed. No potential tumours detected above confidence {YOLO_CONF_THRESHOLD}. <<<<>>>>"
            )
            return

        # --- Detections Found ---
        detections_found = True
        detected_classes_summary = []
        for *box, conf, cls in raw_boxes:
            class_id = int(cls)
            class_name = yolo_model.names.get(class_id, f"Unknown Class {class_id}")
            detected_classes_summary.append(f"{class_name} ({conf:.2f})")

        # 5. Annotate Image
        annotated_image_bytes = annotate_image(
            original_image_bytes, raw_boxes, yolo_model.names, font
        )

        # 6. Send Annotated Image
        caption_text = f"( :; ) Detections: {', '.join(detected_classes_summary)}"
        await update.message.reply_photo(
            photo=InputFile(io.BytesIO(annotated_image_bytes), filename="labelled_scan.jpg"),
            caption=caption_text
        )

        # 7. Call Gemini for Explanation
        explanation = None
        if detections_found and gemini_model:
            status_message = await update.message.reply_text("??? Analyzing with AI...")
            
            prompt_for_gemini = f"""You are an analytical assistant reviewing a medical brain scan.
            
            The image has been processed by a detection model which found: {', '.join(detected_classes_summary) or 'None'}.
            Red boxes mark these detections.

            1. **Verify the image type:** Is this a brain MRI/CT scan?
            2. **Analyze the findings:** Based on the visual evidence in the red boxes and the labels provided, give a brief professional summary.
            
            If the image is NOT a medical scan or the detections are clearly wrong (e.g., detecting a tumor on a cat), state that clearly.

            Disclaimer: "IMPORTANT: This is an AI analysis for informational purposes only and not a medical diagnosis."
            """

            try:
                # Load annotated image as PIL for Gemini
                pil_image = Image.open(io.BytesIO(annotated_image_bytes))
                
                # Generate content (Multimodal: Text + Image)
                response = gemini_model.generate_content([prompt_for_gemini, pil_image])
                explanation = response.text
                
                logger.info("AI analysis successful.")
                await status_message.edit_text(explanation + "\n\n*You can ask one follow-up question.*")

            except Exception as e:
                logger.exception(f"!!!!!!! Error contacting Gemini API: {e}")
                explanation = f"Sorry, AI explanation failed. Error: {e}"
                await status_message.edit_text(explanation)

            # 8. Store Context
            if explanation:
                context.chat_data['last_analysis_context'] = {
                    'original_photo_file_id': photo_file_id,
                    'raw_boxes': raw_boxes,
                    'class_names': yolo_model.names,
                    'initial_explanation': explanation,
                    'detected_classes_summary': detected_classes_summary
                }

        elif detections_found and not gemini_model:
            await update.message.reply_text("Detections found, but Gemini API Key is not set.")

    except Exception as e:
        logger.exception(f"Error in handle_image: {e}")
        await update.message.reply_text("An error occurred processing the image.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def handle_generic_message(update: Update, context: CallbackContext) -> None:
    """Handles text messages as follow-ups using AI."""
    user_message = update.message.text
    chat_id = update.effective_chat.id
    last_context = context.chat_data.get('last_analysis_context')

    if last_context and gemini_model:
        # Retrieve context
        original_photo_file_id = last_context['original_photo_file_id']
        raw_boxes = last_context['raw_boxes']
        class_names = last_context['class_names']
        initial_explanation = last_context['initial_explanation']
        
        # Clear context (allow only one follow-up)
        context.chat_data.pop('last_analysis_context')

        status_message = await update.message.reply_text("??? Processing follow-up with AI...")
        
        tmpdir_followup = tempfile.mkdtemp()
        followup_img_path = os.path.join(tmpdir_followup, "followup.jpg")

        try:
            # Re-download and Re-annotate
            file = await context.bot.get_file(original_photo_file_id)
            await file.download_to_drive(followup_img_path)
            
            with open(followup_img_path, "rb") as f:
                original_bytes = f.read()
                
            # Use basic font logic again
            try:
                font = ImageFont.load_default(size=15)
            except:
                font = ImageFont.load_default()

            annotated_bytes = annotate_image(original_bytes, raw_boxes, class_names, font)
            pil_image = Image.open(io.BytesIO(annotated_bytes))

            # Construct Follow-up Prompt
            followup_prompt = f"""
            Context: You previously analyzed this medical image.
            Your previous analysis: "{initial_explanation}"
            
            User's new Follow-up Question: "{user_message}"
            
            Please answer the user's question based on the image and previous context. Keep it professional and concise.
            """

            # Call Gemini
            response = gemini_model.generate_content([followup_prompt, pil_image])
            await status_message.edit_text(response.text)

        except Exception as e:
            logger.exception(f"Error in follow-up: {e}")
            await status_message.edit_text(f"Error processing follow-up: {e}")
        finally:
            shutil.rmtree(tmpdir_followup, ignore_errors=True)
    else:
        await update.message.reply_text("Please send a brain MRI image first.")
async def help_command(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "🩺 *Medical AI Assistant Help*\n\n"
        "1. Send me a clear picture of a brain MRI/CT scan.\n"
        "2. Wait a moment while our YOLOv8 model detects potential anomalies.\n"
        "3. Read the Gemini AI analysis.\n"
        "4. Reply with any follow-up questions you have about the results!\n\n"
        "Type /clear to reset the conversation at any time.",
        parse_mode='Markdown'
    )

async def clear_command(update: Update, context: CallbackContext) -> None:
    context.chat_data.pop('last_analysis_context', None)
    await update.message.reply_text("🧹 Conversation context cleared! Ready for a new scan.")
# --- Main Function ---
def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("TOKEN MISSING")
        return
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_generic_message))
    
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))

    logger.info("Bot started...")
    application.run_polling()

if __name__ == "__main__":
    main()