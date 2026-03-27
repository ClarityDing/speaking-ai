# app/app.py
import os
from flask import Flask, jsonify
from config import config_by_name
from gemini_routes import gemini_bp


def create_app():
    config_name = os.getenv("FLASK_ENV", "development")
    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    if not app.config["DEBUG"]:
        required_keys = ["SECRET_KEY", "PW_API_KEY", "GEMINI_API_KEY"]
        for key in required_keys:
            if not app.config.get(key):
                raise ValueError(
                    f"Required configuration key '{key}' is not set for production."
                )

    if app.config.get("GEMINI_API_KEY"):
        app.logger.info("GEMINI_API_KEY found in config.")
    else:
        app.logger.warning("GEMINI_API_KEY not found.")

    app.register_blueprint(gemini_bp, url_prefix="/api")
    app.logger.info("Blueprints registered.")

    @app.route("/status", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "ai-feedback-api"})

    app.logger.info(f"Flask app created with '{config_name}' configuration.")
    return app
