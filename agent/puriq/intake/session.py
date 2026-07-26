"""Estado de sesión del intake web (Session_Store, Pieza 7, DD-5/DD-6).

Módulo de E/S **sin FastAPI** (misma frontera que `wizard/asset_store.py` y
`wizard/qa_store.py` del Hito 1): solo importa de `puriq.config` y de la
biblioteca estándar. Persiste el historial de la conversación y la fase del
Intake_Guion en `content/.intake-session.json`, redactado y de forma atómica,
para dar **continuidad** entre visitas (Req 9, 10).

El contrato en disco (los 3 JSON del wizard) sigue siendo la **fuente de verdad**
(Req 9.4): el Session_Store solo guarda continuidad de la charla y nunca deriva
los `missing` (esos se derivan del Contract_State vigente, Req 10.3).

Invariantes:

- `save_session` aplica `config.redact_value` al historial y a la fase antes de
  escribir, de modo que ningún valor de secreto quede persistido (Req 9.3), crea
  `content/` si falta (Req 9.2) y escribe con temp + `os.replace` (mismo patrón
  atómico que `contracts.save_contract`).
- `load_session` es **tolerante**: ante ausencia, JSON inválido o estructura
  inesperada devuelve `Session(history=[], phase=None)` sin fallar (Req 10.1,
  10.2). Nunca deriva `missing` de aquí.

Formato del archivo: ``{"phase": <phase>, "history": [...]}``.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from puriq import config

# Ruta relativa del Session_Store dentro del Project_Root (Req 9.2).
_SESSION_RELPATH = "content/.intake-session.json"


@dataclass
class Session:
    """Sesión de intake persistida: historial serializable y fase en curso.

    Attributes:
        history: lista de mensajes serializables de la conversación.
        phase: fase del Intake_Guion en curso (1..9), o ``None`` si no hay una.
    """

    history: list[dict]
    phase: str | None


def _session_path(project: Path) -> Path:
    """Ruta absoluta del Session_Store para un Project_Root dado."""
    return Path(project) / _SESSION_RELPATH


def load_session(project: Path) -> Session:
    """Carga la sesión previa; tolerante a ausencia/corrupción (Req 10.1, 10.2).

    Si el archivo no existe, su JSON no es legible o su estructura es inesperada
    (no es un objeto, `history` no es una lista, etc.), devuelve
    ``Session(history=[], phase=None)`` SIN fallar. Nunca deriva `missing` de
    aquí: los faltantes se derivan del Contract_State vigente (Req 10.3).
    """
    path = _session_path(project)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return Session(history=[], phase=None)

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return Session(history=[], phase=None)

    if not isinstance(data, dict):
        return Session(history=[], phase=None)

    history = data.get("history")
    if not isinstance(history, list):
        return Session(history=[], phase=None)

    phase = data.get("phase")
    if phase is not None and not isinstance(phase, str):
        phase = None

    return Session(history=history, phase=phase)


def save_session(project: Path, history: list[dict], phase: str | None) -> None:
    """Persiste historial + fase redactados y de forma atómica (Req 9.1, 9.3).

    Aplica `config.redact_value` al historial y a la fase antes de escribir, de
    modo que ningún valor de secreto quede persistido (Req 9.3), crea `content/`
    si falta (Req 9.2) y escribe con temp + `os.replace` (mismo patrón atómico
    que `contracts.save_contract`) para no dejar el archivo a medias.

    Formato del archivo: ``{"phase": <phase>, "history": [...]}``.
    """
    path = _session_path(project)

    redacted_history = config.redact_value(history)
    redacted_phase = config.redact_value(phase)

    payload = json.dumps(
        {"phase": redacted_phase, "history": redacted_history},
        ensure_ascii=False,
        indent=2,
    )

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica: temp en el mismo directorio + os.replace.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        # Si algo falla tras crear el temp, no dejar basura ni tocar el destino.
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
