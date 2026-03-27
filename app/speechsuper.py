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

    words = data.get("words", [])
    sentence_end_indices = {
        i
        for i, w in enumerate(words)
        if any(
            wp.get("charType") == 1 and wp.get("part") in _SENTENCE_END_PUNCT
            for wp in w.get("word_parts", [])
        )
    }

    for i, w in enumerate(words):
        # 1. 處理 Phonetic Clarity (從 phonics 提取事實)
        phonics_facts = []
        for p in w.get("phonics", []):
            score = p.get("overall", 0)
            spell = p.get("spell", "")
            phoneme = "/".join(p.get("phoneme", []))

            if score >= 80:
                status = "Excellent"
            elif score >= 60:
                status = "Clear"
            elif score >= 40:
                status = "Noticeable Accent"
            elif score >= 20:
                status = "Weak/Distorted"
            else:
                status = "Incorrect/Missing"

            phonics_facts.append(f"'{spell}' as /{phoneme}/: {status}")

        # 2. 提取物理重音位置
        detected_stress = [
            f"/{p.get('phoneme')}/"
            for p in w.get("phonemes", [])
            if p.get("stress_mark") == 1
        ]

        # 3. 構建單詞分析物件
        word_item = {
            "index": i,
            "word": w.get("word", ""),
            "phonetic_clarity": phonics_facts,
            "stress": {
                "expected_at": get_primary_stress_phoneme(w.get("word", "")),
                "detected_at": detected_stress,
            },
            "linking_details": {
                "is_linkable_opportunity": w.get("linkable", 0) == 1,
                "was_actually_linked": w.get("linked", 0) == 1,
            },
            "pause_duration_after_ms": w.get("pause", {}).get("duration", 0),  # 保留
        }
        report_dict["word_level_analysis"].append(word_item)

    unfilled_pauses = [
        item["pause_duration_after_ms"]
        for item in report_dict["word_level_analysis"]
        if item["pause_duration_after_ms"] >= 250
        and item["index"] not in sentence_end_indices
    ]

    total_words = len(report_dict["word_level_analysis"])
    phw = round((len(unfilled_pauses) / total_words * 100), 1) if total_words > 0 else 0

    report_dict["metadata"]["unfilled_pause_count"] = len(unfilled_pauses)
    report_dict["metadata"]["unfilled_pauses_per_100_words"] = phw

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

        file_name = audio_file_path.replace(".wav", ".json")
        with open(file_name, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=4)

        if not result_data or not result_data.get("result"):
            return {"error": "SpeechSuper returned empty result."}

        report_dict = generate_speech_super_report_json(result_data["result"])

        return report_dict

    except Exception as e:
        print(f"SpeechSuper Error: {e}")
        return {"error": str(e)}
