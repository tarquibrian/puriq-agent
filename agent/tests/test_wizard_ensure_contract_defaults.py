"""Pruebas del helper `_ensure_contract_defaults` del wizard (Req 1.5).

Cubren la tolerancia a pasos salteados: si el usuario omite un paso opcional del
wizard (p. ej. Marca), el archivo de ese documento del contrato nunca se escribe.
Antes del build, `server._ensure_contract_defaults` debe materializar SOLO los
documentos faltantes con su default valido (via la capa de contrato, DD-1),
**sin** tocar los que ya existen, y avisar por el callback de progreso.

Se prueba el helper de forma directa (unidad), sin levantar el servidor real ni
correr un build (nada de LLM/npm), segun DD-1/DD-2.
"""
from __future__ import annotations

import json

from puriq import schemas
from puriq.wizard import contracts, server


def _write_json(path, data) -> bytes:
    """Escribe `data` como JSON en `path` y devuelve los bytes escritos."""
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(payload)
    return payload


def test_ensure_defaults_materializes_only_missing_theme(tmp_path):
    """Un `theme.tokens.json` ausente se crea valido sin tocar los ya presentes.

    Escenario: el proyecto tiene SOLO `tourism-data.json` y `site.config.json`
    (docs minimos validos) pero NO `theme.tokens.json` (paso Marca salteado).
    Tras `_ensure_contract_defaults` el tema debe existir y ser schema-valido,
    los dos archivos previos deben quedar intactos byte a byte, y el callback de
    progreso debe haber recibido el aviso de "Marca no configurada".
    """
    project = tmp_path

    # Docs previos minimos y validos (los que el usuario si completo).
    tourism = {
        "site": {
            "name": "Pueblo Test",
            "region": "Region Test",
            "defaultLocale": "es",
            "center": {"lat": 0, "lng": 0},
        },
        "places": [],
    }
    site_config = {"layout": "clasico", "modules": {}}

    tourism_path = contracts._doc_path(project, "tourism-data")
    site_config_path = contracts._doc_path(project, "site-config")
    theme_path = contracts._doc_path(project, "theme-tokens")

    tourism_bytes_before = _write_json(tourism_path, tourism)
    site_config_bytes_before = _write_json(site_config_path, site_config)

    # Precondicion: el tema NO existe (paso salteado).
    assert not theme_path.exists()

    # Espia de progreso: captura los mensajes emitidos por el helper.
    mensajes: list[str] = []

    def progress_spy(msg: str) -> None:
        mensajes.append(msg)

    server._ensure_contract_defaults(project, progress_spy)

    # 1) El tema ahora existe y es schema-valido (carga estricta).
    assert theme_path.exists()
    schemas.load(theme_path, "theme-tokens")  # no lanza => valido

    # 2) Los dos archivos previos NO fueron modificados (byte a byte).
    assert tourism_path.read_bytes() == tourism_bytes_before
    assert site_config_path.read_bytes() == site_config_bytes_before

    # 3) El espia recibio el aviso de Marca por defecto (y solo ese, pues los
    #    otros dos documentos ya existian).
    assert mensajes == ["Marca no configurada: usando tema por defecto."]
