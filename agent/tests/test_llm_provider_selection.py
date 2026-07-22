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

from puriq.config import MissingEnvVarError
from puriq.tools import generate_content


# --- helpers ---------------------------------------------------------------
def _fake_get_env(values: dict[str, str | None]):
    """Devuelve un sustituto de `get_env` que consulta `values` por nombre.

    Respeta el contrato de `config.get_env`: si `required=True` y el valor esta
    ausente/vacio, lanza `MissingEnvVarError` nombrando la variable (Req 9.5).
    """

    def _inner(name: str, *, required: bool = False, secret: bool = False):
        value = values.get(name)
        if required and (value is None or value == ""):
            raise MissingEnvVarError(name)
        return value

    return _inner


def _make_fake_httpx(response_payload: dict):
    """Crea un modulo `httpx` de mentira con `post` mockeado (sin red real).

    Devuelve `(fake_httpx, fake_response)`; `fake_response.json()` entrega
    `response_payload` y `raise_for_status` es un no-op registrable.
    """
    fake_response = mock.Mock()
    fake_response.json.return_value = response_payload
    fake_response.raise_for_status = mock.Mock()

    fake_httpx = types.ModuleType("httpx")
    fake_httpx.post = mock.Mock(return_value=fake_response)
    return fake_httpx, fake_response


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

    Verifica que usa el `PURIQ_BEDROCK_MODEL` seleccionado, que pasa la region
    resuelta desde `AWS_REGION` como `region_name` y que extrae el texto de los
    bloques `content` de la respuesta de Claude.
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
        "AWS_REGION": "us-east-1",
    }
    # `get_env` se mockea tambien durante `complete` para que la resolucion de
    # region (`AWS_REGION`) sea determinista y no dependa del entorno/.env.
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()
        assert isinstance(provider, generate_content.BedrockProvider)

        with mock.patch.dict(sys.modules, {"boto3": fake_boto3}):
            result = provider.complete("Describe un lugar")

    # El cliente `bedrock-runtime` recibe la region resuelta desde AWS_REGION.
    fake_boto3.client.assert_called_once_with(
        "bedrock-runtime", region_name="us-east-1"
    )
    # Se invoca el modelo seleccionado por `PURIQ_BEDROCK_MODEL`.
    call = fake_client.invoke_model.call_args
    assert call.kwargs["modelId"] == "anthropic.claude-3-haiku-20240307-v1:0"
    body = json.loads(call.kwargs["body"])
    assert body["messages"][0]["content"][0]["text"] == "Describe un lugar"
    assert result == "Hola turista"


def test_bedrock_provider_omits_region_when_aws_region_unset():
    """Sin `AWS_REGION`, el cliente se crea sin `region_name` (cadena boto3).

    Comprobacion offline (boto3 mockeado): con `AWS_REGION` ausente y sin depender
    de `AWS_DEFAULT_REGION`, el proveedor NO pasa `region_name`, dejando que boto3
    resuelva la region con su propia cadena de configuracion.
    """
    fake_body = mock.Mock()
    fake_body.read.return_value = json.dumps(
        {"content": [{"type": "text", "text": "ok"}]}
    ).encode("utf-8")
    fake_client = mock.Mock()
    fake_client.invoke_model.return_value = {"body": fake_body}

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = mock.Mock(return_value=fake_client)

    env = {"PURIQ_LLM_MODE": "bedrock", "AWS_REGION": None}
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()
        with mock.patch.dict(sys.modules, {"boto3": fake_boto3}):
            provider.complete("hola")

    fake_boto3.client.assert_called_once_with("bedrock-runtime")


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


# --- proveedor compatible con OpenAI / Azure (httpx mockeado) --------------
def test_get_provider_returns_openai_when_mode_openai():
    """`PURIQ_LLM_MODE=openai` -> OpenAICompatibleProvider (DD-4)."""
    env = {
        "PURIQ_LLM_MODE": "openai",
        "PURIQ_OPENAI_API_KEY": "sk-test-key",
        "PURIQ_OPENAI_BASE_URL": None,
        "PURIQ_OPENAI_MODEL": None,
        "PURIQ_OPENAI_API_VERSION": None,
    }
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()

    assert isinstance(provider, generate_content.OpenAICompatibleProvider)
    # Cumple el protocolo del adaptador de LLM.
    assert isinstance(provider, generate_content.LLMProvider)


def test_openai_provider_completes_vanilla_base_url():
    """Estilo OpenAI estandar: `Authorization: Bearer`, `model` en el cuerpo.

    POSTea a `<base>/chat/completions` y parsea `choices[0].message.content`.
    """
    env = {
        "PURIQ_LLM_MODE": "openai",
        "PURIQ_OPENAI_API_KEY": "sk-test-key",
        "PURIQ_OPENAI_BASE_URL": "https://api.groq.com/openai/v1",
        "PURIQ_OPENAI_MODEL": "llama-3.1-70b",
        "PURIQ_OPENAI_API_VERSION": None,
    }
    fake_httpx, _ = _make_fake_httpx(
        {"choices": [{"message": {"content": "  Hola mundo  "}}]}
    )

    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()
        assert not provider.is_azure
        with mock.patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = provider.complete("Describe un lugar")

    call = fake_httpx.post.call_args
    url = call.args[0] if call.args else call.kwargs["url"]
    headers = call.kwargs["headers"]
    body = call.kwargs["json"]

    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test-key"
    assert "api-key" not in headers
    assert body["model"] == "llama-3.1-70b"
    assert body["messages"] == [{"role": "user", "content": "Describe un lugar"}]
    assert body["max_tokens"] == generate_content._MAX_TOKENS
    # El proveedor normaliza (strip) el texto de la respuesta.
    assert result == "Hola mundo"


def test_openai_provider_completes_azure_base_url():
    """Estilo Azure (`azure.com`): cabecera `api-key` y deployment en la URL.

    POSTea a `.../openai/deployments/<deployment>/chat/completions?api-version=...`
    y NO incluye `model` en el cuerpo (viaja en la URL).
    """
    env = {
        "PURIQ_LLM_MODE": "openai",
        "PURIQ_OPENAI_API_KEY": "azure-secret",
        "PURIQ_OPENAI_BASE_URL": "https://myres.openai.azure.com/",
        "PURIQ_OPENAI_MODEL": "gpt-4.1-mini",
        "PURIQ_OPENAI_API_VERSION": "2024-10-21",
    }
    fake_httpx, _ = _make_fake_httpx(
        {"choices": [{"message": {"content": "Respuesta Azure"}}]}
    )

    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        provider = generate_content.get_provider()
        assert provider.is_azure
        with mock.patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = provider.complete("Traduce esto")

    call = fake_httpx.post.call_args
    url = call.args[0] if call.args else call.kwargs["url"]
    headers = call.kwargs["headers"]
    body = call.kwargs["json"]

    assert url == (
        "https://myres.openai.azure.com/openai/deployments/gpt-4.1-mini"
        "/chat/completions?api-version=2024-10-21"
    )
    assert headers["api-key"] == "azure-secret"
    assert "Authorization" not in headers
    # En Azure el modelo/deployment va en la URL, no en el cuerpo.
    assert "model" not in body
    assert body["messages"] == [{"role": "user", "content": "Traduce esto"}]
    assert result == "Respuesta Azure"


def test_openai_provider_missing_api_key_raises():
    """Sin `PURIQ_OPENAI_API_KEY` en modo openai -> MissingEnvVarError (Req 9.5)."""
    env = {"PURIQ_LLM_MODE": "openai", "PURIQ_OPENAI_API_KEY": None}
    with mock.patch.object(generate_content, "get_env", side_effect=_fake_get_env(env)):
        with pytest.raises(MissingEnvVarError) as excinfo:
            generate_content.get_provider()

    assert excinfo.value.name == "PURIQ_OPENAI_API_KEY"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
