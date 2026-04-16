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
        description="The final band score for this criterion, in 0.5 increments (e.g. 6.0, 6.5, 7.0)."
    )
    feedback: FeedbackPayload
