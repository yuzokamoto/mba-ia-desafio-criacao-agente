"""Gemini com repetição limitada para falhas de transporte sem resposta."""

import asyncio
import logging
from collections.abc import AsyncGenerator

import httpx
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

logger = logging.getLogger(__name__)


class ResilientGemini(Gemini):
    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yielded = False
        for attempt in range(4):
            try:
                async for response in super().generate_content_async(llm_request, stream=stream):
                    yielded = True
                    yield response
                return
            except httpx.TransportError as exc:
                if stream or yielded or attempt == 3:
                    raise
                delay = 2 ** attempt
                logger.warning("Falha de transporte Gemini (%s); repetição %s/3 em %ss.",
                               type(exc).__name__, attempt + 1, delay)
                await asyncio.sleep(delay)
