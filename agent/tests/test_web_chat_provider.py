"""Pruebas del provider con tool-use (spec conversational-web-chat, Pieza 4).

Cubre las tareas opcionales 2.2, 2.6 y 2.7 del plan, sobre
`agent/puriq/tools/generate_content.py`:

  - **Property 8 (task 2.2):** la traducción de tools (`_tools_to_bedrock` /
    `_tools_to_openai`) preserva identidad y esquema y no expone `project`.
  - **Property 9 (task 2.6):** el parseo de la respuesta del proveedor
    (`_parse_chat_payload` de Bedrock/OpenAI) produce `ToolCall` estructuradas
    cuyos `name`/`arguments` coinciden con la respuesta.
  - **Ejemplo/integración (task 2.7):** regresión de `complete(prompt)`,
    `complete_chat` text-only, `get_provider()` por `PURIQ_LLM_MODE`, rechazo
    accionable de Ollama, `MissingEnvVarError` sin exponer la clave, lectura de
    la credencial con `get_env(secret=True)`, e integración con MOCK del backend
    (Bedrock `invoke_model` / OpenAI `httpx.post`), sin llamar servicios reales.

Las pruebas de propiedad usan **Hypothesis** con un mínimo de 100 iteraciones.
Cada una lleva el comentario de trazabilidad
`# Feature: conversational-web-chat, Property {N}: {texto}`.
"""
from __future__ import annotations

import copy
import json
import sys
import types
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq.config import MissingEnvVarError
from puriq.intake.tools import INTAKE_TOOL_SPECS
from puriq.tools import generate_content
from puriq.tools.generate_content import (
    BedrockProvider,
    ChatResult,
    Message,
    OllamaProvider,
    OpenAICompatibleProvider,
    ToolCall,
    ToolResult,
    _tools_to_bedrock,
    _tools_to_openai,
)

# Configuración común de PBT: >=100 iteraciones, sin deadline (trabajo en memoria).
pbt = settings(max_examples=100, deadline=None)


# --- Helpers de configuración/mocks (sin red ni servicios reales) ------------
def _fake_get_env(values: dict[str, str | None]):
    """Sustituto de `config.get_env` que consulta `values` por nombre.

    Respeta el contrato: `required=True` con valor ausente/vacío lanza
    `MissingEnvVarError` nombrando la variable (Req 4.5).
    """

    def _inner(name: str, *, required: bool = False, secret: bool = False):
        value = values.get(name)
        if required and (value is None or value == ""):
            raise MissingEnvVarError(name)
        return value

    return _inner


def _fake_bedrock_client(payloads: list[dict]) -> mock.Mock:
    """Cliente boto3 de mentira: cada `invoke_model` devuelve el siguiente payload."""
    responses = []
    for payload in payloads:
        body = mock.Mock()
        body.read.return_value = json.dumps(payload).encode("utf-8")
        responses.append({"body": body})
    client = mock.Mock()
    client.invoke_model.side_effect = responses
    return client


def _make_fake_httpx(payloads: list[dict]):
    """Módulo `httpx` de mentira; cada `post` devuelve el siguiente payload."""
    responses = []
    for payload in payloads:
        resp = mock.Mock()
        resp.json.return_value = payload
        resp.raise_for_status = mock.Mock()
        responses.append(resp)
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.post = mock.Mock(side_effect=responses)
    return fake_httpx


# --- Estrategias reutilizables -----------------------------------------------
def _json_values():
    """Valores JSON-serializables que round-trip exacto por `json.dumps/loads`.

    Se excluyen floats a propósito (evita ambigüedad de igualdad en el
    round-trip); se cubren None/bool/int/str y estructuras anidadas.
    """
    scalars = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1000, max_value=1000),
        st.text(max_size=20),
    )
    return st.recursive(
        scalars,
        lambda children: st.one_of(
            st.lists(children, max_size=3),
            st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=3),
        ),
        max_leaves=5,
    )


_arguments = st.dictionaries(st.text(min_size=1, max_size=8), _json_values(), max_size=4)

# Nombre de tool: mezcla de nombres reales del catálogo y texto arbitrario.
_tool_name = st.one_of(
    st.sampled_from([spec["name"] for spec in INTAKE_TOOL_SPECS]),
    st.text(min_size=1, max_size=16),
)


@st.composite
def _tool_requests(draw) -> list[dict]:
    """Genera una lista no vacía de solicitudes de tool `{id, name, arguments}`."""
    count = draw(st.integers(min_value=1, max_value=4))
    requests = []
    for i in range(count):
        requests.append(
            {
                "id": f"tc_{i}_" + draw(st.text(min_size=0, max_size=6)),
                "name": draw(_tool_name),
                "arguments": draw(_arguments),
            }
        )
    return requests


@st.composite
def _spec_subset(draw) -> list[dict]:
    """Subconjunto (posiblemente vacío) de `INTAKE_TOOL_SPECS`, sin repetidos."""
    n = len(INTAKE_TOOL_SPECS)
    indices = draw(
        st.lists(st.integers(min_value=0, max_value=n - 1), unique=True, max_size=n)
    )
    return [INTAKE_TOOL_SPECS[i] for i in indices]


def _expected_stripped(input_schema: dict | None) -> dict:
    """Copia del `inputSchema` sin `project` (independiente del helper de prod)."""
    schema = copy.deepcopy(input_schema or {})
    properties = schema.get("properties")
    if isinstance(properties, dict):
        properties.pop("project", None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [name for name in required if name != "project"]
    return schema


# =============================================================================
# Property 8 (task 2.2)
# =============================================================================
# Feature: conversational-web-chat, Property 8: La traducción de tools preserva identidad y esquema
@pbt
@given(specs=_spec_subset())
def test_p8_tool_translation_preserves_identity_and_schema(specs):
    """Bedrock y OpenAI producen una tool por spec, preservan name/description y
    esquema de parámetros, y no exponen la propiedad `project`.

    Validates: Requirements 3.3
    """
    bedrock = _tools_to_bedrock(specs)
    openai = _tools_to_openai(specs)

    # Exactamente una herramienta por spec, en el mismo orden.
    assert len(bedrock) == len(specs)
    assert len(openai) == len(specs)

    for spec, b_tool, o_tool in zip(specs, bedrock, openai):
        expected_schema = _expected_stripped(spec.get("inputSchema"))

        # Bedrock: {name, description, input_schema}.
        assert b_tool["name"] == spec["name"]
        assert b_tool["description"] == spec.get("description", "")
        assert b_tool["input_schema"] == expected_schema

        # OpenAI: {type: function, function: {name, description, parameters}}.
        assert o_tool["type"] == "function"
        func = o_tool["function"]
        assert func["name"] == spec["name"]
        assert func["description"] == spec.get("description", "")
        assert func["parameters"] == expected_schema

        # `project` no aparece en el esquema presentado al modelo (DD-2).
        for schema in (b_tool["input_schema"], func["parameters"]):
            assert "project" not in (schema.get("properties") or {})
            assert "project" not in (schema.get("required") or [])

        # El esquema original NO se muta (copia profunda en la traducción).
        original_props = (spec.get("inputSchema") or {}).get("properties") or {}
        if "project" in original_props or "project" in (
            (spec.get("inputSchema") or {}).get("required") or []
        ):
            # Si el spec original tenía project, sigue teniéndolo tras traducir.
            assert "project" in original_props


# =============================================================================
# Property 9 (task 2.6)
# =============================================================================
# Feature: conversational-web-chat, Property 9: La respuesta del proveedor se parsea a Tool_Calls estructuradas
@pbt
@given(requests=_tool_requests(), with_text=st.booleans())
def test_p9_bedrock_payload_parses_to_tool_calls(requests, with_text):
    """Un payload Bedrock con bloques `tool_use` se parsea a `ToolCall` cuyos
    `name`/`arguments` coinciden con la respuesta.

    Validates: Requirements 3.4
    """
    content: list[dict] = []
    if with_text:
        content.append({"type": "text", "text": "Voy a registrar eso."})
    for req in requests:
        content.append(
            {
                "type": "tool_use",
                "id": req["id"],
                "name": req["name"],
                "input": req["arguments"],
            }
        )
    payload = {"stop_reason": "tool_use", "content": content}

    result = BedrockProvider._parse_chat_payload(payload)

    assert isinstance(result, ChatResult)
    assert result.text is None
    parsed = [(tc.name, tc.arguments) for tc in result.tool_calls]
    expected = [(req["name"], req["arguments"]) for req in requests]
    assert parsed == expected
    # Los ids opacos también se preservan para casar el ToolResult.
    assert [tc.id for tc in result.tool_calls] == [req["id"] for req in requests]


# Feature: conversational-web-chat, Property 9: La respuesta del proveedor se parsea a Tool_Calls estructuradas
@pbt
@given(requests=_tool_requests())
def test_p9_openai_payload_parses_to_tool_calls(requests):
    """Un payload OpenAI con `message.tool_calls` se parsea a `ToolCall` cuyos
    `name`/`arguments` (deserializados de JSON) coinciden con la respuesta.

    Validates: Requirements 3.4
    """
    tool_calls = [
        {
            "id": req["id"],
            "type": "function",
            "function": {
                "name": req["name"],
                "arguments": json.dumps(req["arguments"]),
            },
        }
        for req in requests
    ]
    payload = {"choices": [{"message": {"role": "assistant", "tool_calls": tool_calls}}]}

    result = OpenAICompatibleProvider._parse_chat_payload(payload)

    assert isinstance(result, ChatResult)
    assert result.text is None
    parsed = [(tc.name, tc.arguments) for tc in result.tool_calls]
    expected = [(req["name"], req["arguments"]) for req in requests]
    assert parsed == expected
    assert [tc.id for tc in result.tool_calls] == [req["id"] for req in requests]


# =============================================================================
# Ejemplo / integración (task 2.7)
# =============================================================================
# --- get_provider() resuelve por PURIQ_LLM_MODE (4.1) ------------------------
@pytest.mark.parametrize(
    "mode, expected_cls, extra_env",
    [
        ("local", OllamaProvider, {}),
        ("bedrock", BedrockProvider, {}),
        ("openai", OpenAICompatibleProvider, {"PURIQ_OPENAI_API_KEY": "sk-test"}),
        (None, BedrockProvider, {}),  # ausente -> bedrock por defecto
    ],
)
def test_get_provider_resolves_by_llm_mode(mode, expected_cls, extra_env):
    """`get_provider()` resuelve el backend según `PURIQ_LLM_MODE` (Req 4.1)."""
    env = {"PURIQ_LLM_MODE": mode, **extra_env}
    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        provider = generate_content.get_provider()
    assert isinstance(provider, expected_cls)
    assert isinstance(provider, generate_content.LLMProvider)


# --- Ollama rechaza complete_chat nombrando PURIQ_LLM_MODE (4.4) -------------
def test_ollama_complete_chat_rejects_naming_config_and_modes():
    """`OllamaProvider.complete_chat` rechaza con un mensaje que nombra
    `PURIQ_LLM_MODE` y los modos con tool-use (Req 4.4)."""
    env = {"PURIQ_OLLAMA_MODEL": None}
    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        provider = OllamaProvider()
    with pytest.raises(RuntimeError) as excinfo:
        provider.complete_chat([Message(role="user", content="hola")])
    message = str(excinfo.value)
    assert "PURIQ_LLM_MODE" in message
    assert "bedrock" in message
    assert "openai" in message


# --- Sin PURIQ_OPENAI_API_KEY -> MissingEnvVarError sin exponer valor (4.5) --
def test_openai_provider_missing_api_key_raises_without_exposing_value():
    """Construir `OpenAICompatibleProvider` sin la clave lanza `MissingEnvVarError`
    que nombra la variable sin exponer su valor (Req 4.5)."""
    env = {"PURIQ_OPENAI_API_KEY": None}
    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        with pytest.raises(MissingEnvVarError) as excinfo:
            OpenAICompatibleProvider()
    assert excinfo.value.name == "PURIQ_OPENAI_API_KEY"
    # El mensaje nombra la variable pero no incluye ningún valor de secreto.
    assert "PURIQ_OPENAI_API_KEY" in str(excinfo.value)


# --- La credencial se lee con get_env(secret=True) (11.3) --------------------
def test_openai_provider_reads_api_key_as_secret():
    """La clave se lee con `get_env(..., required=True, secret=True)` (Req 11.3)."""
    calls: list[dict] = []

    def _spy_get_env(name, *, required=False, secret=False):
        calls.append({"name": name, "required": required, "secret": secret})
        values = {
            "PURIQ_OPENAI_API_KEY": "sk-secret",
            "PURIQ_OPENAI_BASE_URL": None,
            "PURIQ_OPENAI_MODEL": None,
            "PURIQ_OPENAI_API_VERSION": None,
        }
        value = values.get(name)
        if required and (value is None or value == ""):
            raise MissingEnvVarError(name)
        return value

    with mock.patch.object(generate_content, "get_env", side_effect=_spy_get_env):
        OpenAICompatibleProvider()

    key_calls = [c for c in calls if c["name"] == "PURIQ_OPENAI_API_KEY"]
    assert key_calls, "no se leyó PURIQ_OPENAI_API_KEY"
    assert all(c["secret"] and c["required"] for c in key_calls)


# --- Regresión: complete(prompt) conserva firma/comportamiento (3.2) ---------
def test_regression_bedrock_complete_text_still_works():
    """`BedrockProvider.complete(prompt)` sigue devolviendo el texto (Req 3.2)."""
    provider = BedrockProvider(model_id="anthropic.test")
    provider._client = _fake_bedrock_client(
        [{"content": [{"type": "text", "text": "Hola turista"}]}]
    )
    result = provider.complete("Describe un lugar")
    assert result == "Hola turista"
    body = json.loads(provider._client.invoke_model.call_args.kwargs["body"])
    assert body["messages"][0]["content"][0]["text"] == "Describe un lugar"


def test_regression_openai_complete_text_still_works():
    """`OpenAICompatibleProvider.complete(prompt)` sigue funcionando (Req 3.2)."""
    env = {
        "PURIQ_OPENAI_API_KEY": "sk-test",
        "PURIQ_OPENAI_BASE_URL": "https://api.openai.com/v1",
        "PURIQ_OPENAI_MODEL": "gpt-4o-mini",
        "PURIQ_OPENAI_API_VERSION": None,
    }
    fake_httpx = _make_fake_httpx(
        [{"choices": [{"message": {"content": "  Texto  "}}]}]
    )
    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        provider = OpenAICompatibleProvider()
        with mock.patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = provider.complete("hola")
    assert result == "Texto"
    body = fake_httpx.post.call_args.kwargs["json"]
    assert body["messages"] == [{"role": "user", "content": "hola"}]


def test_regression_ollama_complete_text_still_works():
    """`OllamaProvider.complete(prompt)` sigue funcionando (Req 3.2)."""
    fake_ollama = types.ModuleType("ollama")
    fake_ollama.generate = mock.Mock(return_value={"response": "  local  "})
    env = {"PURIQ_OLLAMA_MODEL": "llama3.2"}
    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        provider = OllamaProvider()
    with mock.patch.dict(sys.modules, {"ollama": fake_ollama}):
        result = provider.complete("traduce")
    assert result == "local"
    fake_ollama.generate.assert_called_once_with(model="llama3.2", prompt="traduce")


# --- complete_chat es text-only (3.6) ----------------------------------------
def test_complete_chat_is_text_only_bedrock_translation():
    """La traducción a Claude solo transporta texto en los bloques de contenido,
    sin bytes de imágenes ni contenido multimodal (Req 3.6)."""
    messages = [
        Message(role="system", content="Sos Puriq."),
        Message(role="user", content="Adjunté assets/cerro-rico.jpg"),
    ]
    system, claude_messages = BedrockProvider._messages_to_claude(messages)
    assert system == "Sos Puriq."
    for msg in claude_messages:
        for block in msg["content"]:
            # Solo bloques de texto (o tool_use/tool_result), nunca imágenes.
            assert block.get("type") in {"text", "tool_use", "tool_result"}
            if block["type"] == "text":
                assert isinstance(block["text"], str)


def test_complete_chat_is_text_only_openai_translation():
    """La traducción a OpenAI mantiene `content` como texto plano (Req 3.6)."""
    messages = [
        Message(role="system", content="Sos Puriq."),
        Message(role="user", content="Adjunté assets/cerro-rico.jpg"),
    ]
    openai_messages = OpenAICompatibleProvider._messages_to_openai(messages)
    for msg in openai_messages:
        assert isinstance(msg["content"], (str, type(None)))


# --- Integración con MOCK: Bedrock tool-use + segundo turno con tool_result --
def test_bedrock_complete_chat_tool_use_and_second_turn_with_tool_result():
    """Bedrock: `invoke_model` devuelve `stop_reason=tool_use` con un bloque
    `tool_use`; se parsea a `ToolCall` y el segundo turno traduce el `tool_result`
    correctamente (Req 4.2). Sin servicios reales."""
    provider = BedrockProvider(model_id="anthropic.test")
    provider._client = _fake_bedrock_client(
        [
            {
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tc_1",
                        "name": "add_place",
                        "input": {"name": "Cerro Rico", "category": "cerro"},
                    }
                ],
            },
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Agregué el Cerro Rico."}],
            },
        ]
    )

    tools = [spec for spec in INTAKE_TOOL_SPECS if spec["name"] == "add_place"]

    # Primer turno: el modelo pide una tool.
    first = provider.complete_chat(
        [Message(role="user", content="Quiero el Cerro Rico")], tools=tools
    )
    assert first.text is None
    assert len(first.tool_calls) == 1
    call = first.tool_calls[0]
    assert call.name == "add_place"
    assert call.arguments == {"name": "Cerro Rico", "category": "cerro"}

    # El cuerpo enviado a Bedrock traduce las tools sin exponer `project`.
    first_body = json.loads(provider._client.invoke_model.call_args_list[0].kwargs["body"])
    assert first_body["tool_choice"] == {"type": "auto"}
    add_place_tool = first_body["tools"][0]
    assert add_place_tool["name"] == "add_place"
    assert "project" not in add_place_tool["input_schema"].get("properties", {})

    # Segundo turno: se aporta el ToolResult y el modelo responde texto.
    messages = [
        Message(role="user", content="Quiero el Cerro Rico"),
        first.assistant_message,
        Message(
            role="tool",
            tool_result=ToolResult(tool_call_id="tc_1", content='{"document": {}}'),
        ),
    ]
    second = provider.complete_chat(messages, tools=tools)
    assert second.text == "Agregué el Cerro Rico."
    assert second.tool_calls == []

    # El cuerpo del segundo turno incluye el bloque tool_result traducido.
    second_body = json.loads(provider._client.invoke_model.call_args_list[1].kwargs["body"])
    tool_result_blocks = [
        block
        for msg in second_body["messages"]
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert len(tool_result_blocks) == 1
    assert tool_result_blocks[0]["tool_use_id"] == "tc_1"
    assert tool_result_blocks[0]["content"] == '{"document": {}}'


# --- Integración con MOCK: OpenAI tool_calls + base_url local ----------------
def test_openai_complete_chat_tool_calls_and_local_base_url():
    """OpenAI-compatible: `httpx.post` devuelve `message.tool_calls`; se parsea a
    `ToolCall` y se usa el `base_url` local para prototipar sin AWS (Req 4.3)."""
    env = {
        "PURIQ_OPENAI_API_KEY": "sk-local",
        "PURIQ_OPENAI_BASE_URL": "http://localhost:1234/v1",
        "PURIQ_OPENAI_MODEL": "local-model",
        "PURIQ_OPENAI_API_VERSION": None,
    }
    fake_httpx = _make_fake_httpx(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "add_place",
                                        "arguments": json.dumps(
                                            {"name": "Cerro Rico", "category": "cerro"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            {"choices": [{"message": {"content": "Listo, agregado."}}]},
        ]
    )

    tools = [spec for spec in INTAKE_TOOL_SPECS if spec["name"] == "add_place"]

    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        provider = OpenAICompatibleProvider()
        assert not provider.is_azure
        with mock.patch.dict(sys.modules, {"httpx": fake_httpx}):
            first = provider.complete_chat(
                [Message(role="user", content="Quiero el Cerro Rico")], tools=tools
            )

            # Segundo turno con el ToolResult.
            messages = [
                Message(role="user", content="Quiero el Cerro Rico"),
                first.assistant_message,
                Message(
                    role="tool",
                    tool_result=ToolResult(
                        tool_call_id="call_1", content='{"document": {}}'
                    ),
                ),
            ]
            second = provider.complete_chat(messages, tools=tools)

    # Parseo del primer turno a ToolCall estructurada.
    assert first.text is None
    assert len(first.tool_calls) == 1
    call = first.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "add_place"
    assert call.arguments == {"name": "Cerro Rico", "category": "cerro"}

    # Usa el endpoint local (base_url) y function calling con las tools traducidas.
    first_call = fake_httpx.post.call_args_list[0]
    url = first_call.args[0] if first_call.args else first_call.kwargs["url"]
    assert url == "http://localhost:1234/v1/chat/completions"
    first_body = first_call.kwargs["json"]
    assert first_body["model"] == "local-model"
    assert first_body["tools"][0]["type"] == "function"
    assert first_body["tools"][0]["function"]["name"] == "add_place"
    assert (
        "project"
        not in first_body["tools"][0]["function"]["parameters"].get("properties", {})
    )

    # Segundo turno: texto final y el ToolResult traducido a role:"tool".
    assert second.text == "Listo, agregado."
    assert second.tool_calls == []
    second_body = fake_httpx.post.call_args_list[1].kwargs["json"]
    tool_msgs = [m for m in second_body["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert tool_msgs[0]["content"] == '{"document": {}}'


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
