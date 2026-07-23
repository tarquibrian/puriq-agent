"""Pruebas de propiedad del copy de portada (`enrich_landing`), Tareas 9.3/9.4/9.5.

Cubren tres propiedades del diseno `landing-and-design-system` sobre la logica
pura de `puriq.tools.generate_content.enrich_landing`, que redacta el copy vacio
de las Landing_Section activas con el LLM (DD-5, Req 15):

  - Property 12 (Tarea 9.3): el copy vacio de secciones activas se completa y el
    copy no vacio se conserva.
  - Property 13 (Tarea 9.4): el prompt construido para cada seccion refleja la
    voz de marca (`voice.tone`).
  - Property 14 (Tarea 9.5): robustez ante fallo del LLM por seccion: las
    secciones cuya invocacion falla conservan su valor previo (vacio), el resto
    se procesa, y el resultado es conforme a `site-config.schema.json`.

En todas se sustituye el proveedor de LLM por un mock determinista
(monkeypatch de `generate_content.get_provider`), de modo que no hay red ni
llamadas a un LLM real. Minimo 100 iteraciones por propiedad; una sola prueba de
propiedad por propiedad; etiquetadas con `# Feature: ..., Property {n}: {texto}`.
"""
from __future__ import annotations

import copy
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from puriq import schemas
from puriq.tools import generate_content


# --- utilidades comunes ----------------------------------------------------

# Texto centinela que devuelven los proveedores "exitosos": no vacio y sin
# ninguna de las palabras clave por tipo (ver `_TYPE_KEYWORDS`), para que su
# presencia en el copy no altere el enrutado del fallo por seccion (Property 14).
_SENTINEL = "TEXTO_GENERADO"

# Catalogo de tipos soportados (DD-3), tal como los reconoce `enrich_landing`.
_TYPES = ["hero", "features", "cta", "gallery", "stats"]


def _is_blank(value: object) -> bool:
    """True si `value` es None, no-string, o una cadena vacia/solo espacios."""
    return not (isinstance(value, str) and value.strip())


def _collect_copy(section: dict) -> dict:
    """Mapa {ruta -> valor} de los campos de copy presentes en una seccion.

    Recorre los campos de copy por tipo declarados por el diseno:
      hero.{headline, subheadline}; features.items[].{title, description};
      cta.message; stats.metrics[].label; gallery.images[].alt.
    Solo incluye claves presentes; los indices son estables (enrich no agrega ni
    elimina items), por lo que la comparacion antes/despues es posicional.
    """
    t = section.get("type")
    content = section.get("content") or {}
    out: dict = {}
    if t == "hero":
        for k in ("headline", "subheadline"):
            if k in content:
                out[k] = content[k]
    elif t == "cta":
        if "message" in content:
            out["message"] = content["message"]
    elif t == "features":
        for i, item in enumerate(content.get("items") or []):
            if isinstance(item, dict):
                for k in ("title", "description"):
                    if k in item:
                        out[f"items[{i}].{k}"] = item[k]
    elif t == "stats":
        for i, metric in enumerate(content.get("metrics") or []):
            if isinstance(metric, dict) and "label" in metric:
                out[f"metrics[{i}].label"] = metric["label"]
    elif t == "gallery":
        for i, image in enumerate(content.get("images") or []):
            if isinstance(image, dict) and "alt" in image:
                out[f"images[{i}].alt"] = image["alt"]
    return out


# --- estrategias -----------------------------------------------------------

def _blank():
    """Valor de copy 'vacio' segun `_is_blank`: None, cadena vacia o espacios."""
    return st.one_of(st.none(), st.just(""), st.text(alphabet=" \t\n", max_size=3))


def _non_blank():
    """Valor de copy 'no vacio', distinto del centinela del proveedor."""
    return st.text(min_size=1, max_size=20).filter(
        lambda s: bool(s.strip()) and _SENTINEL not in s
    )


@st.composite
def _content(draw, t: str, *, all_blank: bool):
    """Construye el `content` de una seccion de tipo `t`.

    Si `all_blank` es True, todos los campos de copy nacen vacios (para forzar
    la construccion de prompts / la generacion). Si es False, cada campo es
    vacio o no vacio de forma arbitraria (para contrastar completado vs
    preservacion). Las claves de copy siempre estan presentes.
    """
    field = st.just("") if all_blank else st.one_of(_blank(), _non_blank())
    if t == "hero":
        return {"headline": draw(field), "subheadline": draw(field)}
    if t == "cta":
        return {"message": draw(field)}
    if t == "features":
        n = draw(st.integers(min_value=1, max_value=3))
        return {"items": [
            {"title": draw(field), "description": draw(field)} for _ in range(n)
        ]}
    if t == "stats":
        n = draw(st.integers(min_value=1, max_value=3))
        return {"metrics": [
            {"value": "", "label": draw(field)} for _ in range(n)
        ]}
    # gallery
    n = draw(st.integers(min_value=1, max_value=3))
    return {"images": [{"src": "", "alt": draw(field)} for _ in range(n)]}


@st.composite
def _site_config(draw, *, all_blank: bool, all_enabled: bool, min_sections: int):
    """Genera un Site_Config schema-valido con una lista `landing` arbitraria.

    `order` se asigna por posicion (1-based, estrictamente creciente) y `layout`
    y `modules` se incluyen para conformar `site-config.schema.json`.
    """
    n = draw(st.integers(min_value=min_sections, max_value=5))
    sections = []
    for i in range(n):
        t = draw(st.sampled_from(_TYPES))
        enabled = True if all_enabled else draw(st.booleans())
        content = draw(_content(t, all_blank=all_blank))
        sections.append(
            {"type": t, "enabled": enabled, "order": i + 1, "content": content}
        )
    return {
        "layout": draw(st.sampled_from(["clasico", "moderno"])),
        "modules": {},
        "landing": sections,
    }


# --- proveedores de LLM falsos (sin red ni LLM real) -----------------------

class _SucceedingProvider:
    """Proveedor que siempre 'genera' texto: devuelve el centinela no vacio."""

    def complete(self, prompt: str) -> str:
        return _SENTINEL


class _RecordingProvider:
    """Proveedor que registra cada prompt recibido y devuelve texto no vacio."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return _SENTINEL


# Palabra clave por tipo, presente SOLO en los prompts de ese tipo. Permite que
# `_FailByType` decida de forma determinista que secciones fallan a partir del
# texto del prompt, sin acoplarse a datos por instancia (hero/cta no incluyen su
# contenido en el prompt).
_TYPE_KEYWORDS = {
    "hero": "hero",
    "features": "destacado",
    "cta": "llamada a la accion",
    "stats": "metrica",
    "gallery": "galeria",
}


class _FailByType:
    """Falla `complete()` cuando el prompt corresponde a un tipo 'a fallar'.

    Un prompt pertenece a un tipo si contiene su palabra clave. Devuelve el
    centinela no vacio en caso contrario. Cumple el protocolo `LLMProvider` y no
    realiza E/S.
    """

    def __init__(self, fail_types: set[str]) -> None:
        self._keywords = {_TYPE_KEYWORDS[t] for t in fail_types}

    def complete(self, prompt: str) -> str:
        for keyword in self._keywords:
            if keyword in prompt:
                raise RuntimeError(f"fallo simulado del LLM: {keyword}")
        return _SENTINEL


# --- Property 12 (Tarea 9.3) -----------------------------------------------
# Feature: landing-and-design-system, Property 12: El copy vacío de secciones activas se completa; el copy no vacío se conserva
@settings(max_examples=150, deadline=None)
@given(site_config=_site_config(all_blank=False, all_enabled=False, min_sections=0))
def test_enrich_landing_fills_empty_and_preserves_nonempty(site_config):
    """Completa el copy vacio de secciones activas y conserva el no vacio.

    Con un proveedor de LLM exitoso, tras `enrich_landing`: en toda seccion
    ACTIVA de un tipo del catalogo, cada campo de copy que estaba vacio deja de
    estarlo; y en cualquier seccion, todo campo de copy que ya tenia texto no
    vacio se conserva identico.

    Validates: Requirements 15.1, 15.2
    """
    original = copy.deepcopy(site_config)

    with mock.patch.object(
        generate_content, "get_provider", return_value=_SucceedingProvider()
    ):
        result = generate_content.enrich_landing(site_config, {}, None)

    for orig_section, new_section in zip(original["landing"], result["landing"]):
        orig_copy = _collect_copy(orig_section)
        new_copy = _collect_copy(new_section)
        active = bool(orig_section.get("enabled"))
        for path, orig_value in orig_copy.items():
            new_value = new_copy[path]
            if not _is_blank(orig_value):
                # El copy no vacio se conserva sin cambios (Req 15.2).
                assert new_value == orig_value
            elif active:
                # El copy vacio de secciones activas se completa (Req 15.1).
                assert not _is_blank(new_value)


# --- Property 13 (Tarea 9.4) -----------------------------------------------
_tone = st.text(min_size=1, max_size=30).filter(lambda s: bool(s.strip()))


# Feature: landing-and-design-system, Property 13: El prompt del copy refleja la voz de marca
@settings(max_examples=150, deadline=None)
@given(
    tone=_tone,
    site_config=_site_config(all_blank=True, all_enabled=True, min_sections=1),
)
def test_enrich_landing_prompt_reflects_brand_voice(tone, site_config):
    """Todo prompt construido por `enrich_landing` contiene el tono de marca.

    Para todo valor de `voice.tone`, cada prompt que `enrich_landing` arma para
    redactar copy incluye ese tono (Req 15.3). Se capturan los prompts con un
    proveedor que los registra; se garantiza al menos un prompt generando solo
    secciones activas con copy vacio.

    Validates: Requirements 15.3
    """
    provider = _RecordingProvider()
    voice = {"tone": tone}

    with mock.patch.object(
        generate_content, "get_provider", return_value=provider
    ):
        generate_content.enrich_landing(site_config, {}, voice)

    assert provider.prompts, "se esperaba al menos un prompt construido"
    expected_tone = tone.strip()
    for prompt in provider.prompts:
        assert expected_tone in prompt


# --- Property 14 (Tarea 9.5) -----------------------------------------------
@st.composite
def _site_config_with_failures(draw):
    """Site_Config schema-valido (copy vacio) mas el conjunto de tipos a fallar."""
    site_config = draw(
        _site_config(all_blank=True, all_enabled=False, min_sections=0)
    )
    fail_types = draw(st.sets(st.sampled_from(_TYPES)))
    return site_config, fail_types


# Feature: landing-and-design-system, Property 14: Robustez ante fallo del LLM por sección
@settings(max_examples=150, deadline=None)
@given(scenario=_site_config_with_failures())
def test_enrich_landing_robust_to_per_section_llm_failure(scenario):
    """Un fallo del LLM por seccion conserva su copy vacio y procesa el resto.

    Si la invocacion al LLM falla para algunas secciones, `enrich_landing`
    conserva el copy vacio de esas secciones, completa el de las secciones
    activas cuyo LLM responde, y devuelve un Site_Config conforme al esquema
    (Req 15.4, 15.5). Las secciones inactivas nunca se tocan.

    Validates: Requirements 15.4, 15.5
    """
    site_config, fail_types = scenario
    provider = _FailByType(fail_types)

    with mock.patch.object(
        generate_content, "get_provider", return_value=provider
    ):
        result = generate_content.enrich_landing(site_config, {}, None)

    # El resultado sigue siendo conforme a site-config.schema.json (Req 15.5).
    schemas.validate(result, "site-config")

    for section in result["landing"]:
        section_copy = _collect_copy(section)
        if not section.get("enabled"):
            # Inactiva: nunca se procesa; su copy vacio se conserva.
            for value in section_copy.values():
                assert _is_blank(value)
        elif section.get("type") in fail_types:
            # Activa pero el LLM falla: conserva el valor previo (vacio).
            for value in section_copy.values():
                assert _is_blank(value)
        else:
            # Activa y el LLM responde: el copy vacio queda completado.
            for value in section_copy.values():
                assert not _is_blank(value)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
