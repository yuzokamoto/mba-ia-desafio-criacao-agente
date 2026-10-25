"""Configuração de caminhos e modelos da aplicação."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = ROOT / "dados"
    runtime_dir: Path = ROOT / "var"
    model: str = "gemini-2.5-flash"
    main_model: str = "gemini-2.5-flash"
    specialists_model: str = "gemini-2.5-flash"

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        model = os.getenv("AURORA_MODEL") or "gemini-2.5-flash"
        return cls(model=model, main_model=os.getenv("AURORA_MODEL_MAIN") or model,
                   specialists_model=os.getenv("AURORA_MODEL_SPECIALISTS") or model)
