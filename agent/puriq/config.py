"""config: acceso seguro a la configuracion por variables de entorno (Req 9).

Centraliza la lectura de configuracion sensible (credenciales AWS, modelo y modo
del LLM, destino de deploy, etc.) desde variables de entorno definidas en
`agent/.env`, y provee `redact` para enmascarar valores de secretos en cualquier
texto de salida o de error.

Invariantes:
  - `get_env(name, required=True)` con la variable ausente lanza un error que
    NOMBRA la variable faltante (Req 9.5).
  - Los valores marcados como `secret=True` (y las credenciales AWS conocidas)
    nunca deben aparecer en mensajes de error ni en la salida del CLI (Req 9.3):
    `redact` los enmascara.
"""
from __future__ import annotations

import os
from pathlib import Path

# Mascara con la que se reemplazan los valores de secretos en `redact`.
_MASK = "***"

# Nombres de variables de entorno tratadas como secreto por defecto
# (credenciales AWS y afines). Sus valores siempre se enmascaran en `redact`.
_DEFAULT_SECRET_NAMES: frozenset[str] = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
    }
)

# Registro interno de nombres de variables tratadas como secreto. Arranca con
# los conocidos y crece cada vez que `get_env(..., secret=True)` lee una nueva.
_secret_names: set[str] = set(_DEFAULT_SECRET_NAMES)

# Ruta al `agent/.env`: este modulo vive en agent/puriq/config.py.
_DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
_dotenv_loaded = False


class MissingEnvVarError(RuntimeError):
    """Se lanza cuando una variable de entorno requerida no esta definida.

    El mensaje nombra la variable faltante para guiar al usuario (Req 9.5).
    """

    def __init__(self, name: str):
        self.name = name
        super().__init__(
            f"Falta la variable de entorno requerida '{name}'. "
            f"Definila en agent/.env (ver agent/.env.example)."
        )


def _load_dotenv() -> None:
    """Carga `agent/.env` en `os.environ` sin pisar variables ya definidas.

    Parser minimo (sin dependencias nuevas): ignora lineas vacias y comentarios,
    parte en el primer '=', recorta espacios y comillas envolventes. Las
    variables ya presentes en el entorno del proceso tienen prioridad.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    try:
        raw = _DOTENV_PATH.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def get_env(name: str, *, required: bool = False, secret: bool = False) -> str | None:
    """Lee una variable de entorno definida en `agent/.env` o en el proceso.

    Args:
        name: nombre de la variable de entorno.
        required: si es True y la variable falta (o esta vacia), lanza
            `MissingEnvVarError` nombrando la variable (Req 9.5).
        secret: si es True, registra la variable como secreta para que `redact`
            enmascare su valor en cualquier salida o mensaje de error (Req 9.3).

    Returns:
        El valor de la variable, o None si no esta definida y no es requerida.
    """
    _load_dotenv()
    if secret:
        _secret_names.add(name)
    value = os.environ.get(name)
    if value is None or value == "":
        if required:
            raise MissingEnvVarError(name)
        return None
    return value


def _secret_values() -> list[str]:
    """Valores actuales de las variables registradas como secreto (no vacios)."""
    _load_dotenv()
    values: list[str] = []
    for name in _secret_names:
        val = os.environ.get(name)
        if val:
            values.append(val)
    return values


def redact(text: str) -> str:
    """Enmascara valores de secretos conocidos en un texto de salida o error.

    Reemplaza cada valor de las variables registradas como secreto (credenciales
    AWS y las leidas con `secret=True`) por una mascara, de modo que ningun valor
    sensible aparezca en la salida del CLI ni en mensajes de error (Req 9.3).
    """
    if not text:
        return text
    # Enmascarar primero los valores mas largos evita reemplazos parciales
    # cuando un secreto es substring de otro.
    for value in sorted(set(_secret_values()), key=len, reverse=True):
        text = text.replace(value, _MASK)
    return text


def redact_value(value: object) -> object:
    """Aplica `redact` de forma recursiva a los strings de una estructura (Req 12.2).

    Variante recursiva de `redact` y unica fuente de verdad para redactar
    estructuras compuestas (respuestas del wizard, del MCP, etc.). Recorre dicts,
    listas y tuplas enmascarando cada string con `redact`, de modo que ningun
    valor de secreto configurado aparezca en la salida. Los valores no-string
    (numeros, booleanos, None) se devuelven sin cambios. Las tuplas se devuelven
    como listas (forma serializable a JSON).
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: redact_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return value
