"""Prueba de propiedad para la validacion estricta antes de escribir el Contrato.

# Feature: landing-and-design-system, Property 4: Validacion estricta antes de toda escritura del Contrato

Property 4 (Validates: Requirements 1.5, 1.6, 13.3, 13.4, 14.4, 15.5): para todo
documento del Contrato (Site_Config o Theme_Tokens) que Puriq escribe, el
documento se persiste **si y solo si** cumple su esquema de `schemas/`; si es
invalido, no se escribe nada y se produce un `jsonschema.ValidationError`.

La ruta de escritura bajo prueba es `build_site._write_contract`, que valida los
tres documentos ANTES de escribir cualquier archivo. Se ejercita escribiendo en
un directorio de trabajo temporal, con `tourism-data` fijo y valido para que la
propiedad aisle la validez del `site.config.json` y del `theme.tokens.json`
(secciones `landing` invalidas y tokens ampliados invalidos).

Se usa Hypothesis (>= 100 iteraciones), una sola propiedad por prueba, etiquetada
con la propiedad del diseno. No se corre ningun build real de Astro ni npm:
`_write_contract` se invoca directamente.
"""
from __future__ import annotations

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from puriq.tools import build_site

# Catalogo de tipos de Landing_Section soportados (site-config.schema.json).
_LANDING_CATALOG = ["hero", "features", "cta", "gallery", "stats"]

# Tourism_Data fijo y valido: la propiedad aisla la validez de site-config y
# theme-tokens, por lo que el tercer documento nunca debe ser la causa de fallo.
VALID_DATA = {
    "site": {
        "name": "Sitio",
        "region": "Region",
        "defaultLocale": "es",
        "center": {"lat": 0.0, "lng": 0.0},
    },
    "places": [
        {"id": "p1", "name": "Lugar", "category": "cat", "coords": {"lat": 1.0, "lng": 2.0}}
    ],
}

# Valores CSS de ejemplo (strings) para los tokens ampliados validos.
_CSS_VALUE = st.sampled_from(["0.25rem", "1rem", "8px", "240ms", "1.5", "640px"])


# --------------------------------------------------------------------------- #
# Generadores de secciones Landing (site-config)                              #
# --------------------------------------------------------------------------- #
@st.composite
def _valid_landing_section(draw):
    """Seccion de portada conforme al esquema (type del catalogo, order >= 1)."""
    section = {
        "type": draw(st.sampled_from(_LANDING_CATALOG)),
        "enabled": draw(st.booleans()),
        "order": draw(st.integers(min_value=1, max_value=20)),
    }
    if draw(st.booleans()):
        section["content"] = draw(
            st.dictionaries(
                keys=st.text(min_size=1, max_size=5),
                values=st.text(max_size=10),
                max_size=3,
            )
        )
    return section


# Variantes de seccion Landing que INFRINGEN el esquema de site-config.
_INVALID_LANDING_SECTION = st.one_of(
    # type fuera del catalogo (enum).
    st.builds(
        lambda order, enabled: {"type": "carousel", "enabled": enabled, "order": order},
        order=st.integers(min_value=1, max_value=5),
        enabled=st.booleans(),
    ),
    # order < 1 (viola minimum: 1).
    st.builds(
        lambda t, enabled, order: {"type": t, "enabled": enabled, "order": order},
        t=st.sampled_from(_LANDING_CATALOG),
        enabled=st.booleans(),
        order=st.integers(max_value=0),
    ),
    # enabled no booleano (viola type: boolean).
    st.builds(
        lambda t, enabled, order: {"type": t, "enabled": enabled, "order": order},
        t=st.sampled_from(_LANDING_CATALOG),
        enabled=st.text(min_size=1, max_size=3),
        order=st.integers(min_value=1, max_value=5),
    ),
    # falta la propiedad requerida `order`.
    st.builds(
        lambda t, enabled: {"type": t, "enabled": enabled},
        t=st.sampled_from(_LANDING_CATALOG),
        enabled=st.booleans(),
    ),
    # propiedad adicional (viola additionalProperties: false).
    st.builds(
        lambda t, enabled, order, extra: {
            "type": t,
            "enabled": enabled,
            "order": order,
            "bogus": extra,
        },
        t=st.sampled_from(_LANDING_CATALOG),
        enabled=st.booleans(),
        order=st.integers(min_value=1, max_value=5),
        extra=st.text(max_size=3),
    ),
)


@st.composite
def _config(draw):
    """Genera un Site_Config valido o invalido y su etiqueta de validez.

    Base valida (`layout` + `modules`); opcionalmente agrega una `landing` valida.
    Para el caso invalido, intercala al menos una seccion que infringe el esquema.
    """
    config = {"layout": "moderno", "modules": {"places": {"enabled": True, "order": 1}}}
    valid = draw(st.booleans())
    if valid:
        if draw(st.booleans()):
            config["landing"] = draw(st.lists(_valid_landing_section(), max_size=4))
        return config, True

    invalid_sections = draw(st.lists(_INVALID_LANDING_SECTION, min_size=1, max_size=3))
    valid_sections = draw(st.lists(_valid_landing_section(), max_size=3))
    config["landing"] = draw(st.permutations(valid_sections + invalid_sections))
    return config, False


# --------------------------------------------------------------------------- #
# Generadores de Theme_Tokens                                                 #
# --------------------------------------------------------------------------- #
def _base_theme() -> dict:
    return {
        "colors": {"primary": "#123456", "background": "#ffffff", "text": "#000000"},
        "typography": {"headingFont": "Inter", "bodyFont": "Inter"},
    }


# Variantes de tokens ampliados que INFRINGEN el esquema de theme-tokens.
_INVALID_THEME_MUTATION = st.sampled_from(
    [
        {"spacing": {"md": 12}},  # spacing.* debe ser string, no numero.
        {"typeScale": {"h1": {"lineHeight": "1.2"}}},  # falta `size` (requerido).
        {"motion": {"bogus": "x"}},  # motion.additionalProperties: false.
        {"shadows": {"md": 4}},  # shadows.* debe ser string.
        {"container": {"lg": 1080}},  # container.* debe ser string.
        {"radii": {"sm": 4}},  # radii.* debe ser string.
    ]
)


@st.composite
def _theme(draw):
    """Genera un Theme_Tokens valido o invalido y su etiqueta de validez.

    Base valida (`colors` + `typography`); opcionalmente agrega tokens ampliados
    validos. Para el caso invalido, inserta un token ampliado mal tipado/formado.
    """
    theme = _base_theme()
    valid = draw(st.booleans())
    if valid:
        if draw(st.booleans()):
            theme["spacing"] = draw(
                st.dictionaries(
                    keys=st.sampled_from(["xs", "sm", "md", "lg", "xl"]),
                    values=_CSS_VALUE,
                    max_size=4,
                )
            )
        if draw(st.booleans()):
            theme["motion"] = {"durationBase": draw(_CSS_VALUE)}
        if draw(st.booleans()):
            theme["typeScale"] = {"h1": {"size": draw(_CSS_VALUE), "lineHeight": draw(_CSS_VALUE)}}
        return theme, True

    theme.update(draw(_INVALID_THEME_MUTATION))
    return theme, False


@st.composite
def _case(draw):
    """Combina un Site_Config y un Theme_Tokens (cada uno valido o invalido)."""
    config, config_valid = draw(_config())
    theme, theme_valid = draw(_theme())
    return config, config_valid, theme, theme_valid


# Feature: landing-and-design-system, Property 4: Validacion estricta antes de toda escritura del Contrato
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(case=_case())
def test_contract_written_iff_valid(case, tmp_path_factory):
    """El Contrato se escribe si y solo si cumple su esquema (Property 4).

    Con `tourism-data` fijo y valido, `_write_contract` escribe los tres
    documentos cuando tanto el Site_Config como el Theme_Tokens son validos; si
    alguno infringe su esquema, lanza `jsonschema.ValidationError` y NO deja
    escrito ningun archivo del Contrato.

    Validates: Requirements 1.5, 1.6, 13.3, 13.4, 14.4, 15.5
    """
    config, config_valid, theme, theme_valid = case
    expected_valid = config_valid and theme_valid

    work = tmp_path_factory.mktemp("work")
    data_dir = work / build_site.DATA_SUBDIR
    contract_files = [
        data_dir / build_site.DATA_FILENAME,
        data_dir / build_site.CONFIG_FILENAME,
        data_dir / build_site.THEME_FILENAME,
    ]

    if expected_valid:
        build_site._write_contract(work, VALID_DATA, config, theme)
        # Un Contrato valido se persiste: los tres documentos quedan escritos.
        for path in contract_files:
            assert path.exists(), f"Falta el documento del Contrato: {path.name}"
    else:
        # Un Contrato invalido se rechaza ANTES de escribir: error nombrando el
        # campo y ningun archivo del Contrato en disco.
        with pytest.raises(jsonschema.ValidationError):
            build_site._write_contract(work, VALID_DATA, config, theme)
        for path in contract_files:
            assert not path.exists(), f"No debio escribirse {path.name} con Contrato invalido"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
