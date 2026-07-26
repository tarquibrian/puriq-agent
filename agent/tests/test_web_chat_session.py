"""Pruebas de propiedad del Session_Store del chat web (spec conversational-web-chat).

Implementa las propiedades 11, 12 y 13 de la sección "Correctness Properties" del
diseño, sobre `agent/puriq/intake/session.py` (`load_session`/`save_session`). Cada
prueba:

  - usa **Hypothesis** con un mínimo de 100 iteraciones (`@settings(max_examples=100)`),
  - opera sobre un **directorio de proyecto temporal** aislado por ejemplo
    (subdirectorio único bajo `tmp_path`) para no cruzar E/S entre ejemplos,
  - lleva el comentario de trazabilidad
    `# Feature: conversational-web-chat, Property {N}: {texto}`.

El Session_Store solo guarda continuidad de la charla (historial + fase) redactado
y de forma atómica; el contrato en disco sigue siendo la fuente de verdad.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from puriq import config
from puriq.intake import session

#: Configuración común de PBT: >=100 iteraciones, sin deadline (E/S en tmp), y se
#: suprime el health-check de fixture de función (usamos `tmp_path` como raíz y
#: creamos un subdirectorio único por ejemplo, así que el aislamiento es real).
pbt = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_ASCII_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _new_project(tmp_path: Path) -> Path:
    """Crea un subdirectorio único bajo `tmp_path` para aislar la E/S por ejemplo."""
    return Path(tempfile.mkdtemp(dir=tmp_path))


# --- Estrategias -------------------------------------------------------------
# Valores JSON que hacen round-trip exacto por json.dumps/json.loads: se excluyen
# floats (precisión/NaN) y se usan solo claves de texto (JSON serializa las claves
# a string, así que restringirlas evita divergencias en el round-trip).
_json_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.text(max_size=40),
)

_json_value = st.recursive(
    _json_scalar,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4),
    ),
    max_leaves=12,
)

# Un "mensaje" del historial: dict serializable con claves de texto.
_message = st.dictionaries(st.text(min_size=1, max_size=8), _json_value, max_size=5)

# Historial: lista de mensajes serializables.
_history = st.lists(_message, max_size=6)

# Fase del Intake_Guion: una cadena (1..9) o None.
_phase = st.one_of(st.none(), st.text(max_size=12), st.sampled_from(["1", "2", "3", "9"]))


# =============================================================================
# Property 11 (task 1.2)
# =============================================================================
# Feature: conversational-web-chat, Property 11: El Session_Store hace round-trip del historial y la fase
@pbt
@given(history=_history, phase=_phase)
def test_p11_session_store_round_trips_history_and_phase(tmp_path, history, phase):
    """Tras `save_session`, un `load_session` posterior devuelve historial y fase equivalentes.

    Validates: Requirements 9.1, 10.1
    """
    project = _new_project(tmp_path)

    session.save_session(project, history, phase)
    loaded = session.load_session(project)

    assert loaded.history == history
    assert loaded.phase == phase


# =============================================================================
# Property 12 (task 1.3)
# =============================================================================
def _is_valid_session_text(text: str) -> bool:
    """Indica si `text` se interpreta como una sesión válida (dict con `history` lista)."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("history"), list)


# Contenidos que NO representan una sesión válida (ausencia se modela con `write=False`).
_corrupt_content = st.one_of(
    st.text(max_size=60),                                   # texto arbitrario (JSON inválido)
    st.integers().map(json.dumps),                          # JSON escalar (no dict)
    st.booleans().map(json.dumps),
    st.lists(st.integers(), max_size=5).map(json.dumps),    # JSON lista (no dict)
    st.just("null"),
    st.just("{}"),                                          # dict sin `history`
    st.just('{"history": "no soy lista"}'),                 # `history` no es lista
    st.just('{"history": {}}'),
    st.just("{ not valid json"),
    st.just(""),
)


# Feature: conversational-web-chat, Property 12: La carga de sesión es tolerante a ausencia o corrupción
@pbt
@given(write=st.booleans(), content=_corrupt_content)
def test_p12_load_session_tolerates_absence_or_corruption(tmp_path, write, content):
    """Ante sesión ausente o ilegible, `load_session` devuelve Session(history=[], phase=None) sin lanzar.

    Validates: Requirements 10.2
    """
    project = _new_project(tmp_path)

    if write:
        # Descarta los pocos contenidos que sí serían una sesión válida.
        assume(not _is_valid_session_text(content))
        session_path = project / "content" / ".intake-session.json"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(content, encoding="utf-8")
    # else: el archivo no existe (caso "ausente").

    loaded = session.load_session(project)  # no debe lanzar

    assert isinstance(loaded, session.Session)
    assert loaded.history == []
    assert loaded.phase is None


# =============================================================================
# Property 13 (task 1.4)
# =============================================================================
@contextlib.contextmanager
def _registered_secret(value: str):
    """Registra `value` como secreto (vía `config.get_env(secret=True)`) y restaura."""
    name = "PURIQ_TEST_SECRET_WEBCHAT_P13"
    saved_env = os.environ.get(name)
    saved_names = set(config._secret_names)
    os.environ[name] = value
    config.get_env(name, secret=True)
    try:
        yield
    finally:
        config._secret_names = saved_names
        if saved_env is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = saved_env


_secret_value = st.text(alphabet=_ASCII_ALNUM, min_size=12, max_size=30)


# Feature: conversational-web-chat, Property 13: Ningún secreto queda persistido en el Session_Store
@pbt
@given(
    secret=_secret_value,
    prefix=st.text(max_size=15),
    suffix=st.text(max_size=15),
    in_phase=st.booleans(),
)
def test_p13_no_secret_persisted_in_session_store(tmp_path, secret, prefix, suffix, in_phase):
    """El contenido escrito en el Session_Store no contiene el valor crudo de un secreto.

    Validates: Requirements 9.3
    """
    project = _new_project(tmp_path)

    with _registered_secret(secret):
        # El secreto viaja verbatim en el historial (contenido de un mensaje y
        # anidado) y, opcionalmente, en la fase.
        history = [
            {"role": "user", "content": secret},
            {"role": "assistant", "content": f"{prefix}{secret}{suffix}"},
            {"role": "tool", "meta": {"nota": secret, "otros": [secret, "ok"]}},
        ]
        phase = secret if in_phase else "3"

        session.save_session(project, history, phase)

        raw = (project / "content" / ".intake-session.json").read_text(encoding="utf-8")
        assert secret not in raw


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
