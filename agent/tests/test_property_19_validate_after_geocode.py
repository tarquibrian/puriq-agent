"""Property 19: el contrato se valida despues de geocode y antes de escribir/construir.

Ejercita el pipeline real de `Puriq.build()` (DD-1): carga tolerante del
`tourism-data.json` -> geocode -> comprobacion de coords accionable ->
validacion estricta -> build. Las fronteras externas (proveedor de geocode y el
ensamblado/build de Astro en `build_site.assemble`) se sustituyen por dobles de
prueba, por lo que no hay llamadas de red ni subprocesos reales.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from puriq import schemas
from puriq.core import CONFIG, DATA, THEME, Puriq
from puriq.tools import build_site, generate_content, geocode

# Documentos de Site_Config y Theme_Tokens validos y fijos: el foco de la
# propiedad es el `tourism-data`, que es el que pasa por geocode.
VALID_CONFIG = {
    "layout": "moderno",
    "modules": {"places": {"enabled": True, "order": 1}},
}
VALID_THEME = {
    "colors": {"primary": "#123456", "background": "#ffffff", "text": "#000000"},
    "typography": {"headingFont": "Inter", "bodyFont": "Inter"},
}

# Alfabeto imprimible simple para nombres/categorias (no vacios).
_TEXT = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),
    min_size=1,
    max_size=8,
)

# Un Place "borrador": todavia sin `coords` (solo `address`), como quedaria un
# documento editado a mano. `resolvable` decide si el geocode le asigna coords;
# `valid_id` decide si su id cumple el patron del esquema (para ejercitar tanto
# la comprobacion de coords como la validacion estructural estricta).
_place_spec = st.fixed_dictionaries(
    {
        "name": _TEXT,
        "category": _TEXT,
        "resolvable": st.booleans(),
        "valid_id": st.booleans(),
    }
)


class _FakeProvider:
    """Proveedor de geocode determinista: resuelve segun un mapa por direccion."""

    def __init__(self, resolve_map: dict[str, bool]):
        self._resolve_map = resolve_map

    def geocode(self, address: str) -> dict | None:
        if self._resolve_map.get(address):
            return {"lat": 10.0, "lng": 20.0}
        return None


def _build_raw_doc(specs: list[dict]) -> tuple[dict, dict[str, bool]]:
    """Construye un Tourism_Data borrador (Places con `address`, sin `coords`)."""
    site = {
        "name": "Sitio",
        "region": "Region",
        "defaultLocale": "es",
        "center": {"lat": 0.0, "lng": 0.0},
    }
    places: list[dict] = []
    resolve_map: dict[str, bool] = {}
    for i, spec in enumerate(specs):
        pid = f"p{i}" if spec["valid_id"] else f"BAD_{i}!"
        address = f"calle {i}"
        places.append(
            {
                "id": pid,
                "name": spec["name"],
                "category": spec["category"],
                "address": address,
            }
        )
        resolve_map[address] = spec["resolvable"]
    return {"site": site, "places": places}, resolve_map


# Feature: agent-tools, Property 19: El contrato se valida despues de geocode y antes de escribirse o construirse
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(specs=st.lists(_place_spec, max_size=5))
def test_contract_validated_after_geocode_and_before_build(specs, tmp_path_factory, monkeypatch):
    project: Path = tmp_path_factory.mktemp("proj")

    raw_doc, resolve_map = _build_raw_doc(specs)

    # Escribir el contrato en disco: el tourism-data es un borrador (sin coords).
    (project / DATA).write_text(schemas.dumps(raw_doc))
    (project / CONFIG).write_text(schemas.dumps(VALID_CONFIG))
    (project / THEME).write_text(schemas.dumps(VALID_THEME))

    # Doble de la frontera de geocode (sin red).
    monkeypatch.setattr(geocode, "get_provider", lambda: _FakeProvider(resolve_map))

    # Doble del ensamblado/build de Astro (sin subprocess). Es la unica via por
    # la que el pipeline escribe el contrato y corre el build; si no se invoca,
    # no hubo escritura ni build.
    assemble_mock = MagicMock(return_value=project / "dist")
    monkeypatch.setattr(build_site, "assemble", assemble_mock)

    puriq = Puriq(project)

    # El documento es apto solo si geocode completa TODAS las coords (todas las
    # direcciones resolubles) Y la estructura cumple el esquema (ids validos).
    all_coords = all(s["resolvable"] for s in specs)
    all_ids_ok = all(s["valid_id"] for s in specs)
    doc_valido = all_coords and all_ids_ok

    if doc_valido:
        puriq.build(use_llm=False)

        # El build (assemble) SI se invoca cuando el documento es valido.
        assert assemble_mock.called

        # El documento que llega al build fue validado DESPUES de geocode:
        # todas las coords estan presentes y cumple el esquema estricto.
        _project_arg, data_arg, config_arg, theme_arg = assemble_mock.call_args.args
        assert all("coords" in place for place in data_arg.get("places", []))
        schemas.validate(generate_content.contract_view(data_arg), "tourism-data")
        schemas.validate(config_arg, "site-config")
        schemas.validate(theme_arg, "theme-tokens")

        # La carga fue TOLERANTE: el tourism-data en disco (borrador, sin coords)
        # NO pasa la validacion estricta, pero el pipeline lo dejo pasar por
        # geocode y solo entonces valido. Comprobado cuando hubo Places borrador.
        if raw_doc["places"]:
            with pytest.raises(jsonschema.ValidationError):
                schemas.load(project / DATA, "tourism-data")
    else:
        # Un documento invalido (coords faltantes tras geocode o estructura que
        # no cumple el esquema) IMPIDE el build: assemble nunca se invoca.
        with pytest.raises((schemas.MissingCoordsError, jsonschema.ValidationError)):
            puriq.build(use_llm=False)
        assert not assemble_mock.called
