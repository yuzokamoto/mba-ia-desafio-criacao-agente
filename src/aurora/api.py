"""API HTTP do assistente do Residencial Aurora."""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .config import Settings
from .service import ConversationService


class SessionInput(BaseModel):
    apartamento: str


class MessageInput(BaseModel):
    texto: str = Field(min_length=1)


class ConfirmationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    confirmado: StrictBool


def create_app(settings: Settings | None = None, model_override=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        app.state.service = ConversationService(settings, model_override)
        yield
        await app.state.service.runner.close()

    app = FastAPI(title="Residencial Aurora", lifespan=lifespan)

    @app.post("/sessoes", status_code=201)
    async def create_session(body: SessionInput):
        return {"session_id": await app.state.service.create(body.apartamento)}

    @app.post("/sessoes/{session_id}/mensagens")
    async def message(session_id: str, body: MessageInput):
        return await app.state.service.message(session_id, body.texto)

    @app.post("/sessoes/{session_id}/confirmacoes")
    async def confirm(session_id: str, body: ConfirmationInput):
        return await app.state.service.confirm(session_id, body.id, body.confirmado)

    @app.get("/sessoes/{session_id}/eventos")
    async def events(session_id: str):
        service = app.state.service
        async with service.lock(session_id):
            session = await service.session(session_id)
            return [event.model_dump(mode="json", by_alias=True, exclude_none=True) for event in session.events]

    @app.get("/apartamentos/{number}/reservas")
    async def reservations(number: str):
        return app.state.service.storage.reservations(number)

    @app.get("/apartamentos/{number}/visitantes")
    async def visitors(number: str):
        return app.state.service.storage.visitors(number)

    return app


def main():
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)
