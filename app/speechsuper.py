# speechsuper.py
from http import HTTPStatus
import time
import hashlib
import requests
import json
import os
import re
from dotenv import load_dotenv

_cmudict_cache = None

_VOWEL_TO_IPA = {
    "aa": "ɑ",
    "ae": "æ",
    "ah": "ʌ",
    "ao": "ɔ",
    "aw": "aʊ",
    "ay": "aɪ",
    "eh": "ɛ",
    "er": "ɝ",
    "ey": "eɪ",
    "ih": "ɪ",
    "iy": "i",
    "ow": "oʊ",
    "oy": "ɔɪ",
    "uh": "ʊ",
    "uw": "u",
}


# ── IPA vowel set (for syllable alignment check) ──────────────────────
_IPA_VOWELS = set(_VOWEL_TO_IPA.values()) | {"ə", "ɚ", "e", "o", "a"}

# CMUdict IPA → SpeechSuper IPA equivalences
# SpeechSuper sometimes simplifies diphthongs or uses regional variants
_IPA_EQUIV = {
    "eɪ": {"eɪ", "e"},
    "ɪ": {"ɪ", "i"},
    "ɛ": {"ɛ", "æ", "e"},
    "ɑ": {"ɑ", "ɔ", "a"},
    "oʊ": {"oʊ", "o"},
    "aɪ": {"aɪ", "a"},
    "i": {"i", "ɪ"},
    "u": {"u", "ʊ"},
    "ʌ": {"ʌ", "ə"},
    "æ": {"æ", "ɛ"},
    "ɔ": {"ɔ", "ɑ"},
    "ɝ": {"ɝ", "ɚ", "ə"},
}


def _phoneme_matches(expected_ipa: str, candidate_ipa: str) -> bool:
    """Check if two IPA symbols match, accounting for notation differences."""
    if expected_ipa == candidate_ipa:
        return True
    return candidate_ipa in _IPA_EQUIV.get(expected_ipa, set())


def _check_stress_alignment(phonemes_raw: list, expected_ipa: str) -> str:
    """
    Compare SpeechSuper detected stress with CMUdict expected stress.
    Returns: "aligned" | "mismatch" | "no_data"

    SpeechSuper typically marks stress on the onset consonant of a syllable,
    while CMUdict marks stress on the syllable's vowel. This function checks
    whether both refer to the same syllable (gap <= 3 positions, with the
    detected consonant preceding the expected vowel).
    """
    if not phonemes_raw or not expected_ipa:
        return "no_data"

    # Find the primary stressed phoneme from SpeechSuper
    stressed_idx = None
    for idx, p in enumerate(phonemes_raw):
        if p.get("stress_mark") == 1:
            stressed_idx = idx
            break
    if stressed_idx is None:
        return "no_data"

    all_ph = [p["phoneme"] for p in phonemes_raw]
    detected_ph = all_ph[stressed_idx]

    # Find the expected vowel in the phonemes array (fuzzy match)
    exp_indices = [
        idx for idx, ph in enumerate(all_ph) if _phoneme_matches(expected_ipa, ph)
    ]
    if not exp_indices:
        # Expected phoneme not found in SpeechSuper array — can't verify
        return "no_data"

    closest_exp_idx = min(exp_indices, key=lambda x: abs(x - stressed_idx))
    gap = stressed_idx - closest_exp_idx  # negative = detected is before expected

    # Same position → trivially aligned
    if gap == 0:
        return "aligned"

    # Detected consonant immediately before expected vowel (onset of same syllable)
    # Allow gap up to -3 for consonant clusters like /str/, /gr/, /skr/
    detected_is_consonant = detected_ph not in _IPA_VOWELS
    expected_is_vowel = expected_ipa in _IPA_VOWELS or any(
        v in _IPA_VOWELS for v in _IPA_EQUIV.get(expected_ipa, set())
    )

    if detected_is_consonant and expected_is_vowel and -3 <= gap < 0:
        return "aligned"

    # Adjacent in either direction (tolerance for edge cases)
    if abs(gap) <= 1:
        return "aligned"

    return "mismatch"


def _get_cmudict():
    global _cmudict_cache
    if _cmudict_cache is None:
        try:
            import nltk

            try:
                _cmudict_cache = nltk.corpus.cmudict.dict()
            except LookupError:
                nltk.download("cmudict", quiet=True)
                _cmudict_cache = nltk.corpus.cmudict.dict()
        except Exception:
            _cmudict_cache = {}
    return _cmudict_cache


def get_primary_stress_phoneme(word: str) -> list:
    cmudict = _get_cmudict()
    clean = word.lower().rstrip(".,!?;:'\"-")
    if clean not in cmudict:
        return []
    for ph in cmudict[clean][0]:
        if ph.endswith("1"):
            base = re.sub(r"\d", "", ph).lower()
            return [f"/{_VOWEL_TO_IPA.get(base, base)}/"]
    return []


load_dotenv()


def generate_speech_super_report_json(data):
    report_dict = {
        "metadata": {
            "speed_wpm": data.get("speed", 0),
            "full_transcript": data.get("recognition", ""),
            "duration": data.get("numeric_duration", 0),
        },
        "word_level_analysis": [],
    }

    _SENTENCE_END_PUNCT = {".", "?", "!"}
    _CLAUSE_PUNCT = {",", ";", ":"}

    words = data.get("words", [])
    sentence_end_indices = {
        i
        for i, w in enumerate(words)
        if any(
            wp.get("charType") == 1 and wp.get("part") in _SENTENCE_END_PUNCT
            for wp in w.get("word_parts", [])
        )
    }
    clause_boundary_indices = {
        i
        for i, w in enumerate(words)
        if any(
            wp.get("charType") == 1 and wp.get("part") in _CLAUSE_PUNCT
            for wp in w.get("word_parts", [])
        )
    }

    # 字尾常見連讀省略輔音拼寫（word-final connected speech reduction）
    _CONNECTED_SPEECH_SPELLINGS = {
        "t",
        "d",
        "k",
        "ck",
        "p",
        "b",
        "g",
        "n",
        "ng",
        "m",
        "l",
    }

    for i, w in enumerate(words):
        # 1. 處理 Phonetic Clarity (從 phonics 提取事實)
        phonics_facts = []
        phonics_list = w.get("phonics", [])
        for p_idx, p in enumerate(phonics_list):
            score = p.get("overall", 0)
            spell = p.get("spell", "")
            phoneme = "/".join(p.get("phoneme", []))
            is_last_phoneme = p_idx == len(phonics_list) - 1

            if score >= 85:
                status = "Excellent"
            elif score >= 75:
                status = "Clear"
            elif score >= 55:
                status = "Noticeable Accent"
            elif score >= 40:
                status = "Weak/Distorted"
            elif (
                score == 0
                and is_last_phoneme
                and spell.lower() in _CONNECTED_SPEECH_SPELLINGS
            ):
                status = "Connected Speech"
            elif score == 0:
                status = "Missing"
            else:
                status = "Incorrect"

            phonics_facts.append(f"'{spell}' as /{phoneme}/: {status}")
            report_dict["metadata"]["_phoneme_total"] = (
                report_dict["metadata"].get("_phoneme_total", 0) + 1
            )
            if status == "Incorrect":
                report_dict["metadata"]["_phoneme_incorrect"] = (
                    report_dict["metadata"].get("_phoneme_incorrect", 0) + 1
                )
            elif status == "Missing":
                report_dict["metadata"]["_phoneme_missing"] = (
                    report_dict["metadata"].get("_phoneme_missing", 0) + 1
                )

        # 2. 提取物理重音位置 + syllable alignment verdict
        phonemes_raw = w.get("phonemes", None)
        if phonemes_raw is None:
            detected_stress = None
            stress_verdict = "no_data"
        else:
            detected_stress = [
                f"/{p.get('phoneme')}/"
                for p in phonemes_raw
                if p.get("stress_mark") == 1
            ] or None

            # Compute stress alignment verdict
            expected_stress_list = get_primary_stress_phoneme(w.get("word", ""))
            if expected_stress_list and detected_stress:
                expected_ipa = expected_stress_list[0].strip("/")
                stress_verdict = _check_stress_alignment(phonemes_raw, expected_ipa)
            else:
                stress_verdict = "no_data"

        # 3. 構建單詞分析物件
        link_type_raw = w.get("linkable_type", -1)
        if link_type_raw == 0:
            link_type = "consonant_to_vowel"
        elif link_type_raw == 1:
            link_type = "th_linking"
        elif link_type_raw == 3:
            link_type = "plosion"
        else:
            link_type = None

        word_item = {
            "index": i,
            "word": w.get("word", ""),
            "phonetic_clarity": phonics_facts,
            "stress": {
                "expected_at": get_primary_stress_phoneme(w.get("word", "")),
                "detected_at": detected_stress,
                "verdict": stress_verdict,
            },
            "linking_details": {
                "is_linkable_opportunity": w.get("linkable", 0) == 1,
                "was_actually_linked": w.get("linked", 0) == 1,
                "link_type": link_type,
            },
            "pause_duration_after_ms": w.get("pause", {}).get("duration", 0),
        }
        report_dict["word_level_analysis"].append(word_item)

    total_words = len(report_dict["word_level_analysis"])

    mid_phrase_pauses = [
        item
        for item in report_dict["word_level_analysis"]
        if item["pause_duration_after_ms"] >= 250
        and item["index"] not in sentence_end_indices
        and item["index"] not in clause_boundary_indices
    ]
    clause_boundary_pauses = [
        item
        for item in report_dict["word_level_analysis"]
        if item["pause_duration_after_ms"] >= 250
        and item["index"] not in sentence_end_indices
        and item["index"] in clause_boundary_indices
    ]
    unfilled_pauses = mid_phrase_pauses + clause_boundary_pauses

    phw = round(len(unfilled_pauses) / total_words * 100, 1) if total_words > 0 else 0.0
    mid_phw = (
        round(len(mid_phrase_pauses) / total_words * 100, 1) if total_words > 0 else 0.0
    )

    report_dict["metadata"]["unfilled_pause_count"] = len(unfilled_pauses)
    report_dict["metadata"]["unfilled_pauses_per_100_words"] = phw
    report_dict["metadata"]["mid_phrase_pause_count"] = len(mid_phrase_pauses)
    report_dict["metadata"]["mid_phrase_pauses_per_100_words"] = mid_phw
    report_dict["metadata"]["clause_boundary_pause_count"] = len(clause_boundary_pauses)

    total_phonemes = report_dict["metadata"].pop("_phoneme_total", 0)
    incorrect_phonemes = report_dict["metadata"].pop("_phoneme_incorrect", 0)
    missing_phonemes = report_dict["metadata"].pop("_phoneme_missing", 0)
    report_dict["metadata"]["phoneme_incorrect_count"] = incorrect_phonemes
    report_dict["metadata"]["phoneme_missing_count"] = missing_phonemes
    report_dict["metadata"]["phoneme_incorrect_rate"] = (
        round(incorrect_phonemes / total_phonemes * 100, 1)
        if total_phonemes > 0
        else 0.0
    )
    report_dict["metadata"]["phoneme_missing_rate"] = (
        round(missing_phonemes / total_phonemes * 100, 1) if total_phonemes > 0 else 0.0
    )

    # ── Aggregated PN signals (for Gemini scoring) ──────────────────────

    # 1. Clarity distribution + intelligibility %
    clarity_dist = {}
    for item in report_dict["word_level_analysis"]:
        for pc in item["phonetic_clarity"]:
            status = pc.rsplit(": ", 1)[-1]
            clarity_dist[status] = clarity_dist.get(status, 0) + 1
    report_dict["metadata"]["clarity_distribution"] = clarity_dist

    total_clarity = sum(clarity_dist.values())
    # Intelligibility = phonemes a listener would understand.
    # Includes: Excellent, Clear (high-quality production)
    #         + Noticeable Accent (still intelligible, per IELTS Band 8: "Accent has minimal effect on intelligibility")
    #         + Connected Speech (natural feature, NOT an error)
    # Excludes: Weak/Distorted, Incorrect, Missing (treated as not intelligible)
    intelligible = (
        clarity_dist.get("Excellent", 0)
        + clarity_dist.get("Clear", 0)
        + clarity_dist.get("Noticeable Accent", 0)
        + clarity_dist.get("Connected Speech", 0)
    )
    report_dict["metadata"]["clarity_intelligibility_pct"] = (
        round(intelligible / total_clarity * 100, 1) if total_clarity > 0 else 0.0
    )
    # High-quality production (Excellent + Clear only). Used as a Band 8 vs 9
    # tiebreaker — distinguishes "effortlessly understood, accent no effect" (Band 9)
    # from "easily understood but accent noticeable" (Band 8).
    excellent_clear = clarity_dist.get("Excellent", 0) + clarity_dist.get("Clear", 0)
    report_dict["metadata"]["clarity_high_quality_pct"] = (
        round(excellent_clear / total_clarity * 100, 1) if total_clarity > 0 else 0.0
    )

    # 2. Stress summary: genuine mismatches vs aligned (onset-consonant artifacts)
    stress_counts = {"aligned": 0, "mismatch": 0, "no_data": 0}
    stress_mismatch_words = []
    for item in report_dict["word_level_analysis"]:
        v = item["stress"]["verdict"]
        stress_counts[v] = stress_counts.get(v, 0) + 1
        if v == "mismatch":
            stress_mismatch_words.append(item["word"])
    # Stress mismatch rate: % of stress detections that were placed on the wrong syllable.
    # Denominator excludes "no_data" — words where stress couldn't be determined contain
    # no signal. Length-normalised so 3 errors in 30 words ≠ 3 errors in 200 words.
    stress_evaluable = stress_counts["aligned"] + stress_counts["mismatch"]
    stress_mismatch_rate = (
        round(stress_counts["mismatch"] / stress_evaluable * 100, 1)
        if stress_evaluable > 0
        else 0.0
    )
    report_dict["metadata"]["stress_alignment_summary"] = {
        "aligned_count": stress_counts["aligned"],
        "genuine_mismatch_count": stress_counts["mismatch"],
        "no_data_count": stress_counts["no_data"],
        "mismatch_rate": stress_mismatch_rate,
        "mismatch_words": stress_mismatch_words,
    }

    # 3. Linking rate (overall) + C-to-V breakdown
    link_opps = sum(
        1
        for item in report_dict["word_level_analysis"]
        if item["linking_details"]["is_linkable_opportunity"]
    )
    link_done = sum(
        1
        for item in report_dict["word_level_analysis"]
        if item["linking_details"]["was_actually_linked"]
    )
    report_dict["metadata"]["linking_rate"] = (
        round(link_done / link_opps * 100, 1) if link_opps > 0 else 0.0
    )
    report_dict["metadata"]["linking_opportunities"] = link_opps
    report_dict["metadata"]["linking_achieved"] = link_done

    cv_opps = sum(
        1
        for item in report_dict["word_level_analysis"]
        if item["linking_details"]["is_linkable_opportunity"]
        and item["linking_details"]["link_type"] == "consonant_to_vowel"
    )
    cv_done = sum(
        1
        for item in report_dict["word_level_analysis"]
        if item["linking_details"]["was_actually_linked"]
        and item["linking_details"]["link_type"] == "consonant_to_vowel"
    )
    report_dict["metadata"]["linking_cv_opportunities"] = cv_opps
    report_dict["metadata"]["linking_cv_achieved"] = cv_done
    report_dict["metadata"]["linking_cv_rate"] = (
        round(cv_done / cv_opps * 100, 1) if cv_opps > 0 else 0.0
    )

    # 4. Phoneme ceiling band (PN Step 1 pre-computed)
    rate = report_dict["metadata"]["phoneme_incorrect_rate"]
    if rate < 3:
        ceiling = 9
    elif rate < 8:
        ceiling = 8
    elif rate < 14:
        ceiling = 7
    elif rate < 26:
        ceiling = 6
    elif rate < 38:
        ceiling = 5
    else:
        ceiling = 4
    report_dict["metadata"]["phoneme_ceiling_band"] = ceiling

    return report_dict


def run_speech_super_assessment(audio_file_path):
    appKey = os.getenv("SPEECHSUPER_APP_KEY")
    secretKey = os.getenv("SPEECHSUPER_APP_SECRET")
    baseURL = os.getenv("SPEECHSUPER_API_URL")
    coreType = os.getenv("SPEECHSUPER_CORE_TYPE")

    timestamp = str(int(time.time()))
    userId = "ClarityEnglish"

    url = baseURL + coreType
    connectSig = hashlib.sha1(
        (appKey + timestamp + secretKey).encode("utf-8")
    ).hexdigest()
    startSig = hashlib.sha1(
        (appKey + timestamp + userId + secretKey).encode("utf-8")
    ).hexdigest()

    params = {
        "connect": {
            "cmd": "connect",
            "param": {
                "sdk": {"version": 16777472, "source": 9, "protocol": 2},
                "app": {
                    "applicationId": appKey,
                    "sig": connectSig,
                    "timestamp": timestamp,
                },
            },
        },
        "start": {
            "cmd": "start",
            "param": {
                "app": {
                    "userId": userId,
                    "applicationId": appKey,
                    "timestamp": timestamp,
                    "sig": startSig,
                },
                "audio": {
                    "audioType": "wav",
                    "channel": 1,
                    "sampleBytes": 2,
                    "sampleRate": 16000,
                },
                "request": {
                    "coreType": coreType,
                    "model": "non_native",
                    "tokenId": "tokenId",
                },
            },
        },
    }

    try:
        data = {"text": json.dumps(params)}
        headers = {"Request-Index": "0"}

        with open(audio_file_path, "rb") as f:
            files = {"audio": f}
            res = requests.post(url, data=data, headers=headers, files=files)

        result_data = res.json()

        if not result_data or not result_data.get("result"):
            return {"error": "SpeechSuper returned empty result."}

        report_dict = generate_speech_super_report_json(result_data["result"])

        file_name = audio_file_path.replace(".wav", ".json")
        with open(file_name, "w", encoding="utf-8") as f:
            json.dump(report_dict, f, ensure_ascii=False, indent=4)

        file_name_2 = audio_file_path.replace(".wav", "_raw_data.json")
        with open(file_name_2, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=4)

        return report_dict

    except Exception as e:
        print(f"SpeechSuper Error: {e}")
        return {"error": str(e)}
