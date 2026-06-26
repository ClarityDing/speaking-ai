# app/schemas.py
from pydantic import BaseModel, Field
from typing import List


class Strength(BaseModel):
    point: str
    quote: str


class Improvement(BaseModel):
    point: str
    suggestion: str


class FeedbackPayload(BaseModel):
    summary: str
    strengths: List[Strength]
    improvements: List[Improvement]


class GradingResult(BaseModel):
    score: float = Field(
        description="The final band score for this criterion, on the full IELTS scale 1.0-9.0 in 0.5 increments (e.g. 3.0, 4.5, 6.0, 7.5, 9.0). Use the WHOLE range — do not cluster around 6-7."
    )
    feedback: FeedbackPayload
