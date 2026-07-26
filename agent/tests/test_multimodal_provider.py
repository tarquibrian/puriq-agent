"""Pruebas del provider multimodal (spec multimodal-ingest, Pieza 4 visión).

Cubre las tareas opcionales 4.2, 4.3, 4.6 y 4.7 del plan, sobre
`agent/puriq/tools/generate_content.py`:

  - **Property 7 (task 4.2):** sin imágenes, la traducción de cada proveedor
    (`_messages_to_claude` / `_messages_to_openai`) es idéntica a la text-only
    del Hito 2, y `complete(prompt)` conserva su firma/comportamiento
    (Validates: Requirements 4.3, 4.4).
  - **Property 11 (task 4.3):** un proveedor sin visión (`OllamaProvider` /
    el guard compartido `_guard_vision_support`) rechaza `complete_chat` con
    imágenes nombrando `PURIQ_LLM_MODE` y los modos con visión
    (Validates: Requirements 5.4).
  - **Property 8 (task 4.6):** la traducción multimodal transporta cada imagen
    (bloque nativo base64 + media type) junto con las tools traducidas
    (Validates: Requirements 4.1, 4.2, 4.5).
  - **Ejemplo/integración (task 4.7):** integración con MOCK del backend
    multimodal (Bedrock `invoke_model` / OpenAI `httpx.post`), regresión de
    `complete(prompt)` en los tres proveedores, `get_provider()` por
    `PURIQ_LLM_MODE` y `MissingEnvVarError` sin exponer la clave, sin llamar a
    servicios reales (Requirements 4.4, 5.1, 5.2, 5.3, 5.5).

Las pruebas de propiedad usan **Hypothesis** con un mínimo de 100 iteraciones.
Cada una lleva el comentario de trazabilidad
`# Feature: multimodal-ingest, Property {N}: {texto}`.
"""
from __future__ import annotations

import base64
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
    ImageContent,
    Message,
    OllamaProvider,
    OpenAICompatibleProvider,
    ToolCall,
    ToolResult,
    _guard_vision_support,
    _messages_have_images,
    _tools_to_bedrock,
    _tools_to_openai,
)

# Configuración común de PBT: >=100 iteraciones, sin deadline (trabajo en memoria).
pbt = settings(max_examples=100, deadline=None)


# --- Helpers de configuración/mocks (sin red ni servicios reales) ------------
# Se REPLICA el patrón de mocks del test del Hito 2 (test_web_chat_provider.py),
# no se importa: cada archivo de test es autónomo.
def _fake_get_env(values: dict[str, str | None]):
    """Sustituto de `config.get_env` que consulta `values` por nombre.

    Respeta el contrato: `required=True` con valor ausente/vacío lanza
    `MissingEnvVarError` nombrando la variable (Req 5.5).
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


# --- Traducciones de referencia text-only (comportamiento del Hito 2) --------
# Se reimplementan aquí, SIN manejar imágenes, para servir de oráculo de la
# Property 7: cuando los mensajes no tienen imágenes, la salida de los
# traductores de producción debe ser idéntica a esta referencia.
def _ref_messages_to_claude(messages: list[Message]) -> tuple[str, list[dict]]:
    """Referencia text-only del cuerpo Messages de Claude (Hito 2)."""
    system_parts: list[str] = []
    claude_messages: list[dict] = []
    for msg in messages:
        if msg.role == "system":
            if msg.content:
                system_parts.append(msg.content)
        elif msg.role == "user":
            claude_messages.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": msg.content or ""}],
                }
            )
        elif msg.role == "assistant":
            blocks: list[dict] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for call in msg.tool_calls or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            claude_messages.append({"role": "assistant", "content": blocks})
        elif msg.role == "tool":
            result = msg.tool_result
            claude_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": result.tool_call_id if result else "",
                            "content": result.content if result else "",
                        }
                    ],
                }
            )
    return "\n".join(system_parts), claude_messages


def _ref_messages_to_openai(messages: list[Message]) -> list[dict]:
    """Referencia text-only de la forma de mensajes de OpenAI (Hito 2)."""
    result: list[dict] = []
    for msg in messages:
        if msg.role == "system":
            result.append({"role": "system", "content": msg.content or ""})
        elif msg.role == "user":
            result.append({"role": "user", "content": msg.content or ""})
        elif msg.role == "assistant":
            entry: dict = {"role": "assistant", "content": msg.content}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in msg.tool_calls
                ]
            result.append(entry)
        elif msg.role == "tool":
            tr = msg.tool_result
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": tr.tool_call_id if tr else "",
                    "content": tr.content if tr else "",
                }
            )
    return result


# --- Estrategias reutilizables -----------------------------------------------
def _json_values():
    """Valores JSON-serializables que round-trip exacto por `json.dumps/loads`."""
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

# Media types raster soportados por la visión (DD-M7, _VISION_MEDIA_TYPES).
_media_type = st.sampled_from(["image/jpeg", "image/png", "image/webp", "image/gif"])

# Datos de imagen en base64 (bytes arbitrarios no vacíos codificados).
_image_data = st.builds(
    lambda raw: base64.b64encode(raw).decode("ascii"),
    st.binary(min_size=1, max_size=48),
)

_image = st.builds(ImageContent, media_type=_media_type, data=_image_data)


@st.composite
def _tool_call(draw) -> ToolCall:
    """Una `ToolCall` con id/name/arguments arbitrarios."""
    return ToolCall(
        id=draw(st.text(max_size=8)),
        name=draw(_tool_name),
        arguments=draw(_arguments),
    )


@st.composite
def _textonly_messages(draw) -> list[Message]:
    """Lista variada de `Message` SIN imágenes (system/user/assistant/tool)."""
    count = draw(st.integers(min_value=1, max_value=6))
    messages: list[Message] = []
    for _ in range(count):
        role = draw(st.sampled_from(["system", "user", "assistant", "tool"]))
        if role == "system":
            messages.append(Message(role="system", content=draw(st.text(max_size=30))))
        elif role == "user":
            messages.append(
                Message(
                    role="user",
                    content=draw(st.one_of(st.none(), st.text(max_size=30))),
                )
            )
        elif role == "assistant":
            calls = None
            if draw(st.booleans()):
                calls = draw(st.lists(_tool_call(), min_size=1, max_size=3))
            messages.append(
                Message(
                    role="assistant",
                    content=draw(st.one_of(st.none(), st.text(max_size=30))),
                    tool_calls=calls,
                )
            )
        else:  # tool
            messages.append(
                Message(
                    role="tool",
                    tool_result=ToolResult(
                        tool_call_id=draw(st.text(max_size=8)),
                        content=draw(st.text(max_size=20)),
                    ),
                )
            )
    return messages


@st.composite
def _messages_with_images(draw) -> list[Message]:
    """Lista de `Message` con al menos un `user` que trae >=1 imagen."""
    messages: list[Message] = [
        Message(role="system", content=draw(st.text(max_size=20)))
    ]
    n_users = draw(st.integers(min_value=1, max_value=3))
    for _ in range(n_users):
        images = draw(st.lists(_image, min_size=1, max_size=3))
        messages.append(
            Message(
                role="user",
                content=draw(st.one_of(st.none(), st.text(max_size=20))),
                images=images,
            )
        )
    return messages


@st.composite
def _spec_subset(draw) -> list[dict]:
    """Subconjunto (posiblemente vacío) de `INTAKE_TOOL_SPECS`, sin repetidos."""
    n = len(INTAKE_TOOL_SPECS)
    indices = draw(
        st.lists(st.integers(min_value=0, max_value=n - 1), unique=True, max_size=n)
    )
    return [INTAKE_TOOL_SPECS[i] for i in indices]


# =============================================================================
# Property 7 (task 4.2)
# =============================================================================
# Feature: multimodal-ingest, Property 7: Sin imágenes, complete_chat preserva el comportamiento text-only
@pbt
@given(messages=_textonly_messages())
def test_p7_textonly_translation_is_identical_to_hito2(messages):
    """Para toda lista de mensajes SIN imágenes, `_messages_to_claude` y
    `_messages_to_openai` producen exactamente la traducción text-only del
    Hito 2 (byte a byte con el oráculo de referencia).

    Validates: Requirements 4.3, 4.4
    """
    # Precondición: ningún mensaje trae imágenes (invariante del generador).
    assert not _messages_have_images(messages)

    # Bedrock/Claude: system concatenado + mensajes idénticos a la referencia.
    ref_system, ref_claude = _ref_messages_to_claude(messages)
    system, claude_messages = BedrockProvider._messages_to_claude(messages)
    assert system == ref_system
    assert claude_messages == ref_claude
    # Sin bloques de imagen en ninguna parte del contenido.
    for msg in claude_messages:
        for block in msg["content"]:
            assert block.get("type") != "image"

    # OpenAI-compatible: `content` de user/system sigue siendo string plano.
    ref_openai = _ref_messages_to_openai(messages)
    openai_messages = OpenAICompatibleProvider._messages_to_openai(messages)
    assert openai_messages == ref_openai
    for msg in openai_messages:
        if msg["role"] in {"system", "user"}:
            assert isinstance(msg["content"], (str, type(None)))


# Feature: multimodal-ingest, Property 7: Sin imágenes, complete_chat preserva el comportamiento text-only
@pbt
@given(prompt=st.text(max_size=40), reply=st.text(max_size=40))
def test_p7_complete_prompt_preserves_signature_and_behavior(prompt, reply):
    """`complete(prompt)` conserva su firma text-only y su comportamiento: envía
    el prompt tal cual y devuelve el texto del modelo (Req 4.4). Sin servicios
    reales (mock de `invoke_model`).
    """
    provider = BedrockProvider(model_id="anthropic.test")
    provider._client = _fake_bedrock_client(
        [{"content": [{"type": "text", "text": reply}]}]
    )
    result = provider.complete(prompt)
    assert result == reply.strip()
    body = json.loads(provider._client.invoke_model.call_args.kwargs["body"])
    # El prompt viaja como único bloque de texto text-only, sin imágenes.
    user_content = body["messages"][0]["content"]
    assert user_content == [{"type": "text", "text": prompt}]


# =============================================================================
# Property 11 (task 4.3)
# =============================================================================
# Feature: multimodal-ingest, Property 11: Un proveedor sin visión rechaza las imágenes nombrando PURIQ_LLM_MODE
@pbt
@given(messages=_messages_with_images())
def test_p11_provider_without_vision_rejects_images(messages):
    """Para toda lista con >=1 imagen, un proveedor sin visión (Ollama) y el
    guard compartido rechazan `complete_chat` con un `RuntimeError` que nombra
    `PURIQ_LLM_MODE` y los modos con visión (`bedrock`, `openai`).

    Validates: Requirements 5.4
    """
    provider = OllamaProvider(model="llama-test")
    assert provider.supports_vision is False

    with pytest.raises(RuntimeError) as excinfo:
        provider.complete_chat(messages)
    message = str(excinfo.value)
    assert "PURIQ_LLM_MODE" in message
    assert "bedrock" in message
    assert "openai" in message

    # El guard compartido (defensa en profundidad) rechaza igual y nombra la var.
    with pytest.raises(RuntimeError) as guard_exc:
        _guard_vision_support(messages, supports_vision=False)
    assert "PURIQ_LLM_MODE" in str(guard_exc.value)

    # Con visión, el guard no interfiere (no lanza).
    _guard_vision_support(messages, supports_vision=True)


# =============================================================================
# Property 8 (task 4.6)
# =============================================================================
# Feature: multimodal-ingest, Property 8: La traducción multimodal transporta cada imagen junto con las tools
@pbt
@given(messages=_messages_with_images(), specs=_spec_subset())
def test_p8_multimodal_translation_carries_images_and_tools(messages, specs):
    """Para toda lista con imágenes en `user` y cualquier subconjunto de tools,
    la traducción de cada proveedor incluye por imagen su bloque nativo (base64
    + media type) y además incluye todas las tools traducidas.

    Validates: Requirements 4.1, 4.2, 4.5
    """
    users_in = [m for m in messages if m.role == "user"]

    # --- Bedrock/Claude: bloque {"type":"image","source":{base64,media_type,data}}.
    _system, claude_messages = BedrockProvider._messages_to_claude(messages)
    users_out = [m for m in claude_messages if m["role"] == "user"]
    assert len(users_out) == len(users_in)
    for m_in, m_out in zip(users_in, users_out):
        blocks = m_out["content"]
        # Coexiste el bloque de texto con los bloques de imagen (Req 4.5).
        assert any(b.get("type") == "text" for b in blocks)
        image_blocks = [b for b in blocks if b.get("type") == "image"]
        assert len(image_blocks) == len(m_in.images)
        for image, block in zip(m_in.images, image_blocks):
            assert block["source"] == {
                "type": "base64",
                "media_type": image.media_type,
                "data": image.data,
            }

    # --- OpenAI-compatible: parte {"type":"image_url","image_url":{"url":data:..}}.
    openai_messages = OpenAICompatibleProvider._messages_to_openai(messages)
    users_openai = [m for m in openai_messages if m["role"] == "user"]
    assert len(users_openai) == len(users_in)
    for m_in, m_out in zip(users_in, users_openai):
        parts = m_out["content"]
        assert isinstance(parts, list)
        assert any(p.get("type") == "text" for p in parts)
        image_parts = [p for p in parts if p.get("type") == "image_url"]
        assert len(image_parts) == len(m_in.images)
        for image, part in zip(m_in.images, image_parts):
            assert part["image_url"]["url"] == (
                f"data:{image.media_type};base64,{image.data}"
            )

    # --- Las tools se traducen íntegras en ambos proveedores (Req 4.5).
    bedrock_tools = _tools_to_bedrock(specs)
    openai_tools = _tools_to_openai(specs)
    expected_names = {spec["name"] for spec in specs}
    assert len(bedrock_tools) == len(specs)
    assert len(openai_tools) == len(specs)
    assert {t["name"] for t in bedrock_tools} == expected_names
    assert {t["function"]["name"] for t in openai_tools} == expected_names


# =============================================================================
# Ejemplo / integración (task 4.7)
# =============================================================================
# --- supports_vision por proveedor (5.2, 5.3, 5.4) ---------------------------
def test_supports_vision_flag_per_provider():
    """Bedrock y OpenAI-compatible soportan visión; Ollama no (Req 5.2–5.4)."""
    assert BedrockProvider(model_id="anthropic.test").supports_vision is True
    assert OllamaProvider(model="llama-test").supports_vision is False
    env = {"PURIQ_OPENAI_API_KEY": "sk-test"}
    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        assert OpenAICompatibleProvider().supports_vision is True


# --- Integración con MOCK: Bedrock multimodal (5.2) --------------------------
def test_bedrock_complete_chat_multimodal_body_has_image_and_tools():
    """Bedrock: el cuerpo Claude enviado a `invoke_model` incluye un bloque
    `image` con `source.base64` + `media_type` y coexiste con `tools` (Req 5.2).
    Sin servicios reales.
    """
    provider = BedrockProvider(model_id="anthropic.test")
    provider._client = _fake_bedrock_client(
        [{"stop_reason": "end_turn", "content": [{"type": "text", "text": "Es un cerro."}]}]
    )
    image = ImageContent(media_type="image/jpeg", data="QUJDRA==")
    tools = [
        spec for spec in INTAKE_TOOL_SPECS if spec["name"] in {"add_place", "edit_item"}
    ]
    messages = [
        Message(role="user", content="Describí esta foto", images=[image]),
    ]

    result = provider.complete_chat(messages, tools=tools)
    assert result.text == "Es un cerro."

    body = json.loads(provider._client.invoke_model.call_args.kwargs["body"])
    user_blocks = body["messages"][0]["content"]
    image_blocks = [b for b in user_blocks if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"] == {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": "QUJDRA==",
    }
    # La imagen coexiste con el bloque de texto y con las tools traducidas.
    assert any(b.get("type") == "text" for b in user_blocks)
    assert {t["name"] for t in body["tools"]} == {"add_place", "edit_item"}
    assert body["tool_choice"] == {"type": "auto"}
    # Las tools no exponen `project` al modelo (DD-2).
    for tool in body["tools"]:
        assert "project" not in tool["input_schema"].get("properties", {})


# --- Integración con MOCK: OpenAI vision (5.3) -------------------------------
def test_openai_complete_chat_multimodal_body_has_image_url_and_endpoint():
    """OpenAI-compatible: el `content` del user viaja como partes con una
    `image_url` (`data:<media_type>;base64,<data>`) y se usa el endpoint
    `.../chat/completions` (Req 5.3). Sin servicios reales.
    """
    env = {
        "PURIQ_OPENAI_API_KEY": "sk-test",
        "PURIQ_OPENAI_BASE_URL": "https://api.openai.com/v1",
        "PURIQ_OPENAI_MODEL": "gpt-4o-mini",
        "PURIQ_OPENAI_API_VERSION": None,
    }
    fake_httpx = _make_fake_httpx(
        [{"choices": [{"message": {"content": "Es un cerro."}}]}]
    )
    image = ImageContent(media_type="image/png", data="QUJDRA==")
    tools = [spec for spec in INTAKE_TOOL_SPECS if spec["name"] == "add_place"]

    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        provider = OpenAICompatibleProvider()
        assert not provider.is_azure
        with mock.patch.dict(sys.modules, {"httpx": fake_httpx}):
            result = provider.complete_chat(
                [Message(role="user", content="describe", images=[image])],
                tools=tools,
            )

    assert result.text == "Es un cerro."

    call = fake_httpx.post.call_args
    url = call.args[0] if call.args else call.kwargs["url"]
    assert url == "https://api.openai.com/v1/chat/completions"

    body = call.kwargs["json"]
    parts = body["messages"][0]["content"]
    assert isinstance(parts, list)
    image_parts = [p for p in parts if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == "data:image/png;base64,QUJDRA=="
    assert any(p.get("type") == "text" for p in parts)
    # Coexiste con las tools de function calling.
    assert {t["function"]["name"] for t in body["tools"]} == {"add_place"}


# --- Regresión: complete(prompt) sin cambios en los tres providers (4.4) -----
def test_regression_bedrock_complete_text_still_works():
    """`BedrockProvider.complete(prompt)` sigue devolviendo el texto (Req 4.4)."""
    provider = BedrockProvider(model_id="anthropic.test")
    provider._client = _fake_bedrock_client(
        [{"content": [{"type": "text", "text": "Hola turista"}]}]
    )
    result = provider.complete("Describe un lugar")
    assert result == "Hola turista"
    body = json.loads(provider._client.invoke_model.call_args.kwargs["body"])
    assert body["messages"][0]["content"][0]["text"] == "Describe un lugar"


def test_regression_openai_complete_text_still_works():
    """`OpenAICompatibleProvider.complete(prompt)` sigue funcionando (Req 4.4)."""
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
    """`OllamaProvider.complete(prompt)` sigue funcionando (Req 4.4)."""
    fake_ollama = types.ModuleType("ollama")
    fake_ollama.generate = mock.Mock(return_value={"response": "  local  "})
    provider = OllamaProvider(model="llama3.2")
    with mock.patch.dict(sys.modules, {"ollama": fake_ollama}):
        result = provider.complete("traduce")
    assert result == "local"
    fake_ollama.generate.assert_called_once_with(model="llama3.2", prompt="traduce")


# --- get_provider() resuelve por PURIQ_LLM_MODE (5.1) ------------------------
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
    """`get_provider()` resuelve el backend según `PURIQ_LLM_MODE` (Req 5.1)."""
    env = {"PURIQ_LLM_MODE": mode, **extra_env}
    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        provider = generate_content.get_provider()
    assert isinstance(provider, expected_cls)
    assert isinstance(provider, generate_content.LLMProvider)


# --- Sin PURIQ_OPENAI_API_KEY -> MissingEnvVarError que la nombra (5.5) ------
def test_openai_provider_missing_api_key_raises_without_exposing_value():
    """Construir `OpenAICompatibleProvider` sin la clave lanza `MissingEnvVarError`
    que nombra la variable sin exponer su valor (Req 5.5)."""
    env = {"PURIQ_OPENAI_API_KEY": None}
    with mock.patch.object(
        generate_content, "get_env", side_effect=_fake_get_env(env)
    ):
        with pytest.raises(MissingEnvVarError) as excinfo:
            OpenAICompatibleProvider()
    assert excinfo.value.name == "PURIQ_OPENAI_API_KEY"
    assert "PURIQ_OPENAI_API_KEY" in str(excinfo.value)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
