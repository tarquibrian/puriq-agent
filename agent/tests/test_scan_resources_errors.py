"""Pruebas unitarias de condiciones de error de scan_resources (spec agent-tools).

Cubre los criterios de aceptación:
  - Req 1.2: falta site.json -> error que nombra el archivo y la ruta consultada.
  - Req 1.3: falta places.csv -> error que nombra el archivo y la ruta consultada.
  - Req 1.11: valor no numérico en lat/lng -> error que identifica la fila y la columna.
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

import pytest

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path al correr
# pytest desde cualquier directorio.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.tools import scan_resources  # noqa: E402


def _write_site_json(path: Path) -> None:
    path.write_text(
        json.dumps({"site": {"name": "T", "region": "R"}, "categories": []}),
        encoding="utf-8",
    )


def _write_places_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# --- Req 1.2: falta site.json ----------------------------------------------

def test_missing_site_json_raises_error_naming_file_and_path():
    """Sin site.json, scan_resources lanza FileNotFoundError que nombra el
    archivo faltante y la ruta consultada (Req 1.2)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # Solo places.csv existe; site.json falta.
        _write_places_csv(d / "places.csv", [], ["name"])

        with pytest.raises(FileNotFoundError) as excinfo:
            scan_resources.run(d)

        message = str(excinfo.value)
        assert "site.json" in message
        assert str(d) in message


# --- Req 1.3: falta places.csv ---------------------------------------------

def test_missing_places_csv_raises_error_naming_file_and_path():
    """Con site.json pero sin places.csv, scan_resources lanza FileNotFoundError
    que nombra el archivo faltante y la ruta consultada (Req 1.3)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        # places.csv falta a propósito.

        with pytest.raises(FileNotFoundError) as excinfo:
            scan_resources.run(d)

        message = str(excinfo.value)
        assert "places.csv" in message
        assert str(d) in message


# --- Req 1.11: valor no numérico en lat/lng --------------------------------

def test_non_numeric_lat_raises_error_identifying_row_and_column():
    """Un lat no numérico produce un error que identifica la fila (índice del CSV)
    y la columna inválida (Req 1.11)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        _write_places_csv(
            d / "places.csv",
            [{"name": "Plaza", "lat": "abc", "lng": "-65.0"}],
            ["name", "lat", "lng"],
        )

        with pytest.raises(ValueError) as excinfo:
            scan_resources.run(d)

        message = str(excinfo.value)
        assert "lat" in message
        # La primera fila de datos es la fila 2 del CSV (el encabezado es la fila 1).
        assert "2" in message
        assert "abc" in message


def test_non_numeric_lng_raises_error_identifying_row_and_column():
    """Un lng no numérico produce un error que identifica la fila y la columna
    'lng' (Req 1.11)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        _write_places_csv(
            d / "places.csv",
            [
                {"name": "Plaza", "lat": "-19.0", "lng": "-65.0"},
                {"name": "Mirador", "lat": "-19.5", "lng": "no-num"},
            ],
            ["name", "lat", "lng"],
        )

        with pytest.raises(ValueError) as excinfo:
            scan_resources.run(d)

        message = str(excinfo.value)
        assert "lng" in message
        # La segunda fila de datos es la fila 3 del CSV (encabezado = fila 1).
        assert "3" in message
        assert "no-num" in message


def test_non_numeric_row_number_accounts_for_skipped_empty_names():
    """El número de fila reportado corresponde a la fila real del CSV aunque se
    omitan filas con name vacío (Req 1.11 en conjunto con Req 1.7)."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_site_json(d / "site.json")
        _write_places_csv(
            d / "places.csv",
            [
                {"name": "   ", "lat": "1.0", "lng": "1.0"},   # fila 2: omitida
                {"name": "Real", "lat": "xx", "lng": "2.0"},   # fila 3: error
            ],
            ["name", "lat", "lng"],
        )

        with pytest.raises(ValueError) as excinfo:
            scan_resources.run(d)

        message = str(excinfo.value)
        assert "lat" in message
        assert "3" in message
