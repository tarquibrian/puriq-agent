"""Pruebas unitarias de seleccion de proveedor de LLM (DD-4, Tarea 7.8).

Verifican la fabrica `generate_content.get_provider` (DD-4):
  - `PURIQ_LLM_MODE=local`   -> `OllamaProvider` (fallback local, Req 3.9).
  - `PURIQ_LLM_MODE=bedrock` -> `BedrockProvider` con `PURIQ_BEDROCK_MODEL` (Req 3.8).
  - Modo ausente / cualquier otro valor -> `BedrockProvider` (por defecto, Req 3.8).

Se mockea el acceso a la configuracion (`get_env`) y las fronteras de
red/servicio (`boto3` para Bedrock, `ollama` para el modo local). Estas
librerias pueden no estar instaladas: se inyectan como stubs via
`sys.modules`, de modo que ninguna prueba realiza llamadas reales.

_Requirements: 3.8, 3.9_
"""
from __future__ import annotations

import json
import sys
import types
from unittest import mock

import pytest

from puriq.tools import generate_content


# --- helpers ---------------------------------------------------------------
def _fake_get_env(values: dict[str, str | None]):
    """Devuelve un sustituto de `get_env` que consulta `values` por nombre."""

    def _inner(name: str, *, required: bool = False, secret: bool = False):
        return values.get(name)

    return _inner


# --- seleccion de proveedor por PURIQ_LLM_MODE (Req 3.8 / 3.9) -------------
def test_get_provider_returns_ollama_when_mode_local():
    """`PURIQ_LLM_MODE=local` -> OllamaProvider (fallback local, Req 3.9)."""
    env = {"PURIQ_LLM_MODE": "local", "PURIQ_OLLAMA_MODEL": None}
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()

    assert isinstance(provider, generate_content.OllamaProvider)
    # Cumple el protocolo del adaptador de LLM.
    assert isinstance(provider, generate_content.LLMProvider)


def test_get_provider_returns_bedrock_when_mode_bedrock():
    """`PURIQ_LLM_MODE=bedrock` -> BedrockProvider con `PURIQ_BEDROCK_MODEL` (Req 3.8)."""
    env = {
        "PURIQ_LLM_MODE": "bedrock",
        "PURIQ_BEDROCK_MODEL": "anthropic.claude-3-haiku-20240307-v1:0",
    }
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()

    assert isinstance(provider, generate_content.BedrockProvider)
    # El modelo se toma de `PURIQ_BEDROCK_MODEL`.
    assert provider._model_id == "anthropic.claude-3-haiku-20240307-v1:0"
    assert isinstance(provider, generate_content.LLMProvider)


def test_get_provider_defaults_to_bedrock_when_mode_absent():
    """Sin `PURIQ_LLM_MODE` -> BedrockProvider por defecto (Req 3.8)."""
    env = {"PURIQ_LLM_MODE": None, "PURIQ_BEDROCK_MODEL": None}
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()

    assert isinstance(provider, generate_content.BedrockProvider)
    # Sin `PURIQ_BEDROCK_MODEL` cae al modelo por defecto del modulo.
    assert provider._model_id == generate_content._DEFAULT_BEDROCK_MODEL


def test_get_provider_defaults_to_bedrock_on_unknown_mode():
    """Un modo desconocido -> BedrockProvider por defecto (Req 3.8)."""
    env = {"PURIQ_LLM_MODE": "gpt-quantum", "PURIQ_BEDROCK_MODEL": None}
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()

    assert isinstance(provider, generate_content.BedrockProvider)


def test_get_provider_mode_local_is_case_insensitive_and_trimmed():
    """El modo se normaliza (mayusculas/espacios): ` LOCAL ` -> Ollama (Req 3.9)."""
    env = {"PURIQ_LLM_MODE": "  LOCAL  "}
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()

    assert isinstance(provider, generate_content.OllamaProvider)


# --- comportamiento minimo del proveedor Bedrock (boto3 mockeado) ----------
def test_bedrock_provider_completes_via_mocked_boto3():
    """BedrockProvider invoca `bedrock-runtime` de boto3 (sin red real, Req 3.8).

    Verifica que usa el `PURIQ_BEDROCK_MODEL` seleccionado y que extrae el texto
    de los bloques `content` de la respuesta de Claude.
    """
    fake_body = mock.Mock()
    fake_body.read.return_value = json.dumps(
        {"content": [{"type": "text", "text": "Hola turista"}]}
    ).encode("utf-8")
    fake_client = mock.Mock()
    fake_client.invoke_model.return_value = {"body": fake_body}

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = mock.Mock(return_value=fake_client)

    env = {
        "PURIQ_LLM_MODE": "bedrock",
        "PURIQ_BEDROCK_MODEL": "anthropic.claude-3-haiku-20240307-v1:0",
    }
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()
    assert isinstance(provider, generate_content.BedrockProvider)

    with mock.patch.dict(sys.modules, {"boto3": fake_boto3}):
        result = provider.complete("Describe un lugar")

    fake_boto3.client.assert_called_once_with("bedrock-runtime")
    # Se invoca el modelo seleccionado por `PURIQ_BEDROCK_MODEL`.
    call = fake_client.invoke_model.call_args
    assert call.kwargs["modelId"] == "anthropic.claude-3-haiku-20240307-v1:0"
    body = json.loads(call.kwargs["body"])
    assert body["messages"][0]["content"][0]["text"] == "Describe un lugar"
    assert result == "Hola turista"


# --- comportamiento minimo del proveedor Ollama (ollama mockeado) ----------
def test_ollama_provider_completes_via_mocked_ollama():
    """OllamaProvider genera con el modelo local via `ollama` (sin red real, Req 3.9)."""
    fake_ollama = types.ModuleType("ollama")
    fake_ollama.generate = mock.Mock(return_value={"response": "  Texto local  "})

    env = {"PURIQ_LLM_MODE": "local", "PURIQ_OLLAMA_MODEL": "llama3.2"}
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()
    assert isinstance(provider, generate_content.OllamaProvider)

    with mock.patch.dict(sys.modules, {"ollama": fake_ollama}):
        result = provider.complete("Traduce esto")

    fake_ollama.generate.assert_called_once_with(model="llama3.2", prompt="Traduce esto")
    # El proveedor normaliza (strip) la respuesta del modelo.
    assert result == "Texto local"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
