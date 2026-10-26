"""Tools do ADK; identidade e confirmação são verificadas em código."""

import json
from datetime import date as calendar_date

from google.adk.tools import ToolContext

from .regulation import Regulation, normalize
from .storage import Storage


class ToolSet:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.areas = json.loads((storage.data_dir / "areas.json").read_text())
        self.regulation = Regulation(storage.data_dir / "regulamento.md")

    def identity(self, context: ToolContext):
        registered = self.storage.session_apartment(context.session.id)
        return registered if registered and context.state.get("apartamento") == registered else None

    def area(self, value):
        for area in self.areas:
            if normalize(value.strip()) in (normalize(area["id"]), normalize(area["nome"])):
                return area
        return None

    @staticmethod
    def date(value):
        try:
            parsed = calendar_date.fromisoformat(value)
            return parsed.isoformat() if parsed.isoformat() == value else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def confirmation(context, payload):
        if context.tool_confirmation is None:
            context.request_confirmation(
                hint="Responda à confirmação no aplicativo para executar a ação.", payload=payload)
            return {"status": "pendente", "mensagem": "Confirmação pendente: responda pelo aplicativo."}
        if not context.tool_confirmation.confirmed:
            return {"status": "negada", "mensagem": "Confirmação negada pelo morador; nada foi executado."}
        return None

    def build(self):
        def list_my_reservations(tool_context: ToolContext) -> dict:
            """Lista exclusivamente as reservas ativas do apartamento autenticado."""
            apartment = self.identity(tool_context)
            if not apartment:
                return {"erro": "Identidade da sessão inválida."}
            return {"reservas": self.storage.reservations(apartment)}

        def list_my_visitors(tool_context: ToolContext) -> dict:
            """Lista exclusivamente visitantes do apartamento autenticado."""
            apartment = self.identity(tool_context)
            if not apartment:
                return {"erro": "Identidade da sessão inválida."}
            return {"visitantes": self.storage.visitors(apartment)}

        def check_availability(area: str, date: str, tool_context: ToolContext) -> dict:
            """Consulta disponibilidade sem revelar dados de quem reservou."""
            if not self.identity(tool_context):
                return {"erro": "Identidade da sessão inválida."}
            selected = self.area(area)
            if not selected:
                return {"erro": "Área desconhecida.", "areas_validas": self.areas}
            if not self.date(date):
                return {"erro": "Use uma data no formato AAAA-MM-DD."}
            return {"status": "livre" if self.storage.available(selected["id"], date) else "ocupada"}

        def reserve_area(area: str, date: str, tool_context: ToolContext) -> dict:
            """Reserva área; cobrança exige confirmação pelo aplicativo."""
            apartment = self.identity(tool_context)
            if not apartment:
                return {"erro": "Identidade da sessão inválida."}
            selected = self.area(area)
            if not selected:
                return {"erro": "Área desconhecida.", "areas_validas": self.areas}
            if not self.date(date):
                return {"erro": "Use uma data no formato AAAA-MM-DD."}
            operation_id = f"{tool_context.session.id}:{tool_context.function_call_id}"
            with self.storage.connect() as db:
                previous = db.execute("SELECT result FROM operations WHERE id=?", (operation_id,)).fetchone()
            if previous:
                result = json.loads(previous[0])
            else:
                if not self.storage.available(selected["id"], date):
                    return {"status": "ocupada", "mensagem": "A data está ocupada."}
                if selected["taxa"] > 0:
                    pending = self.confirmation(tool_context, {"area": selected["id"], "data": date, "taxa": selected["taxa"]})
                    if pending:
                        return pending
                result = self.storage.reserve(apartment, selected["id"], date, operation_id)
            if result["status"] == "reservada":
                fee = self.area(result["area"])["taxa"]
                formatted_fee = f"{fee:,.2f}".translate(str.maketrans({",": ".", ".": ","}))
                result = {**result, "taxa": fee, "cobranca": fee > 0,
                          "mensagem": (f"Reserva confirmada. Cobrança de R$ {formatted_fee} gerada."
                                       if fee > 0 else "Reserva confirmada. Sem cobrança.")}
            return result

        def cancel_my_reservation(tool_context: ToolContext, code: str | None = None,
                                  area: str | None = None, date: str | None = None) -> dict:
            """Cancela reserva própria por código ou área e data, sem confirmação."""
            apartment = self.identity(tool_context)
            if not apartment:
                return {"erro": "Identidade da sessão inválida."}
            if code:
                return self.storage.cancel(apartment, code=code)
            selected = self.area(area) if area else None
            if not selected:
                return {"erro": "Área desconhecida.", "areas_validas": self.areas}
            if not self.date(date):
                return {"erro": "Use uma data no formato AAAA-MM-DD."}
            return self.storage.cancel(apartment, area=selected["id"], date=date)

        def authorize_visitor(name: str, date: str, tool_context: ToolContext) -> dict:
            """Autoriza visitante somente após confirmação pelo aplicativo."""
            apartment = self.identity(tool_context)
            if not apartment:
                return {"erro": "Identidade da sessão inválida."}
            name = name.strip()
            if not name or len(name) > 200 or not self.date(date):
                return {"erro": "Informe nome e data válida no formato AAAA-MM-DD."}
            pending = self.confirmation(tool_context, {"nome": name, "data": date})
            if pending:
                return pending
            return self.storage.authorize(apartment, name, date,
                                          f"{tool_context.session.id}:{tool_context.function_call_id}")

        def search_regulation(query: str) -> dict:
            """Busca somente o capítulo mais relevante para a pergunta."""
            return self.regulation.search(query)

        return {function.__name__: function for function in (
            list_my_reservations, list_my_visitors, check_availability, reserve_area,
            cancel_my_reservation, authorize_visitor, search_regulation)}
