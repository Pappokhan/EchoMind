import logging
import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash")

#Request behaviour
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
RETRY_BACKOFF_SECONDS = float(os.getenv("RETRY_BACKOFF_SECONDS", "1.5"))

DATA_DIR = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
LOG_DIR = os.getenv("LOG_DIR", os.path.join(BASE_DIR, "logs"))
DB_PATH = os.path.join(DATA_DIR, "echomind.db")
REQUEST_LOG_PATH = os.path.join(LOG_DIR, "requests.jsonl")

#Flask
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0" if DEBUG else "1") == "1"

MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH_BYTES", str(10 * 1024 * 1024)))

CHAT_UPLOADS_DIR = os.getenv("CHAT_UPLOADS_DIR", os.path.join(DATA_DIR, "chat_uploads"))
MAX_ATTACHMENT_SIZE_BYTES = int(os.getenv("MAX_ATTACHMENT_SIZE_BYTES", str(8 * 1024 * 1024)))
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
ALLOWED_PDF_MIME_TYPES = {"application/pdf"}
ALLOWED_ATTACHMENT_MIME_TYPES = ALLOWED_IMAGE_MIME_TYPES | ALLOWED_PDF_MIME_TYPES

RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))
WORKERS = int(os.getenv("WEB_CONCURRENCY", os.getenv("WORKERS", "2")))
GUNICORN_TIMEOUT = int(os.getenv("GUNICORN_TIMEOUT", "60"))

PROMPT_VERSION = "v1.0"

DEMO_MODE = len(GEMINI_API_KEY) == 0

if not DEBUG and SECRET_KEY == "dev-secret-change-me":
    logging.getLogger("echomind").warning(
        "FLASK_SECRET_KEY is unset — using the insecure default. "
        "Set FLASK_SECRET_KEY in your .env before deploying."
    )
