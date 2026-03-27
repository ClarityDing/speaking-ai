# app/auth_check.py
import os
import secrets
from functools import wraps
from flask import request, jsonify


def api_key_required(f):
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        valid_api_key = os.getenv("PW_API_KEY")
        provided_key = None

        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            provided_key = auth_header.split(None, 1)[1].strip()

        if not provided_key:
            return (
                jsonify(
                    {
                        "error": "Missing API Key. Provide it in the 'Authorization: Bearer <key>' header."
                    }
                ),
                401,
            )

        if not valid_api_key or not secrets.compare_digest(provided_key, valid_api_key):
            return jsonify({"error": "Invalid API Key."}), 403

        return await f(*args, **kwargs)

    return decorated_function
