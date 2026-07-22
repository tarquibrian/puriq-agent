"""Pruebas unitarias para `puriq.config` (Req 9.2, 9.3, 9.5).

Cubren:
  - `get_env` con la variable presente (lee el valor del entorno) -> Req 9.2.
  - `get_env(required=True)` con la variable ausente: lanza un error que NOMBRA
    la variable faltante -> Req 9.5.
  - `redact` no filtra secretos conocidos (credenciales AWS) ni los leidos con
    `secret=True` en textos de salida/error -> Req 9.3.

Estas pruebas manipulan el estado de proceso del modulo (`os.environ`, el flag
de carga del `.env` y el registro de nombres secretos), por lo que cada prueba
restaura ese estado con fixtures para no filtrar entre pruebas.
"""
from __future__ import annotations

import importlib

import pytest

from puriq import config


@pytest.fixture(autouse=True)
def _isolate_config_state(monkeypatch):
    """Aisla el estado global del modulo config entre pruebas.

    - Evita que `_load_dotenv` lea un `agent/.env` real (marca dotenv cargado).
    - Restaura el registro de nombres secretos a los valores por defecto.
    """
    # No cargar el .env real: simular que ya se cargo para que `_load_dotenv`
    # sea un no-op y las pruebas dependan solo de lo que definimos aqui.
    monkeypatch.setattr(config, "_dotenv_loaded", True)
    # Restaurar el registro de secretos a los conocidos por defecto tras la prueba.
    original_secret_names = set(config._secret_names)
    yield
    config._secret_names.clear()
    config._secret_names.update(original_secret_names)


# --- get_env: variable presente (Req 9.2) ---------------------------------

def test_get_env_returns_value_when_present(monkeypatch):
    monkeypatch.setenv("PURIQ_LLM_MODE", "bedrock")
    assert config.get_env("PURIQ_LLM_MODE") == "bedrock"


def test_get_env_optional_missing_returns_none(monkeypatch):
    monkeypatch.delenv("PURIQ_MISSING_OPTIONAL", raising=False)
    assert config.get_env("PURIQ_MISSING_OPTIONAL") is None


def test_get_env_empty_value_treated_as_missing_optional(monkeypatch):
    monkeypatch.setenv("PURIQ_EMPTY", "")
    assert config.get_env("PURIQ_EMPTY") is None


# --- get_env: requerida y ausente nombra la variable (Req 9.5) -------------

def test_get_env_required_missing_raises_named_error(monkeypatch):
    monkeypatch.delenv("PURIQ_REQUIRED_VAR", raising=False)
    with pytest.raises(config.MissingEnvVarError) as excinfo:
        config.get_env("PURIQ_REQUIRED_VAR", required=True)
    # El error debe NOMBRAR la variable faltante (Req 9.5).
    assert excinfo.value.name == "PURIQ_REQUIRED_VAR"
    assert "PURIQ_REQUIRED_VAR" in str(excinfo.value)


def test_get_env_required_empty_raises_named_error(monkeypatch):
    monkeypatch.setenv("PURIQ_REQUIRED_EMPTY", "   ")
    # Un valor no vacio en espacios NO es vacio -> se considera presente.
    assert config.get_env("PURIQ_REQUIRED_EMPTY", required=True) == "   "

    monkeypatch.setenv("PURIQ_REQUIRED_EMPTY", "")
    with pytest.raises(config.MissingEnvVarError) as excinfo:
        config.get_env("PURIQ_REQUIRED_EMPTY", required=True)
    assert "PURIQ_REQUIRED_EMPTY" in str(excinfo.value)


# --- redact: no filtra secretos (Req 9.3) ----------------------------------

def test_redact_masks_known_aws_credentials(monkeypatch):
    secret_key = "AKIAIOSFODNN7EXAMPLE"
    secret_val = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", secret_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret_val)

    text = f"error al conectar: id={secret_key} key={secret_val}"
    redacted = config.redact(text)

    assert secret_key not in redacted
    assert secret_val not in redacted
    assert "***" in redacted


def test_redact_masks_var_registered_via_secret_flag(monkeypatch):
    token = "super-secret-session-token-value"
    monkeypatch.setenv("MY_CUSTOM_TOKEN", token)
    # Leer con secret=True registra la variable como secreta.
    config.get_env("MY_CUSTOM_TOKEN", secret=True)

    redacted = config.redact(f"fallo con token {token}")
    assert token not in redacted
    assert "***" in redacted


def test_redact_leaves_non_secret_text_intact(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_SECURITY_TOKEN", raising=False)

    text = "mensaje normal sin secretos: puerto 4322"
    assert config.redact(text) == text


def test_redact_handles_empty_text():
    assert config.redact("") == ""


def test_redact_masks_all_occurrences_of_secret(monkeypatch):
    secret_val = "AKIAEXAMPLEREPEATED12"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", secret_val)
    text = f"{secret_val} y de nuevo {secret_val}"
    redacted = config.redact(text)
    assert secret_val not in redacted
    assert redacted.count("***") == 2
