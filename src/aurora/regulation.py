"""Busca determinística que devolve somente um capítulo do regulamento."""

import re
import unicodedata
from pathlib import Path


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


class Regulation:
    def __init__(self, path: Path):
        text = path.read_text()
        self.chapters = re.findall(r"^## Capítulo .*?(?=^## Capítulo |\Z)", text, re.M | re.S)

    def search(self, query: str) -> dict:
        stopwords = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "para", "que", "qual", "quais", "como", "ate", "ao", "aos", "um", "uma", "funciona", "horas", "posso"}
        tokens = set(re.findall(r"[a-z0-9]+", normalize(query))) - stopwords
        # Sinônimos ampliam o vocabulário sem misturar capítulos na resposta.
        synonyms = {"cachorro": "animais", "gato": "animais", "barulho": "silencio", "mudar": "mudancas", "carro": "garagem"}
        tokens |= {synonyms[t] for t in tokens if t in synonyms}
        def score(chapter):
            title, _, body = normalize(chapter).partition("\n")
            title_words = set(re.findall(r"[a-z0-9]+", title))
            body_words = set(re.findall(r"[a-z0-9]+", body))
            return sum(20 * (token in title_words) + (token in body_words) for token in tokens)
        best = max(self.chapters, key=score)
        if not tokens or score(best) == 0:
            return {"mensagem": "Não encontrei um capítulo correspondente; detalhe o assunto."}
        return {"capitulo": best.splitlines()[0].removeprefix("## "), "texto": best.strip()}
