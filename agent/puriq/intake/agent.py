"""intake/agent.py: bucle conversacional por turno (Pieza 3, Chat_Agent, DD-1/DD-2).

Este módulo implementa la **superficie B** (web) del intake cuando Puriq trae su
propio LLM: un bucle por turno que arma el contexto (system prompt + estado +
historial), invoca `complete_chat` con las intake tools, despacha las tool-calls
por `run_intake_tool` **inyectando `project`**, respeta un límite finito de rondas
y devuelve `{respuesta, estado}`.

Principio rector (DD-1): el Chat_Agent es una **superficie, no una
reimplementación**. Despacha TODA tool-call por `run_intake_tool` del núcleo
(Req 5.1) y toma el estado de `get_state` (Req 1.5, 5.5); no conoce la lógica de
ninguna intake tool ni valida el contrato: hereda validación, atomicidad,
integridad referencial y traducción de errores del Hito 1.

Inyección de `project` (DD-2, Req 1.8): el LLM no ve `project` (se le quita del
esquema en `complete_chat`); el Chat_Agent lo **inyecta** en los `arguments` de
cada tool-call antes de despachar. En el historial persistido, `project` NO se
guarda en los `arguments` de las tool-calls, para no filtrar rutas locales.

Archivos como referencias textuales (DD-7, Req 8.2, 8.3): `request.archivos` son
rutas relativas bajo `assets/` ya subidas; se insertan como **texto** en el
mensaje de usuario del turno, nunca como bytes.

Sesión (DD-6, Req 9, 10): la sesión da **continuidad** (historial + fase); el
contrato en disco es la fuente de verdad. Los faltantes salen del contrato
(`get_state`), nunca del historial (Req 10.3).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puriq import schemas
from puriq.intake.ingest import IncomingFile, prepare_incoming
from puriq.intake.prompt import build_system_prompt
from puriq.intake.session import load_session, save_session
from puriq.intake.tools import (
    INTAKE_TOOL_SPECS,
    get_state,
    run_intake_tool,
)
from puriq.tools.generate_content import (
    ChatResult,  # noqa: F401 - documentado como tipo devuelto por complete_chat
    ImageContent,
    LLMProvider,
    Message,
    ToolCall,
    ToolResult,
    get_provider,
)
from puriq.wizard.assets import IMAGE_EXTS, normalize_asset_name

#: Máximo de rondas de ejecución de tool-call por turno (Req 1.6). Un valor finito
#: acota el bucle de tool-use aunque el modelo insista en pedir herramientas.
DEFAULT_MAX_TOOL_ROUNDS = 8

#: Mensaje con que se cierra un turno que agotó `max_tool_rounds` (Req 1.7).
_LIMIT_MESSAGE = (
    "Se alcanzó el límite de acciones para este turno. Guardé lo que pude "
    "registrar hasta ahora; continuemos en el siguiente mensaje."
)


@dataclass
class ChatRequest:
    """Entrada de un turno del canal web (Req 6.1, 6.2, 8.1).

    Attributes:
        mensaje: texto del usuario para este turno.
        archivos: referencias a assets/ ya subidos (text-only, no binarios).
        binarios: Archivos_Entrantes reales (imágenes/PDF) del turno para la
            ingesta multimodal (Req 6.1, DD-M4/M5). Vacío en el camino JSON del
            Hito 2 (solo el canal multipart lo llena, DD-M8).
    """

    mensaje: str
    archivos: list[str] = field(default_factory=list)
    binarios: list[IncomingFile] = field(default_factory=list)


@dataclass
class ChatResponse:
    """Salida de un turno del canal web (Req 1.5, 6.3).

    Attributes:
        respuesta: texto del asistente.
        estado: Contract_State (salida de `get_state`, redactado).
    """

    respuesta: str
    estado: dict


#: Nota accionable cuando llegan imágenes pero el proveedor no soporta visión
#: (DD-M7): la imagen igual se puede guardar con `attach_asset`, pero no se envía
#: al modelo. Nombra `PURIQ_LLM_MODE` y los modos con visión, sin abortar el turno.
_NO_VISION_NOTE = (
    "\n\n[Aviso: el proveedor de LLM actual no soporta visión (imágenes). Las "
    "imágenes adjuntas NO se enviaron al modelo, pero sí se pueden guardar y "
    "asociar con attach_asset. Para analizar imágenes, configurá la variable "
    "PURIQ_LLM_MODE a un modo con visión ('bedrock' o 'openai').]"
)


def _build_user_content(
    mensaje: str,
    archivos: list[str],
    *,
    pdf_texts: list[str] | None = None,
    image_names: list[str] | None = None,
    rejected: list[str] | None = None,
    no_vision_note: bool = False,
) -> str:
    """Compone el mensaje de usuario del turno (texto + referencias + multimodal).

    Concatena `mensaje` con:
      - las **referencias** de `archivos` (rutas bajo `assets/`) como contexto
        textual, para que el asistente las reconozca (Req 8.2); nunca lee ni
        transmite bytes (Req 8.3).
      - los `image_names` de los binarios de imagen del turno (nombres
        normalizados), para que el modelo sepa con qué `filename` invocar
        `attach_asset` (DD-M4); los bytes NO viajan por el texto.
      - los `pdf_texts` extraídos de los PDF del turno como **contexto** a
        destilar (Req 3.2, DD-M5); el PDF no se publica ni se persiste.
      - los mensajes de `rejected` (archivos no soportados/ inválidos), para que
        el asistente informe al usuario (Req 1.4, 3.6).
      - una nota accionable si llegaron imágenes pero el proveedor no tiene
        visión (`no_vision_note`), nombrando `PURIQ_LLM_MODE` (DD-M7).
    """
    partes: list[str] = []
    texto = mensaje or ""
    if texto:
        partes.append(texto)

    referencias = [a for a in (archivos or []) if isinstance(a, str) and a.strip()]
    if referencias:
        lineas = "\n".join(f"- {ref}" for ref in referencias)
        partes.append(
            "[El usuario adjuntó estas imágenes/archivos ya subidos "
            "(referencias, no binarios):]\n"
            f"{lineas}"
        )

    nombres = [n for n in (image_names or []) if isinstance(n, str) and n.strip()]
    if nombres:
        lineas = "\n".join(f"- {nombre}" for nombre in nombres)
        partes.append(
            "[El usuario adjuntó estas imágenes en este turno; usá su nombre tal "
            "cual al invocar attach_asset (los bytes se inyectan automáticamente):]"
            f"\n{lineas}"
        )

    textos_pdf = [t for t in (pdf_texts or []) if isinstance(t, str) and t.strip()]
    for idx, texto_pdf in enumerate(textos_pdf, start=1):
        partes.append(
            f"[Texto extraído de un PDF de contexto (#{idx}) para destilar en "
            "descripciones, Q&A o datos; el PDF no se publica:]\n"
            f"{texto_pdf}"
        )

    mensajes_rechazo = [r for r in (rejected or []) if isinstance(r, str) and r.strip()]
    if mensajes_rechazo:
        lineas = "\n".join(f"- {msg}" for msg in mensajes_rechazo)
        partes.append(
            "[Archivos que no se pudieron procesar (informá al usuario):]\n"
            f"{lineas}"
        )

    contenido = "\n\n".join(partes)
    if no_vision_note:
        contenido = f"{contenido}{_NO_VISION_NOTE}" if contenido else _NO_VISION_NOTE.lstrip()
    return contenido


def _match_asset_key(
    filename: Any, asset_binaries: dict[str, bytes]
) -> str | None:
    """Casa el `filename` de una tool-call con una clave de `asset_binaries` (DD-M4).

    Las claves de `asset_binaries` son nombres **normalizados** (forma `slug.ext`,
    como los produce `normalize_asset_name`). El LLM puede nombrar el archivo tal
    cual lo vio o con leves variantes; se intenta:
      1. coincidencia directa con la clave (el nombre tal cual), y
      2. coincidencia del nombre **normalizado** (mismo criterio que `attach_asset`).
    Devuelve la clave de `asset_binaries` correspondiente, o None si no hay
    binario del turno para ese nombre (en cuyo caso no se inyecta nada).
    """
    if not isinstance(filename, str) or not filename.strip():
        return None
    if filename in asset_binaries:
        return filename
    try:
        normalized = normalize_asset_name(filename, IMAGE_EXTS)
    except ValueError:
        return None
    if normalized in asset_binaries:
        return normalized
    return None


def _dict_to_message(data: Any) -> Message:
    """Reconstruye un `Message` a partir de su forma serializable (historial).

    Tolerante a entradas parciales: campos ausentes se toman como vacíos y una
    entrada malformada se degrada a un mensaje de usuario vacío, para no romper
    la continuidad si la sesión trae ruido.
    """
    if not isinstance(data, dict):
        return Message(role="user", content="")

    role = data.get("role") or "user"
    content = data.get("content")

    tool_calls: list[ToolCall] | None = None
    raw_calls = data.get("tool_calls")
    if isinstance(raw_calls, list):
        tool_calls = [
            ToolCall(
                id=str(item.get("id", "")),
                name=str(item.get("name", "")),
                arguments=item.get("arguments") or {},
            )
            for item in raw_calls
            if isinstance(item, dict)
        ] or None

    tool_result: ToolResult | None = None
    raw_result = data.get("tool_result")
    if isinstance(raw_result, dict):
        tool_result = ToolResult(
            tool_call_id=str(raw_result.get("tool_call_id", "")),
            content=str(raw_result.get("content", "")),
        )

    return Message(
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_result=tool_result,
    )


def _message_to_dict(msg: Message) -> dict:
    """Serializa un `Message` a la forma que espera `save_session` (dicts).

    El `arguments` de las tool-calls persistido NO incluye `project`: se inyecta
    al despachar y no debe guardarse, para no filtrar rutas locales del servidor
    en el historial (DD-2, ver Data Models del diseño).
    """
    out: dict[str, Any] = {"role": msg.role}
    # Se conserva `content` incluso cuando es None (turno de solo tool-calls),
    # coherente con la forma serializable documentada.
    out["content"] = msg.content
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "name": call.name,
                "arguments": {
                    k: v
                    for k, v in (call.arguments or {}).items()
                    if k != "project"
                },
            }
            for call in msg.tool_calls
        ]
    if msg.tool_result is not None:
        out["tool_result"] = {
            "tool_call_id": msg.tool_result.tool_call_id,
            "content": msg.tool_result.content,
        }
    return out


def _serialize_tool_result(result: dict | str) -> str:
    """Serializa el resultado de `run_intake_tool` a texto para el modelo.

    Mismo criterio que `mcp/server._serialize`: un `str` se devuelve tal cual
    (rutas, mensajes); un `dict` (estado del contrato o error accionable) se
    serializa a JSON legible con `schemas.dumps` (Req 5.4).
    """
    if isinstance(result, str):
        return result
    return schemas.dumps(result)


def _infer_phase(estado: dict) -> str | None:
    """Deriva una fase simple del intake a partir de los `missing` del estado.

    Metadato de continuidad (no fuente de verdad): mapea la primera pieza
    faltante a su fase del Intake_Guion. Si no falta nada esencial, devuelve la
    fase de generación ("9"). Se mantiene deliberadamente simple (DD-6).
    """
    missing = estado.get("missing") if isinstance(estado, dict) else None
    if not missing:
        return "9"
    piece_to_phase = {
        "site": "1",
        "modules": "2",
        "places": "3",
        "brand": "5",
    }
    for item in missing:
        if isinstance(item, dict):
            fase = piece_to_phase.get(item.get("piece"))
            if fase is not None:
                return fase
    return None


class ChatAgent:
    """Bucle conversacional por turno de la superficie web (Pieza 3, Req 1).

    Resuelve el proveedor con `get_provider()` cuando no se inyecta uno (Req 4.1);
    la inyección por constructor habilita mocks deterministas para PBT.
    """

    def __init__(
        self,
        project: Path,
        *,
        provider: LLMProvider | None = None,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ):
        """Configura el agente para un Project_Root.

        Args:
            project: raíz del proyecto con los tres documentos del contrato.
            provider: proveedor de LLM; si es None, se resuelve con
                `get_provider()` (Req 4.1). Inyectable para pruebas.
            max_tool_rounds: cota finita de rondas de tool-call por turno
                (Req 1.6, default `DEFAULT_MAX_TOOL_ROUNDS`).
        """
        self.project = Path(project)
        self._provider = provider if provider is not None else get_provider()
        self.max_tool_rounds = max_tool_rounds

    def run_turn(self, request: ChatRequest) -> ChatResponse:
        """Procesa un turno completo de conversación (Req 1, 5, 8, 9, 10).

        Pasos:
          1. Cargar sesión (`load_session`): historial + fase previos, tolerante
             a ausencia/corrupción (Req 10.1, 10.2).
          2. Estado inicial con `get_state` para inyectar en el prompt (Req 1.1).
          3. Preparar los binarios del turno con `prepare_incoming` según la
             visión del proveedor (Req 1, 2.2, 3.2, 10, DD-M1/M5/M7/M9).
          4. Construir mensajes: system (`build_system_prompt`) + historial +
             mensaje de usuario con referencias + nombres de imágenes + texto de
             PDF (contexto) + rechazos (Req 3.2, 8.2, 8.3); imágenes como
             `Message.images` si hay visión, o nota accionable si no (DD-M4/M7).
          5. Bucle de tool-use hasta `max_tool_rounds` (Req 1.6): despachar cada
             tool-call por `run_intake_tool` con `project` inyectado (Req 1.3,
             1.8, 5.1) y, para `attach_asset` de un binario del turno, con
             `content_base64` inyectado por nombre (DD-M4, Req 2.1); texto sin
             tool-calls finaliza el turno (Req 1.4).
          6. Al agotar el límite con tool-calls pendientes, cerrar con mensaje de
             límite alcanzado (Req 1.7).
          7. Estado final con `get_state` tras las tool-calls (Req 1.5, 5.5,
             10.3).
          8. Persistir sesión redactada SIN binarios, `content_base64` ni
             `Message.images` (`save_session`, Req 9.1, 9.3, DD-M4).
          9. Devolver `ChatResponse(respuesta, estado)`.
        """
        # 1) Sesión previa (continuidad; tolerante a ausencia/corrupción).
        session = load_session(self.project)
        conversation: list[Message] = [
            _dict_to_message(item) for item in session.history
        ]

        # 2) Estado inicial del contrato para orientar el prompt del turno.
        estado_inicial = get_state(self.project)
        system_msg = Message(
            role="system", content=build_system_prompt(estado_inicial)
        )

        # 3) Preparar los binarios del turno (imágenes/PDF) enrutándolos y
        #    validándolos según haya visión o no (Req 1, 2.2, 3.2, 10,
        #    DD-M1/M5/M7/M9). El router NO escribe el contrato: solo produce los
        #    artefactos que consume este turno.
        supports_vision = bool(getattr(self._provider, "supports_vision", False))
        ingest_result = prepare_incoming(
            request.binarios, supports_vision=supports_vision
        )
        # Mapa filename normalizado -> bytes para la inyección de DD-M4; el LLM
        # razona solo con el nombre y el agente inyecta los bytes al despachar.
        asset_binaries = ingest_result.asset_binaries

        # 4) Mensaje de usuario: texto + referencias + nombres de imágenes del
        #    turno + texto de PDF como contexto + rechazos; imágenes como
        #    Message.images si hay visión (Req 2.2, 3.2, 8.2, 8.3, DD-M4/M5/M7).
        # Sin visión pero con imágenes: nota accionable que nombra PURIQ_LLM_MODE.
        no_vision_note = (not supports_vision) and bool(asset_binaries)
        user_content = _build_user_content(
            request.mensaje,
            request.archivos,
            pdf_texts=ingest_result.pdf_texts,
            image_names=list(asset_binaries.keys()),
            rejected=ingest_result.rejected,
            no_vision_note=no_vision_note,
        )
        # Adjuntar los bloques de imagen como Message.images SOLO si el ingest los
        # produjo (implica proveedor con visión, DD-M7). Se convierte cada
        # ImageBlock del ingest a ImageContent del modelo de mensajes neutral.
        user_images = [
            ImageContent(media_type=block.media_type, data=block.data)
            for block in ingest_result.image_blocks
        ] or None
        user_msg = Message(
            role="user",
            content=user_content,
            images=user_images,
        )
        conversation.append(user_msg)

        # 4) Bucle de tool-use acotado por max_tool_rounds (Req 1.6).
        respuesta = _LIMIT_MESSAGE
        for _ in range(self.max_tool_rounds):
            result = self._provider.complete_chat(
                [system_msg, *conversation], tools=INTAKE_TOOL_SPECS
            )
            # Anexar el turno del asistente al historial antes de despachar.
            conversation.append(result.assistant_message)

            if result.tool_calls:
                for call in result.tool_calls:
                    # Inyectar project SIN mutar los args originales del modelo
                    # (que se persisten sin project ni bytes) — DD-2, DD-M4,
                    # Req 1.8, 2.1.
                    args = dict(call.arguments or {})
                    args["project"] = str(self.project)
                    # Inyección de bytes por nombre de archivo (DD-M4, Req 2.1):
                    # si el modelo pide attach_asset para un binario del turno,
                    # se inyecta su content_base64 en una copia de los args ANTES
                    # de despachar, de modo que el LLM nunca transporte los bytes.
                    if call.name == "attach_asset":
                        fname = _match_asset_key(
                            args.get("filename"), asset_binaries
                        )
                        if fname is not None:
                            args["content_base64"] = base64.b64encode(
                                asset_binaries[fname]
                            ).decode("ascii")
                    # Despacho por el núcleo; tool desconocida o error se traduce
                    # a un resultado accionable sin lanzar (Req 5.1, 5.3, 5.4).
                    tool_output = run_intake_tool(call.name, args)
                    conversation.append(
                        Message(
                            role="tool",
                            tool_result=ToolResult(
                                tool_call_id=call.id,
                                content=_serialize_tool_result(tool_output),
                            ),
                        )
                    )
                # Continuar el bucle: el modelo verá los Tool_Result.
                continue

            # Texto sin tool-calls: fin del turno (Req 1.4).
            respuesta = result.text or ""
            break
        else:
            # Se alcanzó el límite con tool-calls aún pendientes (Req 1.7): se
            # cierra con un mensaje de límite y el contrato vigente.
            conversation.append(Message(role="assistant", content=respuesta))

        # 7) Estado final tras las tool-calls (Req 1.5, 5.5); los faltantes salen
        #    del contrato en disco, no del historial (Req 10.3).
        estado_final = get_state(self.project)

        # 8) Persistir sesión redactada (Req 9.1, 9.3). `_message_to_dict` no
        #    serializa `Message.images` ni el `content_base64` inyectado (que solo
        #    vive en la copia local de los args), igual que excluye `project` de
        #    los args de las tool-calls (DD-2, DD-M4): la sesión nunca guarda bytes.
        history_serializable = [_message_to_dict(msg) for msg in conversation]
        phase = _infer_phase(estado_final)
        save_session(self.project, history_serializable, phase)

        # 9) Devolver la respuesta del turno.
        return ChatResponse(respuesta=respuesta, estado=estado_final)
