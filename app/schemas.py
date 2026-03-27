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
    score: int = Field(description="The final integer band score for this criterion.")
    feedback: FeedbackPayload
