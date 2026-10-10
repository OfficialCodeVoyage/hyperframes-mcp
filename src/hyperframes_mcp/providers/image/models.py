"""Data models for AI image generation requests and results."""

from pydantic import BaseModel, ConfigDict


class AIImageRequest(BaseModel):
    """Request parameters for AI image generation."""

    model_config = ConfigDict(frozen=True)

    prompt: str
    preset: str = "draft"
    size: str = "1024x1024"


class AIImageResult(BaseModel):
    """Result from an AI image generation call."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    image_bytes: bytes
    width: int
    height: int
    quality: str
    cost_usd: float
    model: str = "gpt-image-1"
    provider: str = "openai"
