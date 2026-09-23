"""Parser de los archivos de la KB (app/kb/*.md) a objetos Chunk.

Formato esperado de cada chunk:

    ### CHUNK <id> — <título>
    Intent: <intent>
    Contenido: <texto, puede ocupar varias líneas>
    Metadatos: <JSON, puede ocupar varias líneas>
"""

import json
import re
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.models.schemas import Chunk

_HEADER_RE = re.compile(r"^###\s+CHUNK\s+(?P<id>\S+)\s+[—-]\s+(?P<titulo>.+?)\s*$", re.MULTILINE)
_BODY_RE = re.compile(
    r"^Intent:\s*(?P<intent>.+?)\s*$"
    r".*?^Contenido:\s*(?P<contenido>.+?)"
    r"^Metadatos:\s*(?P<metadatos>\{.*?\})\s*$",
    re.MULTILINE | re.DOTALL,
)
_POLICY_RE = re.compile(r"^(?P<policy>[A-Z]+-[A-Z]+)-(?P<version>\w+)$")


class KBParseError(ValueError):
    pass


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def parse_policy_file(path: Path) -> list[Chunk]:
    source_policy = path.stem
    match = _POLICY_RE.match(source_policy)
    if not match:
        raise KBParseError(f"Nombre de archivo de política inválido: {path.name}")
    version = match.group("version")

    text = path.read_text(encoding="utf-8")
    headers = list(_HEADER_RE.finditer(text))
    if not headers:
        raise KBParseError(f"No se encontraron chunks en {path.name}")

    chunks: list[Chunk] = []
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[header.end():end]
        local_id = header.group("id")

        fields = _BODY_RE.search(body)
        if not fields:
            raise KBParseError(f"{path.name} CHUNK {local_id}: faltan Intent/Contenido/Metadatos")
        try:
            metadatos = json.loads(fields.group("metadatos"))
        except json.JSONDecodeError as exc:
            raise KBParseError(f"{path.name} CHUNK {local_id}: metadatos no son JSON válido") from exc

        chunks.append(
            Chunk(
                chunk_id=f"{source_policy}-{local_id}",
                titulo=header.group("titulo"),
                intent=fields.group("intent"),
                contenido=_normalize_ws(fields.group("contenido")),
                source_policy=source_policy,
                version=version,
                metadatos=metadatos,
            )
        )
    return chunks


def load_chunks(kb_path: Optional[Path] = None) -> list[Chunk]:
    kb_dir = kb_path or get_settings().kb_path
    files = sorted(Path(kb_dir).glob("*.md"))
    if not files:
        raise KBParseError(f"No hay archivos .md en {kb_dir}")

    chunks = [chunk for f in files for chunk in parse_policy_file(f)]
    ids = [c.chunk_id for c in chunks]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise KBParseError(f"chunk_id duplicados: {sorted(duplicates)}")
    return chunks
