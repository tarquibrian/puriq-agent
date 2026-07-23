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
  - `openai`  -> `OpenAICompatibleProvider` (API compatible con OpenAI: OpenAI,
    Azure OpenAI, Groq, OpenRouter, servidores locales tipo vLLM/LM Studio).

Modo `openai` (proveedor compatible con OpenAI, con soporte para Azure):
  Se selecciona con `PURIQ_LLM_MODE=openai` y se configura por entorno:
    - `PURIQ_OPENAI_API_KEY`    (requerida; se registra como secreto).
    - `PURIQ_OPENAI_BASE_URL`   (default `https://api.openai.com/v1`).
    - `PURIQ_OPENAI_MODEL`      (nombre del modelo; en Azure es el DEPLOYMENT;
       default `gpt-4o-mini` para OpenAI estandar).
    - `PURIQ_OPENAI_API_VERSION`(solo Azure; default `2024-10-21`).
  Deteccion de Azure: si `base_url` contiene `azure.com`, se usa el estilo Azure
  (cabecera `api-key`, URL `/openai/deployments/<deployment>/chat/completions?
  api-version=...`, sin `model` en la URL). En caso contrario se usa el estilo
  OpenAI estandar (cabecera `Authorization: Bearer`, URL `<base>/chat/completions`
  con `model` en el cuerpo). La E/S usa `httpx` (dependencia ya presente).

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
        """Crea (una sola vez) y devuelve el cliente `bedrock-runtime` de boto3.

        Resuelve la region via `AWS_REGION` (como hace `geocode`) y la pasa como
        `region_name`. Si no esta definida, se omite para que boto3 use su propia
        cadena de configuracion (p. ej. `AWS_DEFAULT_REGION` o el perfil).
        """
        if self._client is None:
            import boto3  # import diferido: solo si se usa Bedrock

            region = get_env("AWS_REGION")
            kwargs = {}
            if region:
                kwargs["region_name"] = region
            self._client = boto3.client("bedrock-runtime", **kwargs)
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


# Valores por defecto del proveedor compatible con OpenAI.
_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
# Version de API por defecto para Azure OpenAI (estable actual).
_DEFAULT_OPENAI_API_VERSION = "2024-10-21"
# Timeout prudente para las llamadas HTTP al servicio de LLM.
_OPENAI_TIMEOUT_SECONDS = 60.0


class OpenAICompatibleProvider:
    """Proveedor de LLM sobre cualquier API compatible con OpenAI (con Azure).

    Cubre dos estilos de la API `chat/completions` detectados por la `base_url`:

    - Azure OpenAI (si `base_url` contiene ``azure.com``):
        * Autenticacion con la cabecera ``api-key: <clave>``.
        * URL con el deployment en la ruta:
          ``<base>/openai/deployments/<deployment>/chat/completions?api-version=<v>``
          (si `base_url` ya termina en ``/openai`` no se duplica ese segmento).
          El modelo/deployment va en la URL, no en el cuerpo.
    - OpenAI estandar y compatibles (OpenAI, Groq, OpenRouter, vLLM/LM Studio):
        * Autenticacion con la cabecera ``Authorization: Bearer <clave>``.
        * URL ``<base>/chat/completions`` con ``"model"`` en el cuerpo.

    La clave se lee con `get_env(..., secret=True)`, de modo que `config.redact`
    enmascara su valor en cualquier salida o mensaje de error; este proveedor
    nunca la registra ni la imprime. La E/S usa `httpx` (dependencia existente).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_version: str | None = None,
        max_tokens: int = _MAX_TOKENS,
    ):
        """Configura el proveedor leyendo la configuracion por entorno.

        Args:
            api_key: clave de API. Si es None, se lee de `PURIQ_OPENAI_API_KEY`
                (requerida; ausente -> `MissingEnvVarError`) y se registra como
                secreto para el enmascarado de `redact`.
            base_url: endpoint base. Si es None, se lee de `PURIQ_OPENAI_BASE_URL`
                (default `https://api.openai.com/v1`).
            model: nombre del modelo (en Azure, el nombre del DEPLOYMENT). Si es
                None, se lee de `PURIQ_OPENAI_MODEL` (default `gpt-4o-mini`).
            api_version: version de API para Azure. Si es None, se lee de
                `PURIQ_OPENAI_API_VERSION` (default `2024-10-21`).
            max_tokens: tope de tokens de la respuesta generada.
        """
        # `secret=True` registra la clave para que `redact` la enmascare; si
        # falta, `get_env(required=True)` lanza `MissingEnvVarError` nombrandola.
        self._api_key = api_key or get_env(
            "PURIQ_OPENAI_API_KEY", required=True, secret=True
        )
        self._base_url = (
            base_url or get_env("PURIQ_OPENAI_BASE_URL") or _DEFAULT_OPENAI_BASE_URL
        )
        self._model = (
            model or get_env("PURIQ_OPENAI_MODEL") or _DEFAULT_OPENAI_MODEL
        )
        self._api_version = (
            api_version
            or get_env("PURIQ_OPENAI_API_VERSION")
            or _DEFAULT_OPENAI_API_VERSION
        )
        self._max_tokens = max_tokens

    @property
    def is_azure(self) -> bool:
        """True si la `base_url` apunta a Azure OpenAI (contiene `azure.com`)."""
        return "azure.com" in self._base_url.lower()

    def _build_request(self, prompt: str) -> tuple[str, dict, dict]:
        """Construye ``(url, headers, body)`` segun el estilo (Azure vs estandar).

        En Azure el deployment viaja en la URL y la autenticacion usa `api-key`;
        en el estilo estandar el modelo viaja en el cuerpo y la autenticacion usa
        `Authorization: Bearer`.
        """
        base = self._base_url.rstrip("/")
        body: dict = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_tokens,
            "temperature": 0.7,
        }
        if self.is_azure:
            # Evitar duplicar `/openai` si la base ya lo incluye.
            prefix = base if base.endswith("/openai") else f"{base}/openai"
            url = (
                f"{prefix}/deployments/{self._model}/chat/completions"
                f"?api-version={self._api_version}"
            )
            headers = {
                "api-key": self._api_key,
                "Content-Type": "application/json",
            }
        else:
            url = f"{base}/chat/completions"
            body["model"] = self._model
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        return url, headers, body

    def complete(self, prompt: str) -> str:
        """Invoca `chat/completions` via httpx y devuelve el texto de la respuesta.

        Hace POST con timeout prudente, propaga los errores HTTP (los traducen y
        enmascaran los manejadores del CLI/wizard) y extrae
        `choices[0].message.content`.
        """
        import httpx  # import diferido: solo si se usa el modo openai

        url, headers, body = self._build_request(prompt)
        response = httpx.post(
            url, headers=headers, json=body, timeout=_OPENAI_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return self._extract_text(response.json())

    @staticmethod
    def _extract_text(payload: dict) -> str:
        """Extrae `choices[0].message.content` de la respuesta chat.completions."""
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()


def get_provider() -> LLMProvider:
    """Fabrica del proveedor de LLM segun configuracion (DD-4).

    Selecciona por la env `PURIQ_LLM_MODE`:
      - `local`   -> `OllamaProvider` (fallback local).
      - `openai`  -> `OpenAICompatibleProvider` (OpenAI/Azure/Groq/OpenRouter...).
      - `bedrock` (o cualquier otro valor / ausente) -> `BedrockProvider`.

    Returns:
        Una instancia que cumple el protocolo `LLMProvider`.
    """
    mode = (get_env("PURIQ_LLM_MODE") or "bedrock").strip().lower()
    if mode == "local":
        logger.debug("LLM mode: local (Ollama)")
        return OllamaProvider()
    if mode == "openai":
        logger.debug("LLM mode: openai (proveedor compatible con OpenAI/Azure)")
        return OpenAICompatibleProvider()
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


def enrich(data: dict, voice: dict | None = None, *, translate: bool = True) -> dict:
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
        translate: keyword-only. Si es True (por defecto) se ejecuta el paso 4
            (generacion de traducciones a los Locales extra bajo `i18n`). Si es
            False se OMITE ese paso: util cuando el consumidor del contrato aun
            no renderiza `i18n` y esas llamadas al LLM serian coste desperdiciado.
            El resto del enriquecimiento (descripciones, SEO) no cambia.

    Returns:
        El mismo `data`, con descripciones/SEO completados y, si corresponde y
        `translate` es True, las traducciones bajo la clave companion `i18n`.
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
    #    Solo se ejecuta cuando `translate` es True: si el consumidor del
    #    contrato aun no renderiza `i18n`, generar traducciones seria coste
    #    desperdiciado, asi que el llamador puede desactivarlo con translate=False.
    if translate:
        traducciones = generate_translations(data, voice, provider)
        if traducciones:
            data[I18N_KEY] = traducciones

    return data


# --- copy de la portada (Landing_Module) -----------------------------------
#
# `enrich_landing` reutiliza el patron probado de `enrich`: resuelve el
# proveedor una vez (`get_provider`), completa SOLO los campos de copy vacios
# de las secciones activas, preserva lo no vacio (Req 15.2), inyecta el tono de
# marca via `_voice_directives` (Req 15.3) y tolera un fallo por seccion con
# `_safe_complete` conservando el valor previo (Req 15.4). Opera sobre
# `site.config.json` (no sobre `tourism-data.json`) y devuelve un Site_Config
# conforme a `site-config.schema.json` (Req 15.5): no introduce campos nuevos,
# solo rellena los campos de texto ya previstos por cada tipo de seccion.

# Tipos de Landing_Section del catalogo soportado (DD-3). Un tipo fuera de este
# conjunto se ignora (no se le genera copy), coherente con la omision con gracia
# del render.
_LANDING_TYPES = frozenset({"hero", "features", "cta", "gallery", "stats"})


def _landing_data_context(data: dict) -> str:
    """Arma un resumen de Tourism_Data para contextualizar el copy de portada.

    Incluye el nombre del sitio, la region y hasta unos pocos lugares
    destacados, para que el LLM redacte copy pertinente al destino (Req 15.1).
    """
    data = data or {}
    site = data.get("site", {}) or {}
    partes: list[str] = []
    if site.get("name"):
        partes.append(f"Sitio: {site['name']}.")
    if site.get("region"):
        partes.append(f"Region: {site['region']}.")
    lugares = [p.get("name", "") for p in (data.get("places") or []) if p.get("name")]
    if lugares:
        partes.append(f"Lugares destacados: {', '.join(lugares[:5])}.")
    return " ".join(partes)


def _landing_prompt(tarea: str, contexto: str, voice: dict | None) -> str:
    """Arma el prompt para redactar un campo de copy de una Landing_Section.

    `tarea` describe que escribir (y su limite); `contexto` es el resumen de
    Tourism_Data. Incluye siempre las directivas de voz (tono/formalidad) via
    `_voice_directives`, de modo que el tono de marca se refleje en el prompt
    (Req 15.3).
    """
    partes = [
        "Sos un redactor de turismo. Escribi copy para una seccion de la "
        "portada de un sitio turistico, en espanol.",
        _voice_directives(voice),
        tarea,
    ]
    if contexto:
        partes.append(f"Contexto del destino: {contexto}")
    return "\n".join(partes)


def _fill_blank_field(
    container: dict,
    key: str,
    provider: LLMProvider,
    tarea: str,
    contexto: str,
    voice: dict | None,
    contexto_log: str,
) -> None:
    """Rellena `container[key]` con el LLM solo si esta vacio (Req 15.1, 15.2).

    Si el campo ya tiene texto no vacio, no se toca (Req 15.2). Ante un fallo
    del LLM (o texto vacio), `_safe_complete` devuelve None y se conserva el
    valor previo, registrando la causa y continuando (Req 15.4).
    """
    if not _is_blank(container.get(key)):
        return
    texto = _safe_complete(
        provider, _landing_prompt(tarea, contexto, voice), contexto_log
    )
    if texto is not None:
        container[key] = texto


def _enrich_hero_content(provider, content, contexto, voice) -> None:
    """Rellena el copy vacio de una seccion `hero`: headline y subheadline."""
    _fill_blank_field(
        content, "headline", provider,
        "Escribi un titular breve y atractivo (maximo 8 palabras) para el hero "
        "de la portada. Devolve solo el texto, sin comillas.",
        contexto, voice, "copy de landing hero.headline",
    )
    _fill_blank_field(
        content, "subheadline", provider,
        "Escribi un subtitulo de una sola oracion que complemente el titular "
        "del hero. Devolve solo el texto, sin comillas.",
        contexto, voice, "copy de landing hero.subheadline",
    )


def _enrich_features_content(provider, content, contexto, voice) -> None:
    """Rellena el copy vacio de una seccion `features`: title y description por item."""
    items = content.get("items")
    if not isinstance(items, list):
        return
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        _fill_blank_field(
            item, "title", provider,
            "Escribi un titulo breve (2 a 5 palabras) para un destacado de la "
            "seccion de caracteristicas. Devolve solo el texto, sin comillas.",
            contexto, voice, f"copy de landing features.items[{idx}].title",
        )
        titulo = item.get("title") or ""
        sufijo = f" titulado '{titulo}'" if titulo.strip() else ""
        _fill_blank_field(
            item, "description", provider,
            f"Escribi una descripcion breve (1 a 2 oraciones) para el "
            f"destacado{sufijo}. Devolve solo el texto, sin comillas.",
            contexto, voice, f"copy de landing features.items[{idx}].description",
        )


def _enrich_cta_content(provider, content, contexto, voice) -> None:
    """Rellena el copy vacio de una seccion `cta`: message."""
    _fill_blank_field(
        content, "message", provider,
        "Escribi un mensaje breve y persuasivo de llamada a la accion (1 "
        "oracion) para invitar a la persona visitante a explorar el destino. "
        "Devolve solo el texto, sin comillas.",
        contexto, voice, "copy de landing cta.message",
    )


def _enrich_stats_content(provider, content, contexto, voice) -> None:
    """Rellena el copy vacio de una seccion `stats`: label por metrica."""
    metrics = content.get("metrics")
    if not isinstance(metrics, list):
        return
    for idx, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            continue
        valor = metric.get("value")
        detalle = f" cuyo valor es '{valor}'" if not _is_blank(valor) else ""
        _fill_blank_field(
            metric, "label", provider,
            f"Escribi una etiqueta corta (2 a 4 palabras) que describa la "
            f"metrica{detalle}. Devolve solo el texto, sin comillas.",
            contexto, voice, f"copy de landing stats.metrics[{idx}].label",
        )


def _enrich_gallery_content(provider, content, contexto, voice) -> None:
    """Rellena el copy vacio de una seccion `gallery`: alt por imagen."""
    images = content.get("images")
    if not isinstance(images, list):
        return
    for idx, image in enumerate(images):
        if not isinstance(image, dict):
            continue
        src = image.get("src")
        detalle = f" (archivo '{src}')" if not _is_blank(src) else ""
        _fill_blank_field(
            image, "alt", provider,
            f"Escribi un texto alternativo accesible y descriptivo (una frase "
            f"corta) para una imagen de la galeria{detalle}. Devolve solo el "
            f"texto, sin comillas.",
            contexto, voice, f"copy de landing gallery.images[{idx}].alt",
        )


# Despacho por tipo de seccion -> funcion que completa su copy vacio.
_LANDING_ENRICHERS = {
    "hero": _enrich_hero_content,
    "features": _enrich_features_content,
    "cta": _enrich_cta_content,
    "stats": _enrich_stats_content,
    "gallery": _enrich_gallery_content,
}


def enrich_landing(site_config: dict, data: dict, voice: dict | None = None) -> dict:
    """Redacta el copy de las Landing_Section activas con campos vacios (Req 15).

    Recorre `site_config["landing"]` y, para cada seccion ACTIVA (`enabled`
    verdadero) de un tipo del catalogo, genera con el LLM el texto de los campos
    de copy que esten vacios, usando `Tourism_Data` (nombre/region/lugares
    destacados) y el `type` de seccion para armar el prompt (Req 15.1). El tono
    de marca (`voice.tone`) se inyecta en el prompt via `_voice_directives`
    (Req 15.3).

    Campos de copy por tipo:
      - `hero.content.{headline, subheadline}`
      - `features.content.items[].{title, description}`
      - `cta.content.message`
      - `stats.content.metrics[].label`
      - `gallery.content.images[].alt`

    Comportamiento (analogo a `enrich`):
      - El copy no vacio se conserva sin cambios (Req 15.2).
      - Un fallo del LLM por seccion/campo conserva el valor previo (vacio),
        registra la causa y continua con el resto, via `_safe_complete`
        (Req 15.4).
      - Las secciones inactivas o de tipo no soportado se omiten (no se les
        genera copy).

    El proveedor de LLM se resuelve UNA sola vez por invocacion via
    `get_provider()` (DD-4) y se reutiliza en todas las secciones.

    Conformidad con el contrato (Req 15.5): la funcion solo rellena campos de
    texto ya previstos dentro de `content` (objeto abierto en el esquema) y no
    introduce claves de nivel superior ni altera la estructura de `landing`, de
    modo que el resultado sigue cumpliendo `site-config.schema.json`.

    Args:
        site_config: documento Site_Config (se modifica in situ y se devuelve).
        data: documento Tourism_Data del que se toma el contexto (no se modifica).
        voice: subdocumento `voice` de Theme_Tokens (`tone`, `formality`).

    Returns:
        El mismo `site_config`, con el copy vacio de las secciones activas
        completado en la medida en que el LLM haya respondido.
    """
    site_config = site_config or {}
    sections = site_config.get("landing")
    if not isinstance(sections, list):
        return site_config

    provider = get_provider()
    contexto = _landing_data_context(data)

    for section in sections:
        if not isinstance(section, dict) or not section.get("enabled"):
            continue
        enricher = _LANDING_ENRICHERS.get(section.get("type"))
        if enricher is None:
            continue
        content = section.get("content")
        if not isinstance(content, dict):
            continue
        enricher(provider, content, contexto, voice)

    return site_config
