"""Pruebas de propiedad de las intake tools (spec conversational-intake-mcp).

Este archivo implementa las 14 propiedades de correctitud definidas en la
sección "Correctness Properties" del diseño, sobre las funciones de
`agent/puriq/intake/tools.py` (y `run_intake_tool` cuando aplica). Cada prueba:

  - usa **Hypothesis** con un mínimo de 100 iteraciones (`@settings(max_examples>=100)`),
  - opera sobre un **directorio de proyecto temporal** aislado por ejemplo
    (subdirectorio único bajo `tmp_path`) para no cruzar E/S entre ejemplos,
  - lleva el comentario de trazabilidad
    `# Feature: conversational-intake-mcp, Property {N}: {texto}`.

Las estrategias generan nombres unicode y con espacios (para ejercitar
`slugify`), coordenadas dentro/fuera de rango, selecciones ordenadas de
módulos/secciones (con claves fuera de catálogo y repetidas), colores hex
válidos e inválidos, entradas de QA con y sin espacios, nombres de archivo con
extensiones soportadas/no soportadas, payloads alrededor de `MAX_ASSET_BYTES`, y
contratos parciales/completos para `get_state`.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from puriq import config
from puriq.intake import tools
from puriq.tools._slug import slugify
from puriq.wizard import contracts
from puriq.wizard.asset_store import append_image
from puriq.wizard.assets import IMAGE_EXTS, MAX_ASSET_BYTES
from puriq.wizard.landing import LANDING_CATALOG
from puriq.wizard.modules import MODULE_CATALOG

# --- Claves de documento del contrato (mismas que contracts._DOC_FILES) -------
_TOURISM = "tourism-data"
_CONFIG = "site-config"
_THEME = "theme-tokens"

_CONTRACT_FILES = (contracts.DATA, contracts.CONFIG, contracts.THEME)

#: Patrón Slug que deben cumplir ids y stems de nombres de archivo generados.
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

#: Configuración común de PBT: >=100 iteraciones, sin deadline (E/S en tmp), y
#: se suprime el health-check de fixture de función (usamos `tmp_path` como raíz
#: y creamos un subdirectorio único por ejemplo, así que el aislamiento es real).
pbt = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- Helpers de proyecto temporal --------------------------------------------
def _init_project(project: Path) -> Path:
    """Escribe un contrato base válido (los 3 JSON) en `project`.

    El `tourism-data` arranca con una identidad de sitio válida y sin lugares;
    `site-config` con layout y sin módulos; `theme-tokens` con los colores
    marcadores por defecto. Es el punto de partida realista sobre el que operan
    las intake tools de escritura.
    """
    tourism = {
        "site": {
            "name": "Potosí",
            "region": "Potosí",
            "defaultLocale": "es",
            "center": {"lat": -19.58, "lng": -65.75},
        },
        "places": [],
    }
    site_config = {"layout": "clasico", "modules": {}}
    theme = {
        "colors": {"primary": "#000000", "background": "#ffffff", "text": "#111111"},
        "typography": {"headingFont": "Inter", "bodyFont": "Inter"},
    }
    (project / contracts.DATA).write_text(
        json.dumps(tourism, ensure_ascii=False), encoding="utf-8"
    )
    (project / contracts.CONFIG).write_text(
        json.dumps(site_config, ensure_ascii=False), encoding="utf-8"
    )
    (project / contracts.THEME).write_text(
        json.dumps(theme, ensure_ascii=False), encoding="utf-8"
    )
    return project


def _new_project(tmp_path: Path) -> Path:
    """Crea un subdirectorio único bajo `tmp_path` con un contrato base inicial.

    Aísla la E/S por ejemplo de Hypothesis: aunque `tmp_path` (fixture de
    función) se crea una sola vez, cada ejemplo obtiene su propio directorio.
    """
    project = Path(tempfile.mkdtemp(dir=tmp_path))
    return _init_project(project)


def _snapshot_contract(project: Path) -> dict[str, bytes | None]:
    """Devuelve los bytes crudos de los 3 archivos del contrato (o None si faltan)."""
    snap: dict[str, bytes | None] = {}
    for fname in _CONTRACT_FILES:
        path = project / fname
        snap[fname] = path.read_bytes() if path.exists() else None
    return snap


def _is_error_response(result: object) -> bool:
    """Indica si `result` es una respuesta de error accionable de `run_intake_tool`.

    `wizard_error_response` devuelve `{causa, accion}` para errores generales o
    `{documento, campo, sugerencia}` para errores de esquema.
    """
    return isinstance(result, dict) and (
        "causa" in result or "sugerencia" in result or "campo" in result
    )


# --- Estrategias reutilizables -----------------------------------------------
_ASCII_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@st.composite
def _sluggable_name(draw) -> str:
    """Genera un nombre unicode/con espacios cuyo `slugify` es no vacío.

    Garantiza al menos un fragmento ASCII alfanumérico (para que el slug cumpla
    `^[a-z0-9-]+$`), pero lo rodea de texto unicode arbitrario y espacios para
    ejercitar la normalización de `slugify`.
    """
    core = draw(st.text(alphabet=_ASCII_ALNUM, min_size=1, max_size=12))
    prefix = draw(st.text(max_size=8))
    suffix = draw(st.text(max_size=8))
    name = f"{prefix} {core} {suffix}"
    # Por construcción el slug es no vacío; se afirma como red de seguridad.
    assume(slugify(name) != "")
    return name


@st.composite
def _sluggable_filestem(draw) -> str:
    """Como `_sluggable_name` pero sin separadores de ruta, apto para `<stem>.ext`.

    Excluye `/` y `\\` para que el stem no se confunda con componentes de
    directorio al construir el nombre de archivo; conserva unicode y espacios
    para ejercitar `slugify`.
    """
    name = draw(_sluggable_name())
    cleaned = name.replace("/", " ").replace("\\", " ")
    assume(slugify(cleaned) != "")
    return cleaned


_category = st.text(min_size=1, max_size=12)

_valid_lat = st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False)
_valid_lng = st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False)


@st.composite
def _valid_hex(draw) -> str:
    """Color hexadecimal válido `#RGB` o `#RRGGBB`."""
    digits = "0123456789abcdefABCDEF"
    length = draw(st.sampled_from([3, 6]))
    body = "".join(draw(st.sampled_from(list(digits))) for _ in range(length))
    return "#" + body


_INVALID_HEX = st.sampled_from(
    ["notacolor", "#12", "#1234", "#gg0011", "123456", "#xyzxyz", "", "rgb(0,0,0)"]
)

_nonblank_text = st.text(min_size=1, max_size=30).filter(lambda s: s.strip() != "")


# =============================================================================
# Property 1 (task 10.3)
# =============================================================================
@st.composite
def _valid_write_op(draw) -> dict:
    """Genera la descripción de una operación de escritura válida de intake."""
    kind = draw(
        st.sampled_from(
            [
                "set_site",
                "add_place",
                "add_event",
                "configure_modules",
                "configure_landing",
                "set_brand",
            ]
        )
    )
    op: dict = {"kind": kind}
    if kind == "set_site":
        op["name"] = draw(_sluggable_name())
        op["region"] = draw(_sluggable_name())
        op["lat"] = draw(_valid_lat)
        op["lng"] = draw(_valid_lng)
        op["zoom"] = draw(st.one_of(st.none(), st.integers(min_value=0, max_value=22)))
    elif kind == "add_place":
        op["name"] = draw(_sluggable_name())
        op["category"] = draw(_category)
        op["lat"] = draw(_valid_lat)
        op["lng"] = draw(_valid_lng)
    elif kind == "add_event":
        op["name"] = draw(_sluggable_name())
        op["start_date"] = "2024-01-01"
    elif kind == "configure_modules":
        keys = draw(
            st.lists(
                st.sampled_from(MODULE_CATALOG),
                min_size=1,
                max_size=len(MODULE_CATALOG),
                unique=True,
            )
        )
        op["selection"] = [{"key": k} for k in keys]
    elif kind == "configure_landing":
        types = draw(st.lists(st.sampled_from(LANDING_CATALOG), min_size=1, max_size=5))
        op["selection"] = [{"type": t} for t in types]
    elif kind == "set_brand":
        keys = draw(
            st.lists(
                st.sampled_from(["primary", "background", "text"]),
                min_size=1,
                max_size=3,
                unique=True,
            )
        )
        op["colors"] = {k: draw(_valid_hex()) for k in keys}
    return op


def _apply_write_op(project: Path, op: dict) -> tuple[dict, str]:
    """Ejecuta la operación descrita y devuelve `(respuesta, doc_afectado)`."""
    kind = op["kind"]
    if kind == "set_site":
        r = tools.set_site(
            project,
            name=op["name"],
            region=op["region"],
            center_lat=op["lat"],
            center_lng=op["lng"],
            center_zoom=op["zoom"],
        )
        return r, _TOURISM
    if kind == "add_place":
        r = tools.add_place(
            project, name=op["name"], category=op["category"], lat=op["lat"], lng=op["lng"]
        )
        return r, _TOURISM
    if kind == "add_event":
        r = tools.add_event(project, name=op["name"], start_date=op["start_date"])
        return r, _TOURISM
    if kind == "configure_modules":
        return tools.configure_modules(project, selection=op["selection"]), _CONFIG
    if kind == "configure_landing":
        return tools.configure_landing(project, selection=op["selection"]), _CONFIG
    if kind == "set_brand":
        return tools.set_brand(project, colors=op["colors"]), _THEME
    raise AssertionError(f"kind desconocido: {kind}")


# Feature: conversational-intake-mcp, Property 1: Una escritura exitosa devuelve el estado persistido
@pbt
@given(op=_valid_write_op())
def test_p1_write_returns_persisted_state(tmp_path, op):
    """El documento devuelto por una escritura == documento cargado tras la op.

    Validates: Requirements 1.4, 3.1, 3.7, 4.5, 6.7, 8.5, 9.4
    """
    project = _new_project(tmp_path)
    response, doc = _apply_write_op(project, op)

    assert "document" in response
    loaded = contracts._load_contract(project, doc)
    # "Igual salvo redacción": ambos lados pasan por la misma redacción.
    assert response["document"] == config.redact_value(loaded)


# =============================================================================
# Property 2 (task 3.5)
# =============================================================================
# Feature: conversational-intake-mcp, Property 2: Agregar preserva las entradas preexistentes (aditividad)
@pbt
@given(
    kind=st.sampled_from(["places", "events", "qa"]),
    names=st.lists(_sluggable_name(), min_size=1, max_size=5, unique_by=slugify),
    # Las Q&A se generan DISTINTAS entre sí según el criterio de deduplicación de
    # `qa_store.append_qa_entry` (pregunta recortada e insensible a
    # mayúsculas/minúsculas + respuesta recortada): la aditividad se afirma sobre
    # entradas nuevas, y una entrada repetida es idempotente por diseño (se cubre
    # en `tests/test_relocation_parity.py`).
    qa=st.lists(
        st.tuples(_nonblank_text, _nonblank_text),
        min_size=1,
        max_size=5,
        unique_by=lambda par: (par[0].strip().casefold(), par[1].strip()),
    ),
)
def test_p2_add_preserves_existing(tmp_path, kind, names, qa):
    """Agregar una entrada nueva conserva todas las previas, en orden, sin borrar.

    Validates: Requirements 5.6, 6.3, 10.3
    """
    project = _new_project(tmp_path)

    if kind == "places":
        expected_ids: list[str] = []
        for name in names:
            tools.add_place(project, name=name, category="c", lat=-19.5, lng=-65.7)
            expected_ids.append(slugify(name))
            places = contracts._load_contract(project, _TOURISM).get("places", [])
            assert [p["id"] for p in places] == expected_ids
    elif kind == "events":
        expected_ids = []
        for name in names:
            tools.add_event(project, name=name, start_date="2024-01-01")
            expected_ids.append(slugify(name))
            events = contracts._load_contract(project, _TOURISM).get("events", [])
            assert [e["id"] for e in events] == expected_ids
    else:  # qa
        expected: list[dict] = []
        for question, answer in qa:
            tools.add_qa(project, question=question, answer=answer)
            expected.append({"question": question.strip(), "answer": answer.strip()})
            stored = json.loads(
                (project / "content" / "qa.json").read_text(encoding="utf-8")
            )
            assert stored == expected


# =============================================================================
# Property 3 (task 3.6)
# =============================================================================
# Feature: conversational-intake-mcp, Property 3: Un lugar con solo dirección se persiste como borrador sin inventar coordenadas
@pbt
@given(name=_sluggable_name(), category=_category, address=_nonblank_text)
def test_p3_place_with_address_only_is_draft_without_coords(tmp_path, name, category, address):
    """Un Place con solo dirección se persiste sin `coords` y conserva la dirección.

    Validates: Requirements 1.5, 5.3
    """
    project = _new_project(tmp_path)
    tools.add_place(project, name=name, category=category, address=address)

    places = contracts._load_contract(project, _TOURISM)["places"]
    place = next(p for p in places if p["id"] == slugify(name))
    assert "coords" not in place
    assert place["address"] == address.strip()


# =============================================================================
# Property 4 (task 7.2)
# =============================================================================
# Feature: conversational-intake-mcp, Property 4: get_state es de solo lectura
@pbt
@given(
    n_places=st.integers(min_value=0, max_value=4),
    names=st.lists(_sluggable_name(), min_size=0, max_size=4, unique_by=slugify),
)
def test_p4_get_state_is_read_only(tmp_path, n_places, names):
    """Invocar `get_state` deja los tres archivos del contrato byte a byte iguales.

    Validates: Requirements 2.1
    """
    project = _new_project(tmp_path)
    # Poblar algo de contenido variado antes del snapshot.
    for name in names[:n_places]:
        tools.add_place(project, name=name, category="c", lat=-19.5, lng=-65.7)

    before = _snapshot_contract(project)
    tools.get_state(project)
    after = _snapshot_contract(project)
    assert before == after


# =============================================================================
# Property 5 (task 7.3)
# =============================================================================
def _write_state_p5(project: Path, flags: dict) -> None:
    """Escribe un contrato en un estado arbitrario según `flags` (para `missing`)."""
    site = {"defaultLocale": "es"}
    site["name"] = "" if flags["name_blank"] else "Potosí Ciudad"
    site["region"] = "" if flags["region_blank"] else "Potosí"
    site["center"] = {"lat": 0, "lng": 0} if flags["center_default"] else {"lat": -19.58, "lng": -65.75}
    places = (
        []
        if flags["no_places"]
        else [{"id": "p1", "name": "P", "category": "c", "coords": {"lat": -19.5, "lng": -65.7}}]
    )
    tourism = {"site": site, "places": places}

    if flags["no_modules"]:
        modules = {"places": {"enabled": False, "order": 1}} if flags["disabled_variant"] else {}
    else:
        modules = {"places": {"enabled": True, "order": 1}}
    site_config = {"layout": "clasico", "modules": modules}

    if flags["brand_default"]:
        colors = {"primary": "#000000", "background": "#ffffff", "text": "#111111"}
    else:
        colors = {"primary": "#123456", "background": "#abcdef", "text": "#0f0f0f"}
    theme = {"colors": colors, "typography": {"headingFont": "Inter", "bodyFont": "Inter"}}

    (project / contracts.DATA).write_text(json.dumps(tourism, ensure_ascii=False), encoding="utf-8")
    (project / contracts.CONFIG).write_text(json.dumps(site_config, ensure_ascii=False), encoding="utf-8")
    (project / contracts.THEME).write_text(json.dumps(theme, ensure_ascii=False), encoding="utf-8")


# Feature: conversational-intake-mcp, Property 5: missing refleja exactamente las piezas requeridas ausentes
@pbt
@given(
    name_blank=st.booleans(),
    region_blank=st.booleans(),
    center_default=st.booleans(),
    no_modules=st.booleans(),
    disabled_variant=st.booleans(),
    no_places=st.booleans(),
    brand_default=st.booleans(),
)
def test_p5_missing_reflects_absent_pieces(
    tmp_path, name_blank, region_blank, center_default, no_modules, disabled_variant, no_places, brand_default
):
    """`missing` contiene una pieza si y solo si está ausente o es su marcador base.

    Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
    """
    project = Path(tempfile.mkdtemp(dir=tmp_path))
    flags = {
        "name_blank": name_blank,
        "region_blank": region_blank,
        "center_default": center_default,
        "no_modules": no_modules,
        "disabled_variant": disabled_variant,
        "no_places": no_places,
        "brand_default": brand_default,
    }
    _write_state_p5(project, flags)

    result = tools.get_state(project)
    actual = {(m["piece"], m["field"]) for m in result["missing"]}

    expected: set = set()
    if name_blank:
        expected.add(("site", "name"))
    if region_blank:
        expected.add(("site", "region"))
    if center_default:
        expected.add(("site", "center"))
    if no_modules:
        expected.add(("modules", None))
    if no_places:
        expected.add(("places", None))
    if brand_default:
        expected.add(("brand", "colors"))

    assert actual == expected


# =============================================================================
# Property 6 (task 10.5)
# =============================================================================
_secret_value = st.text(alphabet=_ASCII_ALNUM, min_size=12, max_size=30)


@contextlib.contextmanager
def _registered_secret(value: str):
    """Registra `value` como secreto (vía `config.get_env(secret=True)`) y restaura."""
    name = "PURIQ_TEST_SECRET_PROP6"
    saved_env = os.environ.get(name)
    saved_names = set(config._secret_names)
    os.environ[name] = value
    config.get_env(name, secret=True)
    try:
        yield
    finally:
        config._secret_names = saved_names
        if saved_env is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = saved_env


# Feature: conversational-intake-mcp, Property 6: Ningún valor de secreto aparece en la salida de una tool
@pbt
@given(secret=_secret_value, name=_sluggable_name(), path_kind=st.sampled_from(["success", "error"]))
def test_p6_no_secret_in_tool_output(tmp_path, secret, name, path_kind):
    """La salida (éxito o error) de una tool no contiene el valor crudo del secreto.

    Validates: Requirements 2.8, 14.5
    """
    project = _new_project(tmp_path)
    with _registered_secret(secret):
        if path_kind == "success":
            # El secreto viaja verbatim en un campo persistido (site.region).
            response = tools.run_intake_tool(
                "set_site",
                {
                    "project": str(project),
                    "name": name,
                    "region": secret,
                    "center": {"lat": -19.58, "lng": -65.75},
                },
            )
        else:
            # El secreto viaja en un mensaje de error (source_path inexistente).
            response = tools.run_intake_tool(
                "attach_asset",
                {
                    "project": str(project),
                    "filename": "x.png",
                    "source_path": secret,
                    "target": "place",
                    "id": "no-such-id",
                },
            )
        serialized = json.dumps(response, ensure_ascii=False)
        assert secret not in serialized


# =============================================================================
# Property 7 (task 3.4)
# =============================================================================
# Feature: conversational-intake-mcp, Property 7: Los id y nombres de archivo generados cumplen el patrón slug
@pbt
@given(place_name=_sluggable_name(), event_name=_sluggable_name(), file_stem=_sluggable_filestem())
def test_p7_generated_ids_and_filenames_are_slugs(tmp_path, place_name, event_name, file_stem):
    """Ids de Place/Event y stem de asset cumplen `^[a-z0-9-]+$` y coinciden con `slugify`.

    Validates: Requirements 5.1, 6.1, 11.1, 14.6
    """
    project = _new_project(tmp_path)

    # Place id.
    tools.add_place(project, name=place_name, category="c", lat=-19.5, lng=-65.7)
    place_id = slugify(place_name)
    places = contracts._load_contract(project, _TOURISM)["places"]
    assert any(p["id"] == place_id for p in places)
    assert _SLUG_RE.match(place_id)

    # Event id.
    tools.add_event(project, name=event_name, start_date="2024-01-01")
    event_id = slugify(event_name)
    events = contracts._load_contract(project, _TOURISM)["events"]
    assert any(e["id"] == event_id for e in events)
    assert _SLUG_RE.match(event_id)

    # Nombre de archivo de asset: stem generado.
    filename = f"{file_stem}.png"
    content = base64.b64encode(b"fake-image-bytes").decode("ascii")
    result = tools.attach_asset(
        project,
        filename=filename,
        content_base64=content,
        target="place",
        id=place_id,
    )
    stem = result["path"].split("/")[-1].rsplit(".", 1)[0]
    assert _SLUG_RE.match(stem)
    assert stem == slugify(file_stem)


# =============================================================================
# Property 8 (task 10.4) and Property 14 (task 10.6) share rejected operations.
# =============================================================================
_REJECTED_KINDS = [
    "place_out_of_range",
    "place_single_coord",
    "modules_bad_key",
    "modules_repeated",
    "landing_bad_type",
    "bad_domain",
    "bad_hex",
    "qa_empty",
    "asset_bad_ext",
    "asset_oversized",
    "asset_bad_id",
    "edit_bad_id",
    "remove_bad_id",
]


@st.composite
def _rejected_spec(draw) -> dict:
    """Genera la descripción de una operación que la tool debe rechazar."""
    kind = draw(st.sampled_from(_REJECTED_KINDS))
    spec: dict = {"kind": kind}
    spec["name"] = draw(_sluggable_name())
    spec["category"] = draw(_category)
    if kind == "place_out_of_range":
        spec["lat"] = draw(st.floats(min_value=90.001, max_value=1000, allow_nan=False, allow_infinity=False))
    if kind == "bad_hex":
        spec["hex"] = draw(_INVALID_HEX)
    return spec


_EXISTING_ID = "cerro-rico"
_NONEXISTENT_ID = "no-such-id-zzz"
_SMALL_B64 = base64.b64encode(b"fake-image-bytes").decode("ascii")


def _build_rejected(project: Path, spec: dict) -> tuple[str, dict]:
    """Construye `(tool, arguments)` para `run_intake_tool` a partir de `spec`."""
    kind = spec["kind"]
    p = str(project)
    valid_center = {"lat": -19.58, "lng": -65.75}
    if kind == "place_out_of_range":
        return "add_place", {"project": p, "name": spec["name"], "category": spec["category"], "lat": spec["lat"], "lng": 0.0}
    if kind == "place_single_coord":
        return "add_place", {"project": p, "name": spec["name"], "category": spec["category"], "lat": 10.0}
    if kind == "modules_bad_key":
        return "configure_modules", {"project": p, "selection": [{"key": "__not_a_module__"}]}
    if kind == "modules_repeated":
        return "configure_modules", {"project": p, "selection": [{"key": "map"}, {"key": "map"}]}
    if kind == "landing_bad_type":
        return "configure_landing", {"project": p, "selection": [{"type": "__not_a_type__"}]}
    if kind == "bad_domain":
        return "set_site", {"project": p, "name": spec["name"], "region": "Potosí", "center": valid_center, "domain": "no es un dominio!!"}
    if kind == "bad_hex":
        return "set_brand", {"project": p, "colors": {"primary": spec["hex"]}}
    if kind == "qa_empty":
        return "add_qa", {"project": p, "question": "   ", "answer": "respuesta ok"}
    if kind == "asset_bad_ext":
        return "attach_asset", {"project": p, "filename": "documento.txt", "content_base64": _SMALL_B64, "target": "place", "id": _EXISTING_ID}
    if kind == "asset_oversized":
        big = base64.b64encode(b"\0" * (MAX_ASSET_BYTES + 1)).decode("ascii")
        return "attach_asset", {"project": p, "filename": "grande.png", "content_base64": big, "target": "place", "id": _EXISTING_ID}
    if kind == "asset_bad_id":
        return "attach_asset", {"project": p, "filename": "foto.png", "content_base64": _SMALL_B64, "target": "place", "id": _NONEXISTENT_ID}
    if kind == "edit_bad_id":
        return "edit_item", {"project": p, "id": _NONEXISTENT_ID, "fields": {"category": "x"}}
    if kind == "remove_bad_id":
        return "remove_item", {"project": p, "id": _NONEXISTENT_ID}
    raise AssertionError(f"kind desconocido: {kind}")


def _project_with_place(tmp_path: Path) -> Path:
    """Proyecto base con un Place conocido (`cerro-rico`) ya cargado."""
    project = _new_project(tmp_path)
    tools.add_place(project, name="Cerro Rico", category="montaña", lat=-19.62, lng=-65.75)
    return project


# Feature: conversational-intake-mcp, Property 8: Una operación rechazada deja el contrato persistido sin cambios
@pbt
@given(spec=_rejected_spec())
def test_p8_rejected_op_leaves_contract_unchanged(tmp_path, spec):
    """Una operación rechazada deja los tres archivos del contrato byte a byte iguales.

    Validates: Requirements 3.4, 3.6, 4.3, 4.4, 5.4, 5.5, 8.2, 9.3, 10.2,
               11.2, 11.3, 11.4, 11.6, 14.2, 14.3
    """
    project = _project_with_place(tmp_path)
    tool, arguments = _build_rejected(project, spec)

    before = _snapshot_contract(project)
    result = tools.run_intake_tool(tool, arguments)
    after = _snapshot_contract(project)

    assert _is_error_response(result), f"se esperaba rechazo, se obtuvo: {result!r}"
    assert before == after


# Feature: conversational-intake-mcp, Property 14: Todo error se traduce a una respuesta accionable
@pbt
@given(spec=_rejected_spec())
def test_p14_errors_translate_to_actionable_response(tmp_path, spec):
    """`run_intake_tool` traduce todo error a una respuesta accionable (causa/acción o documento/campo/sugerencia).

    Validates: Requirements 14.4
    """
    project = _project_with_place(tmp_path)
    tool, arguments = _build_rejected(project, spec)

    result = tools.run_intake_tool(tool, arguments)

    assert isinstance(result, dict)
    schema_shape = "documento" in result and "campo" in result and "sugerencia" in result
    general_shape = "causa" in result and "accion" in result
    assert schema_shape or general_shape, f"respuesta no accionable: {result!r}"


# =============================================================================
# Property 9 (task 3.3)
# =============================================================================
# Feature: conversational-intake-mcp, Property 9: El order asignado es 1..n coherente con el orden de la selección
@pbt
@given(
    kind=st.sampled_from(["modules", "landing"]),
    module_keys=st.lists(st.sampled_from(MODULE_CATALOG), min_size=1, max_size=len(MODULE_CATALOG), unique=True),
    landing_types=st.lists(st.sampled_from(LANDING_CATALOG), min_size=1, max_size=6),
)
def test_p9_order_is_one_to_n(tmp_path, kind, module_keys, landing_types):
    """El `order` asignado es 1..n estrictamente creciente y coherente con la posición.

    Validates: Requirements 4.2, 9.2
    """
    project = _new_project(tmp_path)
    if kind == "modules":
        selection = [{"key": k} for k in module_keys]
        tools.configure_modules(project, selection=selection)
        modules = contracts._load_contract(project, _CONFIG)["modules"]
        for position, key in enumerate(module_keys, start=1):
            assert modules[key]["order"] == position
    else:
        selection = [{"type": t} for t in landing_types]
        tools.configure_landing(project, selection=selection)
        landing = contracts._load_contract(project, _CONFIG)["landing"]
        orders = [section["order"] for section in landing]
        assert orders == list(range(1, len(landing_types) + 1))


# =============================================================================
# Property 10 (task 5.3)
# =============================================================================
_EDITABLE_FIELDS = ["category", "address", "shortDescription", "hours"]


# Feature: conversational-intake-mcp, Property 10: edit_item cambia solo los campos indicados y preserva el resto
@pbt
@given(
    name=_sluggable_name(),
    category=_category,
    address=_nonblank_text,
    edits=st.dictionaries(
        keys=st.sampled_from(_EDITABLE_FIELDS),
        values=st.text(min_size=1, max_size=20),
        min_size=1,
        max_size=4,
    ),
)
def test_p10_edit_changes_only_indicated_fields(tmp_path, name, category, address, edits):
    """`edit_item` actualiza solo los campos indicados, preserva el resto y no regenera el id.

    Validates: Requirements 7.1
    """
    project = _new_project(tmp_path)
    tools.add_place(project, name=name, category=category, lat=-19.5, lng=-65.7, address=address)
    place_id = slugify(name)

    original = next(
        p for p in contracts._load_contract(project, _TOURISM)["places"] if p["id"] == place_id
    )

    tools.edit_item(project, id=place_id, fields=edits)

    edited = next(
        p for p in contracts._load_contract(project, _TOURISM)["places"] if p["id"] == place_id
    )
    expected = {**original, **edits}
    expected["id"] = place_id  # el id nunca se regenera
    assert edited == expected


# =============================================================================
# Property 11 (task 6.3)
# =============================================================================
# Feature: conversational-intake-mcp, Property 11: Operar sobre un id inexistente se rechaza como "no encontrado"
@pbt
@given(
    tool=st.sampled_from(["edit_item", "remove_item", "attach_asset"]),
    missing_id=st.sampled_from(["no-such-id-zzz", "fantasma-123", "inexistente"]),
)
def test_p11_operation_on_missing_id_is_rejected(tmp_path, tool, missing_id):
    """`edit_item`/`remove_item`/`attach_asset` sobre un id inexistente se rechazan sin tocar el contrato.

    Validates: Requirements 7.3, 11.6
    """
    project = _project_with_place(tmp_path)
    assume(missing_id != _EXISTING_ID)

    if tool == "edit_item":
        arguments = {"project": str(project), "id": missing_id, "fields": {"category": "x"}}
    elif tool == "remove_item":
        arguments = {"project": str(project), "id": missing_id}
    else:
        arguments = {
            "project": str(project),
            "filename": "foto.png",
            "content_base64": _SMALL_B64,
            "target": "place",
            "id": missing_id,
        }

    before = _snapshot_contract(project)
    result = tools.run_intake_tool(tool, arguments)
    after = _snapshot_contract(project)

    assert _is_error_response(result)
    causa = (result.get("causa") or result.get("sugerencia") or "").lower()
    assert "encontr" in causa or "existe" in causa
    assert before == after


# =============================================================================
# Property 12 (task 5.4)
# =============================================================================
# Feature: conversational-intake-mcp, Property 12: Eliminar un lugar referenciado no deja referencias colgantes
@pbt
@given(
    place_name=_sluggable_name(),
    event_names=st.lists(_sluggable_name(), min_size=1, max_size=4, unique_by=slugify),
    refs=st.lists(st.booleans(), min_size=1, max_size=4),
)
def test_p12_removing_referenced_place_clears_dangling_refs(tmp_path, place_name, event_names, refs):
    """Tras `remove_item` de un Place referenciado, ningún Event conserva ese `placeId`.

    Validates: Requirements 7.4
    """
    project = _new_project(tmp_path)
    place_id = slugify(place_name)
    tools.add_place(project, name=place_name, category="c", lat=-19.5, lng=-65.7)

    # Alinear refs a la cantidad de eventos y garantizar al menos una referencia.
    refs = (refs + [False] * len(event_names))[: len(event_names)]
    if not any(refs):
        refs[0] = True

    for name, ref in zip(event_names, refs):
        tools.add_event(
            project,
            name=name,
            start_date="2024-01-01",
            place_id=place_id if ref else None,
        )

    tools.remove_item(project, id=place_id)

    tourism = contracts._load_contract(project, _TOURISM)
    assert all(p["id"] != place_id for p in tourism.get("places", []))
    assert all(e.get("placeId") != place_id for e in tourism.get("events", []))


# =============================================================================
# Property 13 (task 6.2)
# =============================================================================
# Feature: conversational-intake-mcp, Property 13: Asociar una imagen es aditivo e idempotente
@pbt
@given(
    place_name=_sluggable_name(),
    stems=st.lists(_sluggable_filestem(), min_size=2, max_size=2, unique_by=slugify),
)
def test_p13_attach_asset_is_additive_and_idempotent(tmp_path, place_name, stems):
    """Asociar imágenes es aditivo (preserva las previas) e idempotente (no duplica).

    Validates: Requirements 11.5
    """
    project = _new_project(tmp_path)
    place_id = slugify(place_name)
    tools.add_place(project, name=place_name, category="c", lat=-19.5, lng=-65.7)

    # Primera imagen.
    r1 = tools.attach_asset(
        project, filename=f"{stems[0]}.png", content_base64=_SMALL_B64, target="place", id=place_id
    )
    path1 = r1["path"]
    place = next(p for p in contracts._load_contract(project, _TOURISM)["places"] if p["id"] == place_id)
    assert place["images"] == [path1]

    # Segunda imagen distinta: aditivo, preserva la primera.
    r2 = tools.attach_asset(
        project, filename=f"{stems[1]}.png", content_base64=_SMALL_B64, target="place", id=place_id
    )
    path2 = r2["path"]
    place = next(p for p in contracts._load_contract(project, _TOURISM)["places"] if p["id"] == place_id)
    assert place["images"] == [path1, path2]

    # Idempotencia de la asociación: re-asociar la misma ruta no la duplica.
    append_image(project, "places", place_id, path1)
    place = next(p for p in contracts._load_contract(project, _TOURISM)["places"] if p["id"] == place_id)
    assert place["images"] == [path1, path2]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
