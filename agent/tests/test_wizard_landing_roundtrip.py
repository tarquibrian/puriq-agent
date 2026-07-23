"""Prueba de integracion round-trip de la portada: PUT persiste y GET prellena (Tarea 11.5).

Cubre el escenario end-to-end que NO cubren las dos pruebas existentes de forma
conjunta: un unico ciclo `PUT /api/site-config` -> `GET /api/state` sobre la app
real. Prueba que la seleccion ORDENADA de `landing` enviada por el Wizard_UI se
persiste (Req 13.2) y que, acto seguido, `GET /api/state` la devuelve intacta
para prellenar el paso "Portada" (Req 14.5) — demostrando que persistencia y
prellenado funcionan juntos sobre el mismo estado en disco. El orden de las
secciones se verifica por posicion (no por igualdad accidental), y se confirma
que el copy y los tipos sobreviven el viaje de ida y vuelta.

A diferencia de:
- `test_wizard_site_config_landing.py`, que verifica el PUT contra el disco y el
  rechazo `422` de una seccion fuera del catalogo (Req 14.4), pero no consulta
  `GET /api/state`.
- `test_wizard_state_landing_prefill.py`, que **siembra** el disco a mano y solo
  verifica el GET, pero no ejercita el PUT del Wizard_Server.

Esta prueba encadena ambos endpoints en un solo flujo, sin sembrar disco a mano.

Se usa `fastapi.testclient.TestClient` sobre la app real, apuntando el wizard a
un proyecto temporal via la variable de entorno `PURIQ_PROJECT`. No se corre
build ni LLM.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from puriq.wizard import server


def _client(project) -> TestClient:
    """TestClient con el wizard apuntando a `project` via `PURIQ_PROJECT`."""
    import os

    os.environ[server.PROJECT_ENV_VAR] = str(project)
    return TestClient(server.app)


def test_put_then_get_state_roundtrips_landing(tmp_path):
    """Un PUT con `landing` ordenada se refleja identico en `GET /api/state` (Req 13.2, 14.5).

    Envia via PUT una seleccion ordenada de tres secciones con copy; luego pide
    `GET /api/state` y confirma que `site-config.landing` devuelve exactamente lo
    persistido: el mismo tipo por posicion, el `order` 1-based asignado por el
    servidor, y el `content` de cada seccion intacto. Esto prueba que la escritura
    del Wizard_Server (Req 13.2) y el prellenado desde el estado (Req 14.5)
    operan de forma consistente sobre el mismo documento.
    """
    client = _client(tmp_path)

    payload = {
        "modules": [
            {"key": "places", "enabled": True},
            {"key": "events", "enabled": True},
        ],
        "landing": [
            {
                "type": "hero",
                "enabled": True,
                "content": {"headline": "Uyuni", "subheadline": "El espejo del cielo"},
            },
            {
                "type": "stats",
                "enabled": True,
                "content": {"metrics": [{"value": "10582", "label": "km2 de salar"}]},
            },
            {
                "type": "cta",
                "enabled": False,
                "content": {"message": "Planifica tu visita"},
            },
        ],
    }

    put_resp = client.put("/api/site-config", json=payload)
    assert put_resp.status_code == 200, put_resp.text

    state_resp = client.get("/api/state")
    assert state_resp.status_code == 200, state_resp.text

    landing = state_resp.json()["site-config"]["landing"]

    # El orden se comprueba por posicion (no por coincidencia accidental).
    assert [s["type"] for s in landing] == ["hero", "stats", "cta"]
    # El servidor asigna `order` 1-based estrictamente creciente por posicion.
    assert [s["order"] for s in landing] == [1, 2, 3]

    # El copy sobrevive el viaje de ida y vuelta, campo por campo y por tipo.
    hero, stats, cta = landing
    assert hero["content"] == {"headline": "Uyuni", "subheadline": "El espejo del cielo"}
    assert hero["enabled"] is True
    assert stats["content"] == {"metrics": [{"value": "10582", "label": "km2 de salar"}]}
    assert cta["content"] == {"message": "Planifica tu visita"}
    assert cta["enabled"] is False
