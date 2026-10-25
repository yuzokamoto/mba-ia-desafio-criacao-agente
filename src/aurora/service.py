"""Orquestração do Runner e confirmações por sessão persistida."""

import asyncio
from uuid import uuid4

from fastapi import HTTPException
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from .agents import create_agent_app
from .config import Settings
from .storage import Storage
from .tools import ToolSet

APP_NAME = "aurora"
USER_ID = "resident"
CONFIRMATION = "adk_request_confirmation"


def pending_confirmations(events):
    calls = {}
    answered = set()
    for event in events:
        for call in event.get_function_calls():
            if call.name == CONFIRMATION:
                args = call.args or {}
                original = args.get("originalFunctionCall", {})
                confirmation = args.get("toolConfirmation", {})
                calls[call.id] = {"id": call.id, "acao": original.get("name", "ação"),
                                  "detalhes": confirmation.get("payload") or {}}
        for response in event.get_function_responses():
            if response.id:
                answered.add(response.id)
    return [value for key, value in calls.items() if key not in answered]


class ConversationService:
    def __init__(self, settings: Settings, model_override=None):
        self.storage = Storage(settings.runtime_dir / "aurora.db", settings.data_dir)
        self.sessions = SqliteSessionService(str(settings.runtime_dir / "sessions.db"))
        self.runner = Runner(app=create_agent_app(settings, ToolSet(self.storage), model_override),
                             session_service=self.sessions)
        self.locks = {}

    def lock(self, session_id):
        return self.locks.setdefault(session_id, asyncio.Lock())

    async def create(self, apartment):
        if not self.storage.apartment_exists(apartment):
            raise HTTPException(404, "Apartamento não encontrado.")
        session_id = str(uuid4())
        await self.sessions.create_session(app_name=APP_NAME, user_id=USER_ID,
                                           session_id=session_id, state={"apartamento": apartment})
        self.storage.register_session(session_id, apartment)
        return session_id

    async def session(self, session_id):
        if self.storage.session_apartment(session_id) is None:
            raise HTTPException(404, "Sessão não encontrada.")
        session = await self.sessions.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
        if session is None:
            raise HTTPException(404, "Sessão não encontrada.")
        return session

    async def message(self, session_id, text):
        async with self.lock(session_id):
            session = await self.session(session_id)
            pending = pending_confirmations(session.events)
            if pending:
                return {"resposta": "Responda à confirmação pendente no aplicativo antes de continuar.",
                        "confirmacoes_pendentes": pending}
            return await self.run(session_id, types.Content(role="user", parts=[types.Part(text=text)]))

    async def confirm(self, session_id, confirmation_id, confirmed):
        async with self.lock(session_id):
            session = await self.session(session_id)
            if confirmation_id not in {item["id"] for item in pending_confirmations(session.events)}:
                raise HTTPException(409, "Confirmação não está pendente nesta sessão.")
            content = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id=confirmation_id, name=CONFIRMATION, response={"confirmed": confirmed}))])
            return await self.run(session_id, content)

    async def run(self, session_id, content):
        final_text = ""
        async for event in self.runner.run_async(user_id=USER_ID, session_id=session_id, new_message=content):
            if event.is_final_response() and event.content and event.author != "user":
                text = "".join(part.text for part in event.content.parts or [] if part.text and not part.thought)
                if text:
                    final_text = text
        session = await self.session(session_id)
        return {"resposta": final_text, "confirmacoes_pendentes": pending_confirmations(session.events)}
