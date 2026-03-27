# app/config.py
import os


class Config:
    DEBUG = False
    TESTING = False
    # --- PW API KEY ---
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
    PW_API_KEY = os.getenv("PW_API_KEY")

    # --- Gemini API ---
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")
    GEMINI_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", 0.1))
    GEMINI_TOP_P = float(os.getenv("GEMINI_TOP_P", 0.6))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
