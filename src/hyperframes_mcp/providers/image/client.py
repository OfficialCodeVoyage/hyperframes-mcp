"""AIImageClient -- async wrapper around the OpenAI image API (the "openai" provider).

Wraps openai.AsyncOpenAI with:
- asyncio.Semaphore for concurrency control
- circuitbreaker for persistent server errors
- tenacity retries with exponential backoff for transient errors
- Graceful degradation when API key is missing

Follows the ElevenLabsClient pattern from
src/voice_generator/services/elevenlabs_client.py.
"""

import asyncio
import base64
import logging
from io import BytesIO

import openai
from circuitbreaker import circuit
from PIL import Image
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from hyperframes_mcp.providers.image.models import AIImageResult
from hyperframes_mcp.providers.image.settings import ImageSettings

logger = logging.getLogger(__name__)

PROVIDER_NAME = "openai"


class AIImageClient:
    """Async OpenAI image generation wrapper with circuit breaker and semaphore.

    The model, the presets (quality and size) and the per-image cost come from ``settings``
    (default: ``gpt-image-1``, draft = low 1024x1024, production = medium 1024x1024).
    Constructor DI pattern. Graceful degradation when API key is missing.
    Circuit breaker: 3 failures, 120s recovery -- 5xx errors ONLY.
    Retries: 3 attempts with exponential backoff, for rate limits (HTTP 429) only. A dropped connection is not retried:
    the request may have been billed, and sending it again could bill it twice.
    Semaphore: limits concurrent requests.
    """

    def __init__(
        self,
        api_key: str | None = None,
        max_concurrent: int = 3,
        settings: ImageSettings | None = None,
    ) -> None:
        settings = settings or ImageSettings()
        if settings.provider != PROVIDER_NAME:
            raise ValueError(f"AIImageClient serves provider {PROVIDER_NAME!r}, not {settings.provider!r}")
        self._settings = settings
        self.provider_name = PROVIDER_NAME
        self.model = settings.model
        self._api_key = api_key
        self._client: openai.AsyncOpenAI | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        if api_key:
            # no retries inside the SDK: this class decides what is retried, and a paid request is never sent twice
            # for an error that may have come after the provider billed it
            self._client = openai.AsyncOpenAI(api_key=api_key, max_retries=0)

    async def generate(
        self,
        prompt: str,
        preset: str = "draft",
    ) -> AIImageResult | None:
        """Generate a single image with the configured OpenAI model.

        Args:
            prompt: The generation prompt.
            preset: Preset name from the settings -- "draft" (default: low quality) or
                "production" (default: medium). An unknown name means "draft".

        Returns:
            AIImageResult with PNG bytes, dimensions, cost. None if not configured.
        """
        if not self._client:
            logger.warning("OpenAI not configured (no API key), returning None")
            return None

        async with self._semaphore:
            return await self._generate_with_retry(prompt, preset)

    async def _generate_with_retry(self, prompt: str, preset: str) -> AIImageResult:
        """Retry wrapper around the circuit-breaker-protected API call."""
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(openai.RateLimitError),  # a 429 is refused before billing; a dropped connection may not be
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                return await self._call_with_circuit_breaker(prompt, preset)

    @circuit(
        failure_threshold=3,
        recovery_timeout=120,
        name="openai_image",
        expected_exception=(openai.InternalServerError, openai.APIConnectionError),
    )
    async def _call_with_circuit_breaker(self, prompt: str, preset: str) -> AIImageResult:
        """Circuit-breaker-protected API call. Opens on 5xx errors only."""
        config = self._settings.preset(preset)

        response = await self._client.images.generate(
            model=self.model,
            prompt=prompt,
            quality=config.quality,
            size=config.size,
            output_format="png",
        )

        decoded_bytes = base64.b64decode(response.data[0].b64_json)

        # Validate with PIL and extract dimensions
        img = Image.open(BytesIO(decoded_bytes))
        w, h = img.size

        return AIImageResult(
            image_bytes=decoded_bytes,
            width=w,
            height=h,
            quality=config.quality,
            cost_usd=self._settings.unit_cost(preset),
            model=self.model,
            provider=PROVIDER_NAME,
        )
