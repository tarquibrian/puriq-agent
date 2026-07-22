"""Traduccion compartida de errores a mensajes accionables (DD-4).

Este modulo centraliza la logica que convierte una excepcion lanzada por las
tools/core en un par ``(causa, accion sugerida)`` legible para el usuario. Era
originalmente `cli._describir_error`; se extrajo aqui para que tanto el CLI
(`puriq.cli`) como el wizard web (`puriq.wizard.server`) usen **una sola fuente
de verdad** para los mensajes de error, evitando divergencia entre superficies.

Reglas transversales que se respetan (Req 7.2, 7.4, 7.5, 8.4, 10.4, 12.2, 12.3):
  - El texto devuelto por `describir_error` NO se enmascara aqui: el enmascarado
    con `puriq.config.redact` lo aplica quien serializa (el decorador del CLI o
    `wizard_error_response`).
  - `wizard_error_response` SIEMPRE aplica `config.redact` antes de devolver el
    texto, de modo que ningun valor de secreto llegue a una respuesta HTTP o a
    un mensaje WebSocket (Req 7.5, 12.2).
  - `MissingEnvVarError` produce un mensaje que NOMBRA la variable sin exponer su
    valor (Req 12.3).
  - `jsonschema.ValidationError` se traduce a ``{documento, campo, sugerencia}``
    (Req 7.2).
  - `schemas.MissingCoordsError` produce un mensaje que nombra cada Place
    afectado (Req 7.4).
"""
from __future__ import annotations

from puriq import config


def describir_error(exc: BaseException) -> tuple[str, str | None]:
    """Traduce una excepcion a (causa, accion sugerida) legible para el usuario.

    Devuelve un mensaje de causa descriptivo y, cuando puede inferirse, una
    accion sugerida. La deteccion se hace por tipo de excepcion y, en algunos
    casos, por pistas en el texto del mensaje (p. ej. `dist/` ausente). El texto
    devuelto NO se enmascara aqui: el enmascarado con `redact` lo aplica quien
    serializa (el decorador del CLI o `wizard_error_response`) (Req 12.2).
    """
    # Import perezoso: evita acoplar el arranque al peso de estos modulos.
    from puriq import schemas
    from puriq.config import MissingEnvVarError

    try:
        from jsonschema import ValidationError as _JsonSchemaValidationError
    except Exception:  # pragma: no cover - jsonschema es dependencia declarada
        _JsonSchemaValidationError = ()  # type: ignore[assignment]

    mensaje = str(exc).strip() or exc.__class__.__name__

    # Variable de entorno requerida ausente: el mensaje ya nombra la variable
    # sin su valor (Req 12.3). Sugerimos definirla en agent/.env.
    if isinstance(exc, MissingEnvVarError):
        return (
            f"Configuracion incompleta: {mensaje}",
            "Defini la variable en agent/.env (ver agent/.env.example).",
        )

    # Place sin coordenadas tras geocode: el mensaje ya nombra cada Place (Req 7.4).
    if isinstance(exc, schemas.MissingCoordsError):
        return (
            f"Faltan coordenadas: {mensaje}",
            "Agrega una direccion o coordenadas (lat/lng) a los lugares indicados.",
        )

    # Contrato invalido contra su esquema JSON.
    if _JsonSchemaValidationError and isinstance(exc, _JsonSchemaValidationError):
        return (
            f"El contrato no cumple su esquema: {mensaje}",
            "Revisa el documento (tourism-data.json / site.config.json / "
            "theme.tokens.json) y corregi el campo indicado.",
        )

    # Recursos o artefactos ausentes en disco.
    if isinstance(exc, FileNotFoundError):
        texto = f"{mensaje} {getattr(exc, 'filename', '') or ''}".lower()
        if "dist" in texto:
            return (
                f"No se encontro el sitio construido: {mensaje}",
                "Ejecuta `puriq build` primero para generar el directorio dist/.",
            )
        return (
            f"No se encontro un archivo o directorio necesario: {mensaje}",
            "Verifica que los recursos del proyecto existan en la ruta indicada.",
        )

    # Errores de validacion de entrada: destino de deploy invalido, lat/lng no
    # numerico, etc. El mensaje de la tool suele ser accionable por si mismo.
    if isinstance(exc, ValueError):
        return (f"Entrada invalida: {mensaje}", None)

    # Errores de red / servicio (AWS, HTTP): detectados por nombre de clase para
    # no acoplar el modulo a boto3/httpx.
    nombre = type(exc).__name__
    if any(
        clave in nombre
        for clave in ("ClientError", "BotoCoreError", "HTTPError", "ConnectError",
                      "ConnectionError", "Timeout", "EndpointConnectionError")
    ):
        return (
            f"Fallo de red o servicio externo: {mensaje}",
            "Revisa tu conexion y las credenciales configuradas en agent/.env, "
            "y volve a intentarlo.",
        )

    # Fallback: mensaje descriptivo generico, sin traza cruda.
    return (f"{nombre}: {mensaje}", None)


# Mapeo de $id/title de esquema a nombre de archivo del documento del contrato,
# usado para nombrar el documento infractor en un ValidationError (Req 7.2).
_DOC_POR_PISTA: dict[str, str] = {
    "tourism-data": "tourism-data.json",
    "tourism data": "tourism-data.json",
    "site-config": "site.config.json",
    "site config": "site.config.json",
    "theme-tokens": "theme.tokens.json",
    "theme tokens": "theme.tokens.json",
}


def _doc_desde_pista(pista: str | None) -> str | None:
    """Infiere el nombre de archivo del contrato desde un $id/title de esquema."""
    if not pista:
        return None
    texto = pista.lower()
    for clave, nombre in _DOC_POR_PISTA.items():
        if clave in texto:
            return nombre
    return None


def _documento_de_validation_error(exc: object) -> str | None:
    """Intenta nombrar el documento del contrato infractor (best-effort).

    Recorre el esquema del error y su cadena de contexto/padre buscando un `$id`
    o `title` reconocible; si no lo encuentra, devuelve None (el llamador puede
    pasar el nombre del documento explicitamente).
    """
    node: object | None = exc
    while node is not None:
        schema = getattr(node, "schema", None)
        if isinstance(schema, dict):
            doc = _doc_desde_pista(schema.get("$id") or schema.get("title"))
            if doc:
                return doc
        node = getattr(node, "parent", None)
    return None


def _campo_de_validation_error(exc: object) -> str | None:
    """Construye una ruta legible al campo infractor (p. ej. `places[0].coords`)."""
    path = list(getattr(exc, "absolute_path", []) or [])
    if path:
        campo = ""
        for parte in path:
            if isinstance(parte, int):
                campo += f"[{parte}]"
            elif campo:
                campo += f".{parte}"
            else:
                campo = str(parte)
        return campo
    # Sin ruta (p. ej. propiedad requerida ausente a nivel raiz): intentar
    # extraer el nombre citado del mensaje de jsonschema.
    mensaje = str(getattr(exc, "message", "") or "")
    if "'" in mensaje:
        try:
            return mensaje.split("'", 2)[1]
        except IndexError:
            return None
    return None


def wizard_error_response(exc: BaseException, documento: str | None = None) -> dict:
    """Traduce una excepcion a una respuesta serializable del wizard (DD-4).

    Reutiliza `describir_error` (misma fuente de verdad que el CLI) y aplica
    **siempre** `config.redact` al texto antes de devolverlo, de modo que ningun
    valor de secreto aparezca en una respuesta HTTP o mensaje WebSocket
    (Req 7.5, 12.2).

    Casos especiales:
      - `MissingEnvVarError`: la causa nombra la variable sin su valor (Req 12.3).
      - `jsonschema.ValidationError`: se devuelve ``{documento, campo, sugerencia}``
        (Req 7.2); `documento` puede pasarse explicitamente si se conoce.
      - `schemas.MissingCoordsError`: la causa nombra cada Place afectado (Req 7.4).

    Args:
        exc: la excepcion a traducir.
        documento: nombre del documento del contrato en cuestion, si se conoce
            (permite nombrarlo aun cuando no pueda inferirse del error).

    Returns:
        Un dict listo para serializar. Para `ValidationError`:
        ``{"documento", "campo", "sugerencia"}``. Para el resto:
        ``{"causa", "accion"}`` (``accion`` puede ser None).
    """
    try:
        from jsonschema import ValidationError as _JsonSchemaValidationError
    except Exception:  # pragma: no cover - jsonschema es dependencia declarada
        _JsonSchemaValidationError = ()  # type: ignore[assignment]

    # Validacion de esquema -> {documento, campo, sugerencia} (Req 7.2, 7.3).
    if _JsonSchemaValidationError and isinstance(exc, _JsonSchemaValidationError):
        doc = documento or _documento_de_validation_error(exc)
        campo = _campo_de_validation_error(exc)
        detalle = str(getattr(exc, "message", "") or "").strip()
        sugerencia = (
            f"Revisa {doc or 'el documento del contrato'} y corregi el campo indicado."
        )
        if detalle:
            sugerencia = f"{detalle}. {sugerencia}"
        return {
            "documento": config.redact(doc) if doc else None,
            "campo": config.redact(campo) if campo else None,
            "sugerencia": config.redact(sugerencia),
        }

    # Resto de errores: (causa, accion) reutilizando la traduccion del CLI.
    causa, accion = describir_error(exc)
    respuesta: dict = {"causa": config.redact(causa)}
    respuesta["accion"] = config.redact(accion) if accion else None
    return respuesta
