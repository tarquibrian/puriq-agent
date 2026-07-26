"""Almacén de la base de conocimiento Q&A del wizard (E/S, sin FastAPI, DD-4).

Este módulo alberga los helpers de E/S del Q&A que antes vivían dentro de
`wizard/server.py` (que arrastra FastAPI). Se relocalizan a un módulo neutral
para que tanto el servidor web como el núcleo de intake (`intake/tools.py`)
reutilicen exactamente la misma implementación sin acoplarse al servidor web
(DD-4). El comportamiento se preserva tal cual:

- `append_qa_entry(project, entry) -> str`: anexa un QA_Entry a `content/qa.json`
  sin indexarlo ni consumirlo (Req 5.1, 5.3) y devuelve la ruta relativa
  `content/qa.json` que se registra como knowledgeSource (Req 5.2).
- `register_knowledge_source(project, rel_path) -> dict`: registra `rel_path` en
  `Site_Config.modules.chatweb.knowledgeSource` via load-merge-save (Req 5.2).

Formato del QA_Store: un único archivo `content/qa.json` en la raíz del proyecto
con una lista JSON de entradas ``{"question", "answer"}``. Los QA_Entry se
**anexan** (no se pisan). La ruta relativa `content/qa.json` es la que se
registra en `Site_Config.modules.chatweb.knowledgeSource`, coherente con que el
knowledgeSource apunta al árbol `/content`.
"""
from __future__ import annotations

import json
from pathlib import Path

from puriq.wizard import contracts

# Almacenamiento de la base de conocimiento Q&A (Req 5.1, 5.2, 5.3).
# Un unico archivo `content/qa.json` en la raiz del proyecto con una lista JSON
# de entradas ``{"question", "answer"}``.
_CONTENT_DIRNAME = "content"
_QA_FILENAME = "qa.json"
_QA_RELPATH = f"{_CONTENT_DIRNAME}/{_QA_FILENAME}"

# Documento de estructura (modulos, hero, deploy) sobre el que se registra el
# knowledgeSource del chatweb.
_SITE_CONFIG_DOC = "site-config"


def _save_patch(project: Path, doc: str, patch: dict) -> dict:
    """Aplica load -> merge -> save sobre un documento del contrato (DD-1).

    Carga el documento existente (o su base minima), fusiona `patch` de forma no
    destructiva (`merge_document`) y valida-antes-de-escribir (`save_contract`).
    Devuelve el documento fusionado ya persistido. Propaga
    `ValueError`/`ValidationError` si la validacion contra el esquema falla (el
    llamador los mapea a un error accionable).
    """
    base = contracts._load_contract(project, doc)
    merged = contracts.merge_document(base, patch)
    contracts.save_contract(project, doc, merged)
    return merged


def _qa_fingerprint(entry: dict) -> tuple[str, str]:
    """Huella de un QA_Entry para detectar duplicados (criterio de deduplicacion).

    Dos entradas se consideran la MISMA cuando coinciden pregunta y respuesta
    tras recortar espacios en los bordes, comparando la **pregunta** de forma
    insensible a mayusculas/minusculas (es la clave de busqueda del Q&A) y la
    **respuesta** tal cual (recortada, respetando su capitalizacion, que es
    contenido publicable). Valores no string se normalizan a cadena vacia.
    """
    question = entry.get("question") if isinstance(entry, dict) else None
    answer = entry.get("answer") if isinstance(entry, dict) else None
    q = question.strip().casefold() if isinstance(question, str) else ""
    a = answer.strip() if isinstance(answer, str) else ""
    return (q, a)


def append_qa_entry(project: Path, entry: dict) -> str:
    """Anexa `entry` a `content/qa.json` sin indexarlo (Req 5.1, 5.3).

    Crea `<project>/content` si no existe y mantiene una lista JSON de entradas.
    Si el archivo previo esta corrupto o no es una lista, se reinicia con una
    lista nueva para no perder la entrada actual. Devuelve la ruta relativa
    `content/qa.json` que se registra como knowledgeSource (Req 5.2).

    **Idempotente ante duplicados:** si `entry` ya esta en el QA_Store no se
    vuelve a anexar. El criterio es el de `_qa_fingerprint`: misma pregunta
    (recortada e insensible a mayusculas/minusculas) y misma respuesta
    (recortada). En ese caso el archivo queda intacto y se devuelve igualmente la
    ruta relativa `content/qa.json`, sin lanzar: repetir el llamado es seguro
    tanto desde `POST /api/qa` como desde la intake tool `add_qa` (p. ej. cuando
    el modelo propone una Q&A y la vuelve a escribir al confirmarla). Entradas
    distintas se siguen anexando en orden.
    """
    content_dir = project / _CONTENT_DIRNAME
    content_dir.mkdir(parents=True, exist_ok=True)
    qa_path = content_dir / _QA_FILENAME

    entries: list = []
    if qa_path.exists():
        try:
            cargado = json.loads(qa_path.read_text(encoding="utf-8"))
            if isinstance(cargado, list):
                entries = cargado
        except (ValueError, OSError):
            entries = []

    huella = _qa_fingerprint(entry)
    ya_existe = any(
        isinstance(previa, dict) and _qa_fingerprint(previa) == huella
        for previa in entries
    )
    if ya_existe:
        return _QA_RELPATH

    entries.append(entry)
    qa_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _QA_RELPATH


def register_knowledge_source(project: Path, rel_path: str) -> dict:
    """Registra `rel_path` en `Site_Config.modules.chatweb.knowledgeSource` (Req 5.2).

    Carga `site-config` y arma un parche para `modules.chatweb`. Si `chatweb` ya
    existe con `enabled`/`order`, solo actualiza `knowledgeSource`; si no, crea
    el modulo con `enabled=True` y un `order` entero >= 1 (siguiente al maximo de
    los modulos presentes) para cumplir el esquema. Persiste via load-merge-save.
    """
    base = contracts._load_contract(project, _SITE_CONFIG_DOC)
    modules = base.get("modules") or {}
    chatweb = modules.get("chatweb")

    if isinstance(chatweb, dict) and "enabled" in chatweb and "order" in chatweb:
        chat_patch = {"knowledgeSource": rel_path}
    else:
        orders = [
            m.get("order", 0)
            for m in modules.values()
            if isinstance(m, dict) and isinstance(m.get("order"), int)
        ]
        siguiente = (max(orders) + 1) if orders else 1
        chat_patch = {
            "enabled": True,
            "order": siguiente,
            "knowledgeSource": rel_path,
        }

    return _save_patch(project, _SITE_CONFIG_DOC, {"modules": {"chatweb": chat_patch}})
