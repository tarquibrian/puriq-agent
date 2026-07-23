"""Prueba de que `GET /api/state` expone el `landing` existente (Tarea 11.3, Req 14.5).

Cuando ya existe un `site.config.json` con `Site_Config.landing`, `GET /api/state`
debe devolver ese `landing` dentro del `site-config` para que el Wizard_UI pueda
prellenar el paso "Portada" con las Landing_Section guardadas (Req 14.5). Esto se
cumple por construccion: `get_state` carga cada documento con
`contracts._load_contract`, que para `site-config` usa `schemas.load` y devuelve
el documento **completo** (incluido `landing`); no hay ningun paso que lo
elimine. Esta prueba lo verifica end-to-end contra la app real.

Se usa `fastapi.testclient.TestClient` apuntando el wizard a un proyecto temporal
via `PURIQ_PROJECT`. No se corre build ni LLM.
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


def test_get_state_surfaces_existing_landing(tmp_path):
    """`GET /api/state` devuelve el `landing` existente para prellenar la UI (Req 14.5).

    Escribe un `site.config.json` schema-valido con una lista `landing` de dos
    secciones (hero + features) y confirma que `GET /api/state` las expone en
    `site-config.landing` con su `type`, `order` y `content` intactos.
    """
    site_config = {
        "layout": "clasico",
        "modules": {"places": {"enabled": True, "order": 1}},
        "landing": [
            {
                "type": "hero",
                "enabled": True,
                "order": 1,
                "content": {"headline": "Potosi", "subheadline": "Plata y sal"},
            },
            {
                "type": "features",
                "enabled": False,
                "order": 2,
                "content": {"title": "Que te espera"},
            },
        ],
    }
    # El documento sembrado es schema-valido antes de escribirlo.
    schemas.validate(site_config, "site-config")
    path = contracts._doc_path(tmp_path, "site-config")
    path.write_text(json.dumps(site_config, ensure_ascii=False), encoding="utf-8")

    client = _client(tmp_path)
    resp = client.get("/api/state")
    assert resp.status_code == 200, resp.text

    state = resp.json()
    landing = state["site-config"]["landing"]
    assert [s["type"] for s in landing] == ["hero", "features"]
    assert [s["order"] for s in landing] == [1, 2]
    assert landing[0]["content"] == {"headline": "Potosi", "subheadline": "Plata y sal"}
    assert landing[1]["enabled"] is False


def test_get_state_without_landing_omits_key(tmp_path):
    """Sin `landing` previo, `GET /api/state` no inventa la clave (retrocompat, Req 16.1).

    Un proyecto sin `site.config.json` cae en el documento base minimo
    (`{layout, modules}`) que no incluye `landing`; la UI itera una lista vacia
    sin romperse. Verifica que la clave `landing` no aparece.
    """
    client = _client(tmp_path)
    resp = client.get("/api/state")
    assert resp.status_code == 200, resp.text

    state = resp.json()
    assert "landing" not in state["site-config"]
