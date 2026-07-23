"""Pruebas del endpoint `PUT /api/site-config` con seleccion de portada (Tarea 11.2).

Cubren la extension del endpoint para aceptar una lista **ordenada** de
Landing_Section (`type`, `enabled`, `content`): cuando viene `landing`, el
servidor la construye con `build_landing` (asigna `order` por posicion, restringe
`type` al catalogo) y la persiste en `Site_Config.landing` via load-merge-save
con validacion estricta contra `site-config.schema.json` (Req 14.3, 14.4). Una
seccion fuera del catalogo se rechaza con `422` que nombra el campo, sin escribir
nada (Req 14.4). El comportamiento de `modules`/`deployTarget` se conserva.

Se usa `fastapi.testclient.TestClient` sobre la app real, apuntando el wizard a
un proyecto temporal via la variable de entorno `PURIQ_PROJECT`. No se corre
build ni LLM.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from puriq import schemas
from puriq.wizard import contracts, server


def _client(project) -> TestClient:
    """TestClient con el wizard apuntando a `project` via `PURIQ_PROJECT`."""
    import os

    os.environ[server.PROJECT_ENV_VAR] = str(project)
    return TestClient(server.app)


def test_put_site_config_persists_valid_landing(tmp_path):
    """Un PUT con `landing` valido persiste la portada schema-valida (Req 14.3, 14.4).

    Envia una seleccion ordenada de secciones con copy; espera un `200`, que el
    `site.config.json` en disco contenga `landing` con el `order` asignado por
    posicion (1-based) y el `content` conservado, y que el documento sea
    schema-valido (carga estricta no lanza).
    """
    client = _client(tmp_path)

    payload = {
        "modules": [
            {"key": "places", "enabled": True},
            {"key": "map", "enabled": True},
        ],
        "landing": [
            {
                "type": "hero",
                "enabled": True,
                "content": {"headline": "Potosi", "subheadline": "Plata y sal"},
            },
            {"type": "features", "enabled": True},
            {"type": "cta", "enabled": False, "content": {"message": ""}},
        ],
    }

    resp = client.put("/api/site-config", json=payload)
    assert resp.status_code == 200, resp.text

    # El documento persistido en disco es schema-valido (carga estricta).
    site_config_path = contracts._doc_path(tmp_path, "site-config")
    assert site_config_path.exists()
    schemas.load(site_config_path, "site-config")  # no lanza => valido

    on_disk = json.loads(site_config_path.read_text(encoding="utf-8"))
    landing = on_disk["landing"]
    assert [s["type"] for s in landing] == ["hero", "features", "cta"]
    assert [s["order"] for s in landing] == [1, 2, 3]
    assert landing[0]["content"] == {"headline": "Potosi", "subheadline": "Plata y sal"}
    assert landing[2]["enabled"] is False

    # Los modulos se conservan intactos junto a la portada.
    assert on_disk["modules"]["places"] == {"enabled": True, "order": 1}
    assert on_disk["modules"]["map"] == {"enabled": True, "order": 2}


def test_put_site_config_rejects_out_of_catalog_section(tmp_path):
    """Una seccion fuera del catalogo devuelve `422` sin persistir nada (Req 14.4).

    Envia una seccion con `type` desconocido; espera un `422` cuyo cuerpo nombra
    el campo/problema (el catalogo soportado), y confirma que NO se escribio el
    `site.config.json` (validate-before-write: la construccion falla antes de
    tocar disco).
    """
    client = _client(tmp_path)

    payload = {
        "modules": [{"key": "places", "enabled": True}],
        "landing": [
            {"type": "hero", "enabled": True},
            {"type": "carousel", "enabled": True},
        ],
    }

    resp = client.put("/api/site-config", json=payload)
    assert resp.status_code == 422, resp.text

    body = resp.json()
    # El cuerpo redactado nombra el problema y/o el catalogo soportado.
    texto = json.dumps(body, ensure_ascii=False).lower()
    assert "carousel" in texto or "catalogo" in texto or "hero" in texto

    # No se persistio nada (la seccion invalida se rechaza antes de escribir).
    assert not contracts._doc_path(tmp_path, "site-config").exists()
