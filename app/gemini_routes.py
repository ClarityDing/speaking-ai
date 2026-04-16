# app/gemini_routes.py

import json
import asyncio
from http import HTTPStatus
import os
import random
from flask import Blueprint, jsonify, request, current_app
from google.genai import types
from google import genai
from speechsuper import run_speech_super_assessment
from azure_speech import run_azure_assessment
from auth_check import api_key_required
from utils import (
    load_json_file,
    calculate_overall_band,
    safe_parse_response,
    format_rubric_for_prompt,
    ielts_to_cefr,
)

from gemini_prompts import (
    PROMPT_FC,
    PROMPT_PN,
    PROMPT_TR_LO,
    PROMPT_LR,
    PROMPT_GRA,
)
from schemas import GradingResult

gemini_bp = Blueprint("gemini_main", __name__)


async def _call_gemini_api_async(criterion, prompt_template, semaphore, **kwargs):
    max_retries = 5
    base_sleep_time = 2

    api_key = current_app.config.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)

    current_app.logger.info(f"--- Starting async call for: {criterion} ---")

    async with semaphore:
        for attempt in range(max_retries):
            try:
                full_prompt = prompt_template.format(**kwargs)

                config = types.GenerateContentConfig(
                    temperature=0.0,
                    thinking_config=types.ThinkingConfig(thinking_budget=1024),
                    response_mime_type="application/json",
                    response_schema=GradingResult,
                )

                response = await client.aio.models.generate_content(
                    model="models/gemini-2.5-flash",
                    contents=full_prompt,
                    config=config,
                )

                parsed_result = safe_parse_response(response)

                if not parsed_result or "error" in parsed_result:
                    error_detail = (
                        parsed_result.get("message", "Unknown parsing error")
                        if parsed_result
                        else "Response.parsed was empty"
                    )
                    raise ValueError(
                        f"Failed to get a valid parsed response. Details: {error_detail}"
                    )

                current_app.logger.info(
                    f"--- Successfully parsed response for: {criterion} ---"
                )
                return (
                    criterion,
                    parsed_result.get("score"),
                    parsed_result.get("feedback"),
                )

            except Exception as e:
                current_app.logger.warning(
                    f"--- {criterion} attempt {attempt+1}/{max_retries} failed: {e} ---"
                )
                if attempt < max_retries - 1:
                    jitter = random.uniform(0, 1.5)
                    wait_time = (base_sleep_time * (2**attempt)) + jitter

                    current_app.logger.info(
                        f"Waiting {wait_time:.2f}s before retrying {criterion}..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    return (
                        criterion,
                        None,
                        {
                            "error": f"AI call for {criterion} failed after {max_retries} retries: {str(e)}"
                        },
                    )

    return criterion, None, {"error": "Max retries exceeded"}


async def _run_grading_process(
    exercise_id, essay_title, student_response, generated_report, audio_duration
):
    semaphore = asyncio.Semaphore(2)
    all_exercises = load_json_file("criteria.json")
    if not all_exercises:
        current_app.logger.error("Failed to load criteria.json")
        return {"error": "Failed to load exercise criteria."}

    target_exercise = (
        next(
            (
                ex
                for ex in all_exercises
                if str(ex.get("exerciseID")) == str(exercise_id)
            ),
            None,
        )
        if all_exercises
        else None
    )

    if not target_exercise:
        return {"error": f"Exercise ID '{exercise_id}' not found in criteria.json."}

    # Load learning objectives based on task type
    task_type = target_exercise.get("taskType", "Speaking")

    lo_data = load_json_file("learning_objective_ielts.json")

    if lo_data is None:
        current_app.logger.error(
            "Failed to load learning_objective_ielts.json. Check if file exists or is valid JSON."
        )
        lo_data = {}

    general_lo_list = lo_data.get(task_type, {}).get("learningObjective", [])

    exercise_lo_list = target_exercise.get("criteria", [])

    report_parts = []

    if general_lo_list:
        report_parts.extend([f"- {obj}" for obj in general_lo_list])

    if exercise_lo_list:
        if report_parts:
            report_parts.append("")
        report_parts.extend([f"- {obj}" for obj in exercise_lo_list])

    learning_objectives_str = "\n".join(report_parts)

    # Add graph description to the prompt
    task_prompt = essay_title
    if target_exercise.get("cueCardContent"):
        task_prompt += (
            f"\n\n[IELTS CUE CARD]:\n"
            f"The student was given the following cue card and is expected to address all the points listed:\n"
            f"{target_exercise['cueCardContent']}"
        )

    if "promptTr" not in target_exercise:
        return {
            "error": "Exercise configuration error: 'promptTr' key is missing or invalid in criteria.json."
        }

    if target_exercise["promptTr"] is True:
        criteria_order = ["FC", "PN", "TR", "LR", "GRA"]
        prompt_map = {
            "FC": PROMPT_FC,
            "PN": PROMPT_PN,
            "TR": PROMPT_TR_LO,
            "LR": PROMPT_LR,
            "GRA": PROMPT_GRA,
        }
    else:
        criteria_order = ["FC", "PN", "LR", "GRA"]
        prompt_map = {
            "FC": PROMPT_FC,
            "PN": PROMPT_PN,
            "LR": PROMPT_LR,
            "GRA": PROMPT_GRA,
        }

    # Load rubric data for all criteria
    rubrics_data = load_json_file("rubric.json")
    if not rubrics_data:
        return {"error": "rubric.json not found or is invalid."}

    # Load word count target from criteria.json
    audio_limited = target_exercise.get("AudioLimited")

    tasks = []
    for criterion in criteria_order:
        base_kwargs = {
            "TASK_PROMPT": task_prompt,
            "STUDENT_RESPONSE": student_response,
            "SPEECH_SUPER_REPORT": generated_report,
            "RUBRIC_CRITERIA": format_rubric_for_prompt(
                rubrics_data.get(criterion, {})
            ),
            "AUDIO_LIMITED": str(audio_limited) if audio_limited else "not specified",
            "AUDIO_DURATION": (
                str(audio_duration) if audio_duration else "not specified"
            ),
        }
        if criterion == "TR":
            base_kwargs["LEARNING_OBJECTIVES"] = learning_objectives_str

        task = asyncio.create_task(
            _call_gemini_api_async(
                criterion, prompt_map[criterion], semaphore, **base_kwargs
            )
        )
        tasks.append(task)

    current_app.logger.info("Starting concurrent Gemini API calls")
    results = await asyncio.gather(*tasks)
    current_app.logger.info("All Gemini API calls completed")

    failed_criteria = []
    first_error_message = ""

    for crit, score, feedback in results:
        if score is None:
            failed_criteria.append(crit)
            if isinstance(feedback, dict) and "error" in feedback:
                first_error_message = feedback["error"]
            else:
                first_error_message = "Unknown error"

    if failed_criteria:
        error_msg = f"Grading failed for: {', '.join(failed_criteria)}. Details: {first_error_message}"
        current_app.logger.error(error_msg)
        return {"error": error_msg}

    final_output = {
        "IELTS_score": None,
        "CEFR_level": None,
        "band_scores": {},
        "detailed_feedback": {},
    }

    scores = []

    for crit, score, feedback in results:
        final_output["band_scores"][crit] = score
        final_output["detailed_feedback"][crit] = feedback
        scores.append(score)

    overall_score = calculate_overall_band(scores)
    final_output["IELTS_score"] = str(overall_score)
    final_output["CEFR_level"] = ielts_to_cefr(overall_score)

    return final_output


@gemini_bp.route("/speaking-ielts-api", methods=["POST"])
@api_key_required
async def evaluate_gemini():
    data = request.get_json()
    exercise_id = data.get("exerciseID")
    essay_title = data.get("essayTitle")

    audio_path = "./audio/" + "Q5_fluency_8.5_alu0101576312@ull.edu.es" + ".wav"
    # speech_result = run_azure_assessment(audio_path)
    speech_result = run_speech_super_assessment(audio_path)

    if not speech_result or "error" in speech_result:
        err = (
            speech_result.get("error", "Unknown error")
            if speech_result
            else "Unknown error"
        )
        return (
            jsonify({"error": f"Speech assessment failed: {err}"}),
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    # Save the generated report to a JSON file for debugging

    student_response = speech_result.get("metadata", {}).get("full_transcript", "")
    audio_duration = speech_result.get("metadata", {}).get("duration", 0)
    generated_report = json.dumps(
        speech_result, separators=(",", ":"), ensure_ascii=False
    )

    current_app.logger.info(f"Speech recognized: {student_response}")

    if not all(
        [
            exercise_id,
            essay_title,
            student_response,
            generated_report,
            audio_duration is not None,
        ]
    ):
        return (
            jsonify(
                {
                    "error": "Missing 'exerciseID', 'essayTitle', 'studentResponse', 'generatedReport', or 'audioDuration' in the request."
                }
            ),
            HTTPStatus.BAD_REQUEST,
        )

    current_app.logger.info(
        f"Received request for exerciseID: {exercise_id}. Essay length: {len(student_response)}"
    )

    try:
        result = await _run_grading_process(
            exercise_id, essay_title, student_response, generated_report, audio_duration
        )

        if "error" in result:
            current_app.logger.error(
                f"Grading process failed for {exercise_id}: {result['error']}"
            )
            return jsonify(result), HTTPStatus.INTERNAL_SERVER_ERROR

        return jsonify(result), HTTPStatus.OK

    except Exception as e:
        current_app.logger.error(
            f"Top-level error in endpoint for {exercise_id}: {e}", exc_info=True
        )
        return (
            jsonify({"error": "An internal server error occurred."}),
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
