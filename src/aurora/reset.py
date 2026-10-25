"""Restaura reservas e visitantes sem apagar sessões nem códigos emitidos."""

from .config import Settings
from .storage import Storage


def main():
    settings = Settings.from_env()
    Storage(settings.runtime_dir / "aurora.db", settings.data_dir).reset()
    print("Reservas e visitantes restaurados. Sessões e histórico de códigos preservados.")
