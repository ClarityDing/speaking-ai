# app/utils.py
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional
from functools import lru_cache


BASE_DIR = Path(__file__).resolve().parent


def format_rubric_for_prompt(rubric_dict: dict) -> str:
    if not isinstance(rubric_dict, dict):
        return ""

    try:
        sorted_bands = sorted(rubric_dict.keys(), key=int, reverse=True)
        return "\n".join(
            [f"- Band {band}: {rubric_dict[band]}" for band in sorted_bands]
        )
    except (ValueError, TypeError):
        return "\n".join(
            [f"- {key}: {value}" for key, value in sorted(rubric_dict.items())]
        )


@lru_cache(maxsize=10)
def load_json_file(filename: str) -> Optional[Any]:
    path = BASE_DIR / filename
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    except Exception:
        return None


def ielts_to_cefr(score: float) -> str:
    try:
        score_float = float(score)
    except (ValueError, TypeError):
        return "N/A"

    # U: <1.75
    if score_float < 1.75:
        return "U"
    # A1: >=1.75 & <2.75
    elif score_float < 2.75:
        return "A1"
    # A2: >=2.75 & <3.75
    elif score_float < 3.75:
        return "A2"
    # B1: >=3.75 & <5.25
    elif score_float < 5.25:
        return "B1"
    # B2: >=5.25 & <6.50
    elif score_float < 6.50:
        return "B2"
    # C1: >=6.50 & <7.75
    elif score_float < 7.75:
        return "C1"
    # C2: >=7.75
    else:
        return "C2"


def calculate_overall_band(scores: list[float]) -> Optional[float]:
    valid_scores = [s for s in scores if s is not None]
    if not valid_scores:
        return None
    average = sum(valid_scores) / len(valid_scores)
    return int(average * 2 + 0.5) / 2


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)


def _extract_json_block(text: str) -> Optional[str]:
    match = _JSON_BLOCK_RE.search(text)
    return match.group(0) if match else None


def _parse_ai_json_response(ai_raw_text: str) -> Dict[str, Any]:
    if not isinstance(ai_raw_text, str) or not ai_raw_text.strip():
        return {"error": "AIResponseFormatError", "message": "AI response was empty."}

    cleaned = (
        ai_raw_text.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )

    json_block = _extract_json_block(cleaned)
    if not json_block:
        return {
            "error": "AIResponseFormatError",
            "message": "AI response did not contain a JSON structure.",
        }

    try:
        return json.loads(json_block)
    except json.JSONDecodeError as e:
        return {
            "error": "AIResponseFormatError",
            "message": f"Invalid JSON from AI: {e}",
        }


def safe_parse_response(response: Any) -> Dict[str, Any]:
    if hasattr(response, "parsed") and response.parsed:
        try:
            if hasattr(response.parsed, "model_dump"):
                return response.parsed.model_dump()
            else:
                return response.parsed.dict()
        except Exception as e:
            return {
                "error": "PydanticConversionError",
                "message": f"Failed to convert Pydantic model: {e}",
            }

    text_to_parse = None
    if hasattr(response, "text") and response.text:
        text_to_parse = response.text
    elif hasattr(response, "candidates") and response.candidates:
        try:
            text_to_parse = "".join(
                part.text for part in response.candidates[0].content.parts
            )
        except Exception:
            pass

    if text_to_parse:
        return _parse_ai_json_response(text_to_parse)
    else:
        return {
            "error": "NoTextInResponseError",
            "message": "Could not extract any text from the AI response object.",
        }
