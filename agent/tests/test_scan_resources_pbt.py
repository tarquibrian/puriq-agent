"""Pruebas de propiedad para scan_resources (spec agent-tools)."""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path al correr
# pytest desde cualquier directorio.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.tools import scan_resources  # noqa: E402


# --- Estrategias ------------------------------------------------------------

# Nombres cuyo strip() queda vacío: deben omitirse del resultado (Req 1.7).
_whitespace_names = st.sampled_from(["", " ", "   ", "\t", "  \t  ", "\t \t"])

# Nombres válidos: al menos un carácter no-espacio tras strip(). Excluimos
# caracteres de control y saltos de línea para evitar ambigüedades de round-trip
# en CSV; la propiedad bajo prueba es sobre vacío/espacios, no sobre el charset.
_valid_names = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cc", "Cs"),
        blacklist_characters="\r\n",
    ),
    min_size=1,
).filter(lambda s: s.strip() != "")

_names = st.one_of(_whitespace_names, _valid_names)
_name_lists = st.lists(_names, max_size=15)


def _write_csv(path: Path, fieldnames: list[str], names: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for name in names:
            writer.writerow({fieldnames[0]: name})


def _write_site_json(path: Path) -> None:
    path.write_text(
        json.dumps({"site": {"name": "T", "region": "R"}, "categories": []}),
        encoding="utf-8",
    )


# --- Propiedad --------------------------------------------------------------

# Feature: agent-tools, Property 2: Solo sobreviven filas con nombre no vacío
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(place_names=_name_lists, event_names=_name_lists)
def test_only_rows_with_nonempty_name_survive(place_names, event_names):
    """El resultado de scan_resources incluye exactamente las filas cuyo `name`
    no es vacío ni solo espacios, y ninguna con nombre vacío (Req 1.7)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        _write_csv(d / "places.csv", ["name"], place_names)
        _write_csv(d / "events.csv", ["name", "start_date"], event_names)

        result = scan_resources.run(d)

        expected_places = [n.strip() for n in place_names if n.strip()]
        expected_events = [n.strip() for n in event_names if n.strip()]

        # Sobreviven exactamente las filas con nombre no vacío, en orden.
        assert [p["name"] for p in result["places"]] == expected_places
        assert [e["name"] for e in result["events"]] == expected_events

        # Ninguna fila sobreviviente tiene nombre vacío o solo espacios.
        assert all(p["name"].strip() != "" for p in result["places"])
        assert all(e["name"].strip() != "" for e in result["events"])

        # El conteo coincide con las filas de entrada con nombre válido.
        assert len(result["places"]) == len(expected_places)
        assert len(result["events"]) == len(expected_events)


# --- Property 1 -------------------------------------------------------------

import re  # noqa: E402
import string  # noqa: E402

from puriq.tools._slug import slugify  # noqa: E402

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

# Nombres cuyo Slug es no vacío: al menos un carácter ASCII alfanumérico. Esto
# restringe la generación al espacio real de nombres de lugares/eventos (que
# siempre contienen letras o dígitos), donde el id derivado es un Slug válido.
_alnum = st.sampled_from(string.ascii_letters + string.digits)
_slug_free = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cc", "Cs"),
        blacklist_characters="\r\n",
    ),
    max_size=20,
)


@st.composite
def _sluggable_names(draw):
    return draw(_slug_free) + draw(_alnum) + draw(_slug_free)


_sluggable_lists = st.lists(_sluggable_names(), max_size=12)


# Feature: agent-tools, Property 1: Los ids son slugs bien formados derivados del nombre
# Validates: Requirements 1.6
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(place_names=st.lists(_sluggable_names(), min_size=1, max_size=12),
       event_names=_sluggable_lists)
def test_ids_are_wellformed_slugs_derived_from_name(place_names, event_names):
    """Para todo nombre de Place/Event, scan_resources genera un `id` igual a
    slugify(name) y que cumple el patrón `^[a-z0-9-]+$` (Req 1.6)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        _write_csv(d / "places.csv", ["name"], place_names)
        _write_csv(d / "events.csv", ["name", "start_date"], event_names)

        result = scan_resources.run(d)

        for place in result["places"]:
            assert place["id"] == slugify(place["name"])
            assert _SLUG_RE.match(place["id"]), f"id de place mal formado: {place['id']!r}"

        for event in result["events"]:
            assert event["id"] == slugify(event["name"])
            assert _SLUG_RE.match(event["id"]), f"id de event mal formado: {event['id']!r}"


# --- Property 5 -------------------------------------------------------------


@st.composite
def _events_config(draw):
    """Genera la configuración de events.csv para un directorio de recursos.

    Devuelve una tupla ``(has_events_csv, event_names)``:
      - ``has_events_csv`` indica si el directorio contendrá events.csv.
      - ``event_names`` es la lista de nombres a escribir (mezcla de válidos y
        vacíos/espacios); solo relevante cuando ``has_events_csv`` es True.

    Se cubren ambos lados de la propiedad: con archivo (eventos incluidos) y
    sin archivo (lista vacía).
    """
    has_events_csv = draw(st.booleans())
    event_names = draw(_name_lists)
    return has_events_csv, event_names


# Feature: agent-tools, Property 5: Los eventos se incluyen o quedan vacíos según exista events.csv
# Validates: Requirements 1.4, 1.5
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(place_names=_name_lists, events_config=_events_config())
def test_events_included_or_empty_by_events_csv(place_names, events_config):
    """Para todo directorio de recursos: si contiene events.csv, todos los
    eventos con `name` válido aparecen en `Tourism_Data.events`; si no lo
    contiene, `Tourism_Data.events` es una lista vacía (Req 1.4, 1.5)."""
    has_events_csv, event_names = events_config
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        _write_csv(d / "places.csv", ["name"], place_names)
        if has_events_csv:
            _write_csv(d / "events.csv", ["name", "start_date"], event_names)

        result = scan_resources.run(d)

        assert isinstance(result["events"], list)

        if has_events_csv:
            # Todos los eventos con nombre válido (no vacío ni solo espacios)
            # aparecen en el resultado, en orden.
            expected_events = [n.strip() for n in event_names if n.strip()]
            assert [e["name"] for e in result["events"]] == expected_events
        else:
            # Sin events.csv, la lista de eventos queda vacía.
            assert result["events"] == []


# --- Property 4 -------------------------------------------------------------


def _write_places_csv(path: Path, names: list[str]) -> None:
    """Escribe places.csv con solo la columna `name`."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name"])
        writer.writeheader()
        for name in names:
            writer.writerow({"name": name})


def _write_events_csv(path: Path, rows: list[tuple[str, str]]) -> None:
    """Escribe events.csv con columnas `name` y `place` (referencia cruda)."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "start_date", "place"])
        writer.writeheader()
        for name, place_ref in rows:
            writer.writerow({"name": name, "start_date": "", "place": place_ref})


@st.composite
def _place_names_and_events(draw):
    """Genera nombres de Places y filas de Events cuyo `place` referencia:

    - un nombre de Place existente,
    - un id (slug) de Place existente, o
    - una cadena arbitraria que puede o no corresponder a un Place.

    Así se ejercitan tanto referencias válidas como inexistentes (Req 1.10).
    """
    place_names = draw(st.lists(_sluggable_names(), min_size=1, max_size=8))
    place_ids = [slugify(n) for n in place_names]

    # Fuente de referencias: nombres reales, ids reales o texto arbitrario.
    ref_strategy = st.one_of(
        st.sampled_from(place_names) if place_names else st.just(""),
        st.sampled_from(place_ids) if place_ids else st.just(""),
        _slug_free,
        st.just(""),
    )
    event_rows = draw(
        st.lists(st.tuples(_sluggable_names(), ref_strategy), max_size=10)
    )
    return place_names, event_rows


# Feature: agent-tools, Property 4: Integridad referencial de eventos
# Validates: Requirements 1.10
@settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(_place_names_and_events())
def test_event_referential_integrity(data):
    """Todo `placeId` presente en un Event pertenece al conjunto de ids de Place
    del mismo documento; los Events cuyo `place` referencia un Place inexistente
    no reciben `placeId` (Req 1.10)."""
    place_names, event_rows = data
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        _write_places_csv(d / "places.csv", place_names)
        _write_events_csv(d / "events.csv", event_rows)

        result = scan_resources.run(d)

        place_id_set = {p["id"] for p in result["places"]}

        for event in result["events"]:
            if "placeId" in event:
                # Integridad referencial: el placeId apunta a un Place existente.
                assert event["placeId"] in place_id_set, (
                    f"placeId {event['placeId']!r} no está en {place_id_set}"
                )

        # Solo sobreviven las filas con nombre no vacío, en orden (Req 1.7), así
        # que las filas de entrada supervivientes se corresponden 1:1 con
        # result["events"]. Emparejamos para verificar cada referencia cruda.
        surviving_rows = [(n, ref) for n, ref in event_rows if n.strip()]
        assert len(surviving_rows) == len(result["events"])

        for (_, raw_ref), event in zip(surviving_rows, result["events"]):
            raw = raw_ref.strip()
            resolves = bool(raw) and (
                raw in place_id_set or slugify(raw) in place_id_set
            )
            if resolves:
                # La referencia válida se traduce a un placeId existente.
                assert event.get("placeId") in place_id_set
            else:
                # Referencia inexistente (o vacía): no debe producir placeId.
                assert "placeId" not in event, (
                    f"Event {event['id']!r} con referencia inexistente {raw!r} "
                    f"no debería tener placeId"
                )


# --- Property 3 -------------------------------------------------------------


def _write_places_with_coords(path: Path, rows: list[dict]) -> None:
    """Escribe places.csv con columnas name, lat, lng.

    Cada ``row`` tiene ``name`` (str) y, si trae coords, ``lat``/``lng`` como
    cadenas ya formateadas; si no las trae, las columnas quedan vacías.
    """
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "lat", "lng"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "name": row["name"],
                    "lat": row.get("lat", ""),
                    "lng": row.get("lng", ""),
                }
            )


@st.composite
def _coord_rows(draw):
    """Filas de places.csv con nombre válido y coords presentes o ausentes.

    Cuando están presentes, ``lat``/``lng`` son floats finitos escritos con
    ``repr`` (round-trip seguro a ``float``); cuando están ausentes, ambas
    columnas quedan vacías (Req 1.8, 1.9).
    """
    n = draw(st.integers(min_value=0, max_value=12))
    rows = []
    finite = st.floats(allow_nan=False, allow_infinity=False,
                       min_value=-1e6, max_value=1e6)
    for _ in range(n):
        name = draw(_valid_names)
        if draw(st.booleans()):
            lat = draw(finite)
            lng = draw(finite)
            rows.append({"name": name, "lat": repr(lat), "lng": repr(lng),
                         "_expect_coords": {"lat": lat, "lng": lng}})
        else:
            rows.append({"name": name, "_expect_coords": None})
    return rows


# Feature: agent-tools, Property 3: Las coordenadas del CSV se preservan y son numéricas; su ausencia se respeta
# Validates: Requirements 1.8, 1.9
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(rows=_coord_rows())
def test_csv_coords_preserved_or_absent(rows):
    """Para toda fila de places.csv: si lat y lng son numéricos, el Place tiene
    `coords` con esos valores como floats; si faltan, el Place no tiene la
    clave `coords` (Req 1.8, 1.9)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        _write_places_with_coords(d / "places.csv", rows)

        result = scan_resources.run(d)

        places = result["places"]
        # Todas las filas tienen nombre válido, así que sobreviven en orden.
        assert len(places) == len(rows)

        for row, place in zip(rows, places):
            expected = row["_expect_coords"]
            if expected is None:
                assert "coords" not in place, (
                    f"Place sin lat/lng no debería tener coords: {place!r}"
                )
            else:
                assert "coords" in place, f"Place con lat/lng debería tener coords: {place!r}"
                coords = place["coords"]
                assert isinstance(coords["lat"], float)
                assert isinstance(coords["lng"], float)
                assert coords["lat"] == expected["lat"]
                assert coords["lng"] == expected["lng"]
