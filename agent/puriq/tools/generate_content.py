"""generate_content: usa el LLM (Amazon Bedrock) para rellenar contenido.

Trabaja SOLO sobre contenido/config, nunca sobre codigo:
  - descripciones de lugares/eventos vacias
  - metadatos SEO
  - traducciones a los locales configurados
El tono se toma de theme.tokens.json -> voice.tone.

Este modulo aisla la seleccion del proveedor de LLM detras del protocolo
`LLMProvider` (patron de adaptadores del diseno). La fabrica `get_provider`
resuelve el proveedor por configuracion (DD-4) segun `PURIQ_LLM_MODE`:
  - `local`   -> `OllamaProvider` (fallback local, extra `local`).
  - `bedrock` -> `BedrockProvider` (Amazon Bedrock, por defecto).

`enrich(data, voice=None)` consume el proveedor via `get_provider()` (una vez por
invocacion) para completar SOLO lo faltante: descripciones vacias de
Places/Events, la meta descripcion SEO del sitio (`site.description`) y las
traducciones a los Locales configurados.

Nota de conformidad con el contrato (Req 3.11): `tourism-data.schema.json` fija
`additionalProperties: false` en la raiz y en `site`/`place`/`event`, por lo que
no admite campos extra. Por eso las traducciones NO se embeben en el documento
validado: se adjuntan bajo la clave companion de nivel superior `data["i18n"]`,
y la vista conforme al esquema se obtiene con `contract_view(data)` (ver detalle
en el docstring de `enrich`).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Protocol, runtime_checkable

from puriq.config import get_env

logger = logging.getLogger(__name__)

# Modelos por defecto de cada proveedor. Se pueden sobreescribir por entorno.
_DEFAULT_BEDROCK_MODEL = "anthropic.claude-3-5-sonnet-20240620-v1:0"
_DEFAULT_OLLAMA_MODEL = "llama3.1"

# Modelo de Bedrock referenciado a nivel de modulo (compatibilidad previa).
# La fabrica y el proveedor leen la env `PURIQ_BEDROCK_MODEL` en tiempo de uso.
BEDROCK_MODEL = os.getenv("PURIQ_BEDROCK_MODEL", _DEFAULT_BEDROCK_MODEL)

# Tokens maximos por respuesta del LLM (limite prudente para descripciones/SEO).
_MAX_TOKENS = 1024


@runtime_checkable
class LLMProvider(Protocol):
    """Interfaz de un proveedor de modelo de lenguaje.

    Un proveedor recibe un prompt de texto y devuelve la respuesta del modelo
    como texto plano. Aisla la E/S del servicio (boto3/ollama) para permitir
    mocks en pruebas y el fallback entre proveedores.
    """

    def complete(self, prompt: str) -> str:
        """Devuelve la respuesta del modelo para `prompt` como texto plano."""
        ...


class BedrockProvider:
    """Proveedor de LLM sobre Amazon Bedrock (familia Claude de Anthropic).

    Usa la API Messages de Bedrock via `invoke_model` con el cuerpo de Claude
    (`anthropic_version` + `messages`). Se elige `invoke_model` sobre `converse`
    porque es estable en boto3 y da control directo sobre el cuerpo del modelo;
    el parseo de la respuesta extrae el texto de los bloques `content`.

    Las credenciales y la region las resuelve boto3 desde el entorno; este
    proveedor no lee ni expone secretos.
    """

    def __init__(self, model_id: str | None = None, *, max_tokens: int = _MAX_TOKENS):
        """Configura el proveedor.

        Args:
            model_id: identificador del modelo de Bedrock. Si es None, se lee de
                la env `PURIQ_BEDROCK_MODEL`, con un default de Claude 3.5 Sonnet.
            max_tokens: tope de tokens de la respuesta generada.
        """
        self._model_id = model_id or get_env("PURIQ_BEDROCK_MODEL") or _DEFAULT_BEDROCK_MODEL
        self._max_tokens = max_tokens
        self._client = None  # cliente boto3 perezoso (se crea al primer uso)

    def _get_client(self):
        """Crea (una sola vez) y devuelve el cliente `bedrock-runtime` de boto3."""
        if self._client is None:
            import boto3  # import diferido: solo si se usa Bedrock

            self._client = boto3.client("bedrock-runtime")
        return self._client

    def complete(self, prompt: str) -> str:
        """Invoca Bedrock con la API Messages de Claude y devuelve el texto."""
        client = self._get_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": prompt}]}
            ],
        }
        response = client.invoke_model(
            modelId=self._model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(response["body"].read())
        return self._extract_text(payload)

    @staticmethod
    def _extract_text(payload: dict) -> str:
        """Extrae el texto concatenando los bloques `content` de tipo `text`."""
        content = payload.get("content", [])
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts).strip()


class OllamaProvider:
    """Proveedor de LLM local via Ollama (fallback sin nube).

    Requiere la libreria `ollama` (extra `local`: `pip install puriq[local]`).
    El modelo se toma de la env `PURIQ_OLLAMA_MODEL` (default `llama3.1`).
    """

    def __init__(self, model: str | None = None):
        """Configura el proveedor.

        Args:
            model: nombre del modelo de Ollama. Si es None, se lee de la env
                `PURIQ_OLLAMA_MODEL`, con un default de `llama3.1`.
        """
        self._model = model or get_env("PURIQ_OLLAMA_MODEL") or _DEFAULT_OLLAMA_MODEL

    def complete(self, prompt: str) -> str:
        """Genera una respuesta con el modelo local de Ollama y la devuelve."""
        try:
            import ollama  # import diferido: solo si se usa el modo local
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "El modo LLM local requiere el extra 'local'. "
                "Instalalo con: pip install puriq[local]"
            ) from exc
        response = ollama.generate(model=self._model, prompt=prompt)
        # `ollama.generate` devuelve un dict/mapping con la clave `response`.
        return (response.get("response") or "").strip()


def get_provider() -> LLMProvider:
    """Fabrica del proveedor de LLM segun configuracion (DD-4).

    Selecciona por la env `PURIQ_LLM_MODE`:
      - `local`   -> `OllamaProvider` (fallback local).
      - `bedrock` (o cualquier otro valor / ausente) -> `BedrockProvider`.

    Returns:
        Una instancia que cumple el protocolo `LLMProvider`.
    """
    mode = (get_env("PURIQ_LLM_MODE") or "bedrock").strip().lower()
    if mode == "local":
        logger.debug("LLM mode: local (Ollama)")
        return OllamaProvider()
    logger.debug("LLM mode: bedrock (Amazon Bedrock)")
    return BedrockProvider()


# Clave companion (fuera del contrato) donde `enrich` guarda las traducciones.
# Ver la nota de diseno en `enrich` y `contract_view`.
I18N_KEY = "i18n"

# Tono por defecto cuando `theme.tokens.json -> voice.tone` no esta definido.
_DEFAULT_TONE = "informativo y cercano"

# Mapa minimo de codigos ISO 639-1 a un nombre legible para el prompt. Si el
# codigo no esta en el mapa, se usa el propio codigo (el LLM lo interpreta igual).
_LOCALE_NAMES = {
    "es": "espanol",
    "en": "ingles",
    "pt": "portugues",
    "fr": "frances",
    "de": "aleman",
    "it": "italiano",
    "qu": "quechua",
    "ay": "aymara",
    "gn": "guarani",
}


def _locale_name(locale: str) -> str:
    """Devuelve un nombre legible del Locale para el prompt (o el propio codigo)."""
    return _LOCALE_NAMES.get(locale, locale)


def _voice_directives(voice: dict | None) -> str:
    """Construye la linea de directivas de voz para el prompt (Req 3.4, 3.5).

    Incluye siempre el tono (Req 3.4) y, cuando `voice.formality` esta definido,
    tambien el nivel de formalidad (Req 3.5).
    """
    voice = voice or {}
    tone = (voice.get("tone") or _DEFAULT_TONE).strip()
    directiva = f"Tono: {tone}."
    formality = voice.get("formality")
    if formality:
        directiva += f" Nivel de formalidad: {formality}."
    return directiva


def _is_blank(value: object) -> bool:
    """True si `value` es None, no-string, o una cadena vacia/solo espacios."""
    return not (isinstance(value, str) and value.strip())


def _describe_prompt(item: dict, kind: str, region: str, voice: dict | None,
                     locale: str) -> str:
    """Arma el prompt para redactar la descripcion de un Place o Event.

    Args:
        item: el Place o Event (dict) del que se generara la descripcion.
        kind: "lugar" o "evento", para contextualizar el prompt.
        region: region del sitio (`Tourism_Data.site.region`).
        voice: subdocumento `voice` de Theme_Tokens (tono/formalidad).
        locale: Locale de destino del texto (idioma en que se redacta).
    """
    partes = [
        f"Sos un redactor de turismo. Escribi una descripcion atractiva para el "
        f"siguiente {kind} turistico, en el idioma con codigo ISO '{locale}' "
        f"({_locale_name(locale)}).",
        _voice_directives(voice),
        f"Nombre: {item.get('name', '')}",
    ]
    if region:
        partes.append(f"Region: {region}")
    if item.get("category"):
        partes.append(f"Categoria: {item['category']}")
    if item.get("shortDescription"):
        partes.append(f"Resumen: {item['shortDescription']}")
    if item.get("address"):
        partes.append(f"Direccion: {item['address']}")
    if item.get("tags"):
        partes.append(f"Etiquetas: {', '.join(item['tags'])}")
    partes.append(
        "Devolve solo el texto de la descripcion (2 a 4 oraciones), sin titulos "
        "ni comillas."
    )
    return "\n".join(partes)


def _seo_prompt(name: str, region: str, descripciones: list[str],
                voice: dict | None, locale: str) -> str:
    """Arma el prompt para la meta descripcion SEO del sitio (Req 3.7).

    Se basa en el nombre, la region y las descripciones del contenido de
    `Tourism_Data`.
    """
    contenido = " ".join(d for d in descripciones if d)[:600]
    return "\n".join(
        [
            f"Escribi una meta descripcion SEO en el idioma '{locale}' "
            f"({_locale_name(locale)}) para un sitio de turismo.",
            _voice_directives(voice),
            f"Nombre del sitio: {name}.",
            f"Region: {region}.",
            f"Resumen del contenido: {contenido}" if contenido else "",
            "Devolve solo el texto, en una sola linea de hasta 160 caracteres, "
            "sin comillas.",
        ]
    )


def _translate_prompt(text: str, target_locale: str, voice: dict | None) -> str:
    """Arma el prompt para traducir `text` al Locale destino (Req 3.6)."""
    return "\n".join(
        [
            f"Traduci el siguiente texto al idioma con codigo ISO "
            f"'{target_locale}' ({_locale_name(target_locale)}).",
            _voice_directives(voice),
            "Devolve solo la traduccion, sin comillas ni notas.",
            "",
            text,
        ]
    )


def _safe_complete(provider: LLMProvider, prompt: str, contexto: str) -> str | None:
    """Invoca `provider.complete` capturando fallos por item (Req 3.10, DD-3).

    Ante cualquier excepcion del proveedor, registra la causa y devuelve None,
    para que el llamador conserve el valor previo del item y continue con el
    resto (un fallo por item nunca aborta el enriquecimiento completo).
    """
    try:
        resultado = provider.complete(prompt)
    except Exception as exc:  # noqa: BLE001 - degradacion controlada por item
        logger.warning("Fallo del LLM al generar %s: %s", contexto, exc)
        return None
    resultado = (resultado or "").strip()
    if not resultado:
        logger.warning("El LLM devolvio texto vacio al generar %s", contexto)
        return None
    return resultado


def generate_translations(
    data: dict, voice: dict | None = None, provider: LLMProvider | None = None
) -> dict:
    """Genera traducciones del contenido para cada Locale extra (Req 3.6).

    Recorre `site.locales` y, para cada Locale distinto de `site.defaultLocale`,
    traduce los textos de sitio, Places y Events. Devuelve un documento i18n
    companion (independiente del contrato):

        {
          "<locale>": {
            "site":   {"description": "..."},
            "places": {"<placeId>": {"description": "...", "shortDescription": "..."}},
            "events": {"<eventId>": {"description": "..."}}
          }
        }

    Se devuelve como estructura SEPARADA (no embebida en `tourism-data.json`)
    porque el esquema del contrato fija `additionalProperties: false` en todos
    los niveles y no admite un campo de traducciones; embeberlas invalidaria el
    contrato (Req 3.11). Un fallo del LLM por texto se degrada con gracia: se
    omite ese campo y se continua (Req 3.10, DD-3).
    """
    provider = provider or get_provider()
    site = data.get("site", {}) or {}
    locales = site.get("locales") or []
    default_locale = site.get("defaultLocale")
    extra_locales = [loc for loc in locales if loc and loc != default_locale]
    if not extra_locales:
        return {}

    i18n: dict = {}
    for locale in extra_locales:
        bloque: dict = {"site": {}, "places": {}, "events": {}}

        site_desc = site.get("description")
        if not _is_blank(site_desc):
            traducido = _safe_complete(
                provider,
                _translate_prompt(site_desc, locale, voice),
                f"traduccion de site.description a '{locale}'",
            )
            if traducido:
                bloque["site"]["description"] = traducido

        for place in data.get("places", []):
            campos: dict = {}
            for campo in ("description", "shortDescription"):
                valor = place.get(campo)
                if _is_blank(valor):
                    continue
                traducido = _safe_complete(
                    provider,
                    _translate_prompt(valor, locale, voice),
                    f"traduccion de places[{place.get('id')}].{campo} a '{locale}'",
                )
                if traducido:
                    campos[campo] = traducido
            if campos:
                bloque["places"][place.get("id")] = campos

        for event in data.get("events", []):
            valor = event.get("description")
            if _is_blank(valor):
                continue
            traducido = _safe_complete(
                provider,
                _translate_prompt(valor, locale, voice),
                f"traduccion de events[{event.get('id')}].description a '{locale}'",
            )
            if traducido:
                bloque["events"][event.get("id")] = {"description": traducido}

        i18n[locale] = bloque
    return i18n


def contract_view(data: dict) -> dict:
    """Devuelve la vista del documento conforme al contrato (sin la clave i18n).

    `enrich` adjunta las traducciones bajo la clave companion `i18n` (fuera del
    esquema, ver nota en `enrich`). Esta funcion devuelve una copia superficial
    del documento SIN esa clave, apta para persistir o validar contra
    `tourism-data.schema.json`. Los consumidores del contrato (p. ej.
    `build_site`) deben usar esta vista al escribir/validar el `tourism-data.json`.
    """
    return {k: v for k, v in data.items() if k != I18N_KEY}


def enrich(data: dict, voice: dict | None = None) -> dict:
    """Rellena contenido faltante usando el LLM, respetando la voz de marca.

    Comportamiento (Req 3):
      - Place/Event con `description` vacia -> genera una descripcion con el
        proveedor de LLM (Req 3.1, 3.2). `description` no vacia -> se conserva
        sin cambios (Req 3.3).
      - El prompt incluye el tono de `voice.tone` (Req 3.4) y refleja
        `voice.formality` cuando esta definida (Req 3.5).
      - Metadatos SEO del sitio basados en `name`, `region` y las descripciones
        del contenido (Req 3.7). Se almacenan en `site.description` (unico campo
        de texto libre que el esquema admite como meta descripcion), y solo se
        generan cuando esta vacio, para no pisar datos del usuario.
      - Si `site.locales` tiene mas de un Locale, genera traducciones para cada
        Locale distinto de `site.defaultLocale` (Req 3.6).
      - Un fallo del LLM por item conserva el valor previo de ese item, registra
        la causa y continua con los demas (Req 3.10, DD-3).

    Decision de almacenamiento (conformidad con el contrato, Req 3.11):
      `tourism-data.schema.json` fija `additionalProperties: false` en la raiz y
      en `site`/`place`/`event`, por lo que NO existe un campo del esquema donde
      guardar traducciones ni un bloque SEO dedicado. Para no invalidar el
      contrato:
        * Las descripciones se escriben en `description` (campo del esquema).
        * El SEO se escribe en `site.description` (campo del esquema).
        * Las traducciones se adjuntan bajo la clave companion de nivel superior
          `data["i18n"]`, documentada como EXTENSION fuera del contrato. La vista
          conforme al esquema se obtiene con `contract_view(data)` (sin `i18n`),
          que es la que debe persistirse/validarse. Asi `contract_view(enrich(...))`
          cumple `tourism-data.schema.json`.

    El proveedor de LLM se resuelve UNA sola vez por invocacion via
    `get_provider()` (DD-4) y se reutiliza en todo el enriquecimiento.

    Args:
        data: documento Tourism_Data (se modifica in situ y se devuelve).
        voice: subdocumento `voice` de Theme_Tokens (`tone`, `formality`).

    Returns:
        El mismo `data`, con descripciones/SEO completados y, si corresponde,
        las traducciones bajo la clave companion `i18n`.
    """
    provider = get_provider()
    site = data.get("site", {}) or {}
    region = site.get("region", "")
    default_locale = site.get("defaultLocale") or "es"

    # 1) Descripciones de Places (solo las vacias; se conservan las existentes).
    for place in data.get("places", []):
        if _is_blank(place.get("description")):
            texto = _safe_complete(
                provider,
                _describe_prompt(place, "lugar", region, voice, default_locale),
                f"descripcion de places[{place.get('id')}]",
            )
            if texto is not None:
                place["description"] = texto

    # 2) Descripciones de Events (solo las vacias).
    for event in data.get("events", []):
        if _is_blank(event.get("description")):
            texto = _safe_complete(
                provider,
                _describe_prompt(event, "evento", region, voice, default_locale),
                f"descripcion de events[{event.get('id')}]",
            )
            if texto is not None:
                event["description"] = texto

    # 3) SEO: meta descripcion del sitio en `site.description` (solo si vacia).
    if _is_blank(site.get("description")):
        descripciones = [p.get("description", "") for p in data.get("places", [])]
        seo = _safe_complete(
            provider,
            _seo_prompt(site.get("name", ""), region, descripciones, voice,
                        default_locale),
            "metadatos SEO (site.description)",
        )
        if seo is not None:
            site["description"] = seo
            data["site"] = site

    # 4) Traducciones para cada Locale distinto del `defaultLocale` (Req 3.6).
    #    Se guardan en la clave companion `i18n` (fuera del contrato); usar
    #    `contract_view(data)` para obtener la vista conforme al esquema.
    traducciones = generate_translations(data, voice, provider)
    if traducciones:
        data[I18N_KEY] = traducciones

    return data
