"""Standardized error response schemas (API-005, API-006, API-007)."""
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    error: str
    detail: list
    request_id: str
