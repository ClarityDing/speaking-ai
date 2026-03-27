import azure.cognitiveservices.speech as speechsdk
import json
import os
import re
import sys
import threading

from dotenv import load_dotenv

load_dotenv()


# ── Phoneme Mappings ──────────────────────────────────────────────────────────

PHONE_TO_IPA = {
    "aa": "ɑ",
    "ae": "æ",
    "ah": "ʌ",
    "ao": "ɔ",
    "aw": "aʊ",
    "ay": "aɪ",
    "b": "b",
    "ch": "tʃ",
    "d": "d",
    "dh": "ð",
    "eh": "ɛ",
    "er": "ɝ",
    "ey": "eɪ",
    "f": "f",
    "g": "ɡ",
    "hh": "h",
    "ih": "ɪ",
    "iy": "i",
    "jh": "dʒ",
    "k": "k",
    "l": "l",
    "m": "m",
    "n": "n",
    "ng": "ŋ",
    "ow": "oʊ",
    "oy": "ɔɪ",
    "p": "p",
    "r": "r",
    "s": "s",
    "sh": "ʃ",
    "t": "t",
    "th": "θ",
    "uh": "ʊ",
    "uw": "u",
    "v": "v",
    "w": "w",
    "y": "j",
    "z": "z",
    "zh": "ʒ",
}

VOWEL_PHONES = {
    "aa",
    "ae",
    "ah",
    "ao",
    "aw",
    "ay",
    "eh",
    "er",
    "ey",
    "ih",
    "iy",
    "ow",
    "oy",
    "uh",
    "uw",
}

DIGRAPHS = [
    "tch",
    "dge",
    "ear",
    "sh",
    "ch",
    "th",
    "ph",
    "wh",
    "ng",
    "gh",
    "ck",
    "qu",
    "ea",
    "ee",
    "oo",
    "ou",
    "ow",
    "oi",
    "oy",
    "au",
    "aw",
    "ai",
    "ay",
    "ei",
    "ey",
    "ie",
    "ue",
    "ui",
    "eu",
    "ir",
    "er",
    "ur",
    "or",
    "ar",
    "ll",
    "ss",
    "ff",
]


# ── Helper Functions ──────────────────────────────────────────────────────────


def score_to_quality(score: float, error_type: str = "None") -> str:
    if error_type in ("Omission", "Insertion"):
        return "Incorrect/Missing"
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Clear"
    if score >= 40:
        return "Noticeable Accent"
    if score >= 20:
        return "Weak/Distorted"
    return "Incorrect/Missing"


def split_graphemes(word: str) -> list:
    w = word.lower().rstrip(".,!?;:'\"-")
    groups = []
    i = 0
    while i < len(w):
        matched = False
        for dg in DIGRAPHS:
            if w[i : i + len(dg)] == dg:
                groups.append(dg)
                i += len(dg)
                matched = True
                break
        if not matched:
            groups.append(w[i])
            i += 1
    return groups


def align_graphemes_to_phonemes(word: str, ipa_list: list) -> list:
    graphemes = split_graphemes(word)
    n_g, n_p = len(graphemes), len(ipa_list)

    if n_p == 0:
        return [(g, "?") for g in graphemes]
    if n_g == n_p:
        return list(zip(graphemes, ipa_list))

    if n_g > n_p:
        result = []
        ratio = n_g / n_p
        for pi in range(n_p):
            start = round(pi * ratio)
            end = round((pi + 1) * ratio)
            g = "".join(graphemes[start:end]) or graphemes[min(pi, n_g - 1)]
            result.append((g, ipa_list[pi]))
        return result

    result = [(graphemes[gi], ipa_list[gi]) for gi in range(n_g - 1)]
    result.append(("".join(graphemes[n_g - 1 :]), ipa_list[n_g - 1]))
    return result


_cmudict_cache = None


def _get_cmudict() -> dict:
    global _cmudict_cache
    if _cmudict_cache is None:
        try:
            import nltk

            try:
                _cmudict_cache = nltk.corpus.cmudict.dict()
            except LookupError:
                nltk.download("cmudict", quiet=True)
                _cmudict_cache = nltk.corpus.cmudict.dict()
        except Exception as e:
            print(
                f"Warning: could not load cmudict ({e}). Stress detection disabled.",
                file=sys.stderr,
            )
            _cmudict_cache = {}
    return _cmudict_cache


def get_primary_stress_phoneme(word: str) -> list:
    cmudict = _get_cmudict()
    clean = word.lower().rstrip(".,!?;:'\"-")
    if clean not in cmudict:
        return []
    phones = cmudict[clean][0]
    for ph in phones:
        if ph.endswith("1"):
            base = re.sub(r"\d", "", ph).lower()
            ipa = PHONE_TO_IPA.get(base, base)
            return [f"/{ipa}/"]
    return []


def check_linking(word_a: dict, word_b: dict) -> tuple:
    if word_b is None:
        return False, False

    phones_a = word_a.get("Phonemes", [])
    phones_b = word_b.get("Phonemes", [])
    if not phones_a or not phones_b:
        return False, False

    first_phone_b = phones_b[0]["Phoneme"].lower()
    linkable = first_phone_b in VOWEL_PHONES

    gap_100ns = max(0, word_b["Offset"] - (word_a["Offset"] + word_a["Duration"]))
    gap_ms = gap_100ns // 10_000
    linked = linkable and gap_ms < 50

    return linkable, linked


# ── Azure Conversion ──────────────────────────────────────────────────────────


def convert_azure_result(azure_data: list) -> dict | None:
    """Convert Azure Speech Assessment raw JSON list to IELTS pronunciation analysis format."""
    all_words = []
    transcript_parts = []

    sentence_boundaries = set()
    for sentence in azure_data:
        if sentence.get("RecognitionStatus") != "Success":
            continue
        best = (sentence.get("NBest") or [{}])[0]
        transcript_parts.append(best.get("Lexical", "").strip())
        if all_words:
            sentence_boundaries.add(len(all_words))
        all_words.extend(best.get("Words", []))

    if not all_words:
        print("ERROR: No recognised words found in Azure data.", file=sys.stderr)
        return None

    first_start = all_words[0]["Offset"]
    last_end = all_words[-1]["Offset"] + all_words[-1]["Duration"]
    duration_s = (last_end - first_start) / 10_000_000
    word_count = len(all_words)
    speed_wpm = round(word_count / (duration_s / 60)) if duration_s > 0 else 0
    full_transcript = " ".join(transcript_parts)

    word_analysis = []
    unfilled_pause_count = 0

    for i, wd in enumerate(all_words):
        word = wd.get("Word", "")
        pron = wd.get("PronunciationAssessment", {})
        word_acc = pron.get("AccuracyScore", 0)
        error_type = pron.get("ErrorType", "None")

        phonemes_raw = wd.get("Phonemes", [])
        ipa_list = []
        acc_list = []
        for ph in phonemes_raw:
            azure_ph = ph.get("Phoneme", "").lower()
            ipa_list.append(PHONE_TO_IPA.get(azure_ph, azure_ph))
            acc_list.append(
                ph.get("PronunciationAssessment", {}).get("AccuracyScore", word_acc)
            )

        gp_pairs = align_graphemes_to_phonemes(word, ipa_list)
        phonetic_clarity = []
        for j, (g, p) in enumerate(gp_pairs):
            ph_acc = acc_list[j] if j < len(acc_list) else word_acc
            quality = score_to_quality(ph_acc, error_type)
            phonetic_clarity.append(f"'{g}' as /{p}/: {quality}")

        stressed_at = get_primary_stress_phoneme(word)

        if i + 1 < len(all_words) and (i + 1) not in sentence_boundaries:
            next_wd = all_words[i + 1]
            gap_100ns = max(0, next_wd["Offset"] - (wd["Offset"] + wd["Duration"]))
            pause_ms = gap_100ns // 10_000
        else:
            pause_ms = 0

        if pause_ms >= 250:
            unfilled_pause_count += 1

        next_wd_data = all_words[i + 1] if i + 1 < len(all_words) else None
        linkable, linked = check_linking(wd, next_wd_data)

        word_analysis.append(
            {
                "index": i,
                "word": word,
                "phonetic_clarity": phonetic_clarity,
                "stress": {
                    "expected_at": stressed_at,
                    "detected_at": None,
                },
                "linking_details": {
                    "is_linkable_opportunity": linkable,
                    "was_actually_linked": linked,
                },
                "pause_duration_after_ms": pause_ms,
            }
        )

    unfilled_per_100 = (
        round(unfilled_pause_count / word_count * 100, 1) if word_count else 0
    )

    return {
        "metadata": {
            "speed_wpm": speed_wpm,
            "full_transcript": full_transcript,
            "duration": round(duration_s, 3),
            "unfilled_pause_count": unfilled_pause_count,
            "unfilled_pauses_per_100_words": unfilled_per_100,
        },
        "word_level_analysis": word_analysis,
    }


# ── Azure Speech Assessment ───────────────────────────────────────────────────


def _create_speech_config() -> speechsdk.SpeechConfig:
    key = os.getenv("AZURE_SPEECH_KEY")
    region = os.getenv("AZURE_SPEECH_REGION", "eastus")
    if not key:
        raise RuntimeError("AZURE_SPEECH_KEY not set.")
    return speechsdk.SpeechConfig(subscription=key, region=region)


def continuous_assessment_from_wav(
    filename: str, language: str
) -> tuple[str, dict, str | None]:

    try:
        speech_config = _create_speech_config()
    except Exception as e:
        return "", {}, f"Config Error: {e}"

    if not os.path.isfile(filename):
        return "", {}, f"File not found: {filename}"

    speech_config.speech_recognition_language = language
    audio_config = speechsdk.audio.AudioConfig(filename=filename)

    pron_cfg = speechsdk.PronunciationAssessmentConfig(
        reference_text="",  # Unscripted mode
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
    )

    # 啟用 Prosody (語調/韻律)
    pron_cfg.enable_prosody_assessment()

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )
    pron_cfg.apply_to(recognizer)

    all_texts = []
    all_json_results = []
    done = threading.Event()
    error_msg = None

    def recognized_cb(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            if evt.result.text:
                print(f"[Recognized]: {evt.result.text}")
                all_texts.append(evt.result.text)

            raw_json = evt.result.properties.get(
                speechsdk.PropertyId.SpeechServiceResponse_JsonResult
            )
            if raw_json:
                try:
                    data = json.loads(raw_json)
                    all_json_results.append(data)
                except json.JSONDecodeError:
                    pass

    def canceled_cb(evt):
        nonlocal error_msg
        if evt.cancellation_details.reason == speechsdk.CancellationReason.Error:
            error_msg = f"Error: {evt.cancellation_details.error_details}"
        done.set()

    def stopped_cb(evt):
        done.set()

    recognizer.recognized.connect(recognized_cb)
    recognizer.canceled.connect(canceled_cb)
    recognizer.session_stopped.connect(stopped_cb)

    recognizer.start_continuous_recognition()
    done.wait(timeout=120)
    recognizer.stop_continuous_recognition()

    if error_msg:
        return "", {}, error_msg

    full_transcript = " ".join(all_texts)

    result = {"results": all_json_results, "full_transcript": full_transcript}
    return full_transcript, result, None


def run_azure_assessment(audio_file_path):
    language = "en-US"

    transcript, raw_details, err = continuous_assessment_from_wav(
        audio_file_path, language
    )

    if err:

        return {"error": f"Azure assessment returned error: {err}"}

    if not transcript:
        return {"error": "No transcript recognized."}

    result_json = convert_azure_result(raw_details["results"])

    if result_json is None:
        return {"error": "Failed to convert Azure assessment results."}

    file_root, _ = os.path.splitext(audio_file_path)
    filename = f"{file_root}_azure.json"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(json.dumps(result_json, indent=4, ensure_ascii=False))

    return result_json
