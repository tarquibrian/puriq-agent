"""Pruebas de ejemplo (unit) e integración de las intake tools (Tareas 12.1, 12.2).

Complementan las pruebas de propiedad del nivel intake con casos concretos, tal
como pide la sección *Testing Strategy → Pruebas de ejemplo y de integración* del
diseño de `conversational-intake-mcp`:

- **12.1 Ejemplos (unit)** sobre `puriq.intake.tools`:
    * Delegación a los constructores puros: cada tool de escritura produce en el
      contrato exactamente la misma porción que el constructor puro subyacente
      (`build_place`, `build_event`, `build_modules`, `build_landing`) — Req 1.2, 1.3.
    * `set_brand` escribe y relee los colores correctamente — Req 8.1.
    * Calidad de los mensajes de error específicos traducidos por
      `run_intake_tool` (coordenada fuera de rango, dominio inválido, campo QA
      vacío, id inexistente) — complemento a las propiedades de rechazo, Req 12.3.
    * Fallo de `build` sobre un contrato incompleto: mensaje accionable y sin
      secretos — Req 12.3.

- **12.2 Integración de `build`** (Req 12.1, 12.2):
    * `build` delega en `Puriq(project).build` y envuelve el resultado como
      `{"dist": str(path)}`.
    * ESTRATEGIA (documentada): el build real depende del ensamblado Astro
      (node/npm) y de `geocode`, que no están garantizados en el entorno de CI.
      Igual que hace `test_build_site_integration.py` con `subprocess.run`, aquí
      se **parchea (`monkeypatch`) `Puriq.build`** para que devuelva una ruta
      `dist/` simulada, y se verifica que la intake tool `build` delega
      correctamente (pasando `use_llm` y el `project`) y envuelve la ruta. Así se
      prueba el cableado de la capa fina sin ejecutar node ni tocar la red.

Todas las pruebas operan sobre un proyecto temporal (`tmp_path`) para aislar la
E/S. No se mockea nada de la lógica bajo prueba en los ejemplos unit (salvo el
límite externo `Puriq.build` en la prueba de integración, por diseño).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path al correr
# pytest desde cualquier directorio (mismo patrón que las pruebas existentes).
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.intake import tools  # noqa: E402
from puriq.wizard import contracts  # noqa: E402
from puriq.wizard.intake import build_event, build_place  # noqa: E402
from puriq.wizard.landing import build_landing  # noqa: E402
from puriq.wizard.modules import build_modules  # noqa: E402

_TOURISM = "tourism-data"
_CONFIG = "site-config"
_THEME = "theme-tokens"


def _new_project(tmp_path: Path) -> Path:
    """Crea (y devuelve) un directorio de proyecto temporal vacío."""
    project = tmp_path / "project"
    project.mkdir()
    return project


def _seed_site(project: Path) -> None:
    """Registra una identidad de sitio válida en `tourism-data`.

    El documento base de `tourism-data` trae `site.name`/`site.region` vacíos, que
    la validación de escritura rechaza; `set_site` los completa, de modo que las
    escrituras posteriores sobre `tourism-data` (add_place/add_event) sean válidas.
    """
    tools.set_site(
        project,
        name="Potosí",
        region="Potosí",
        center_lat=-19.58,
        center_lng=-65.75,
    )


# ---------------------------------------------------------------------------
# 12.1 — Delegación a los constructores puros (Req 1.2, 1.3)
# ---------------------------------------------------------------------------

def test_add_place_delegates_to_build_place(tmp_path):
    """`add_place` persiste exactamente la porción que produce `build_place` (Req 1.2)."""
    project = _new_project(tmp_path)
    _seed_site(project)

    kwargs = dict(name="Cerro Rico", category="atraccion", lat=-19.5847, lng=-65.7534)
    tools.add_place(project, **kwargs)

    tourism = contracts._load_contract(project, _TOURISM)
    esperado = build_place(**kwargs)

    assert tourism["places"] == [esperado]


def test_add_place_draft_delegates_to_build_place(tmp_path):
    """Un lugar con solo dirección (borrador) coincide con `build_place` (Req 1.2, 5.3)."""
    project = _new_project(tmp_path)
    _seed_site(project)

    kwargs = dict(name="Casa de la Moneda", category="museo", address="Calle Ayacucho")
    tools.add_place(project, **kwargs)

    tourism = contracts._load_contract(project, _TOURISM)
    esperado = build_place(**kwargs)

    assert tourism["places"] == [esperado]
    # Borrador: no se inventaron coordenadas.
    assert "coords" not in tourism["places"][0]


def test_add_event_delegates_to_build_event(tmp_path):
    """`add_event` persiste exactamente la porción que produce `build_event` (Req 1.2)."""
    project = _new_project(tmp_path)
    _seed_site(project)

    kwargs = dict(
        name="Fiesta de la Villa",
        start_date="2025-04-10",
        end_date="2025-04-11",
        description="Celebración anual",
    )
    tools.add_event(project, **kwargs)

    tourism = contracts._load_contract(project, _TOURISM)
    esperado = build_event(**kwargs)

    assert tourism["events"] == [esperado]


def test_configure_modules_delegates_to_build_modules(tmp_path):
    """`configure_modules` escribe exactamente lo que produce `build_modules` (Req 1.3)."""
    project = _new_project(tmp_path)

    selection = [
        {"key": "places", "enabled": True},
        {"key": "map", "enabled": True},
        {"key": "events", "enabled": False},
    ]
    tools.configure_modules(project, selection=selection)

    site_config = contracts._load_contract(project, _CONFIG)
    assert site_config["modules"] == build_modules(selection)


def test_configure_landing_delegates_to_build_landing(tmp_path):
    """`configure_landing` escribe exactamente lo que produce `build_landing` (Req 1.3)."""
    project = _new_project(tmp_path)

    selection = [
        {"type": "hero", "enabled": True, "content": {"headline": "Potosí"}},
        {"type": "features", "enabled": True},
    ]
    tools.configure_landing(project, selection=selection)

    site_config = contracts._load_contract(project, _CONFIG)
    assert site_config["landing"] == build_landing(selection)


# ---------------------------------------------------------------------------
# 12.1 — `set_brand` escribe y relee colores (Req 8.1)
# ---------------------------------------------------------------------------

def test_set_brand_writes_and_reads_colors(tmp_path):
    """`set_brand` persiste los colores y se releen correctamente (Req 8.1)."""
    project = _new_project(tmp_path)

    colors = {"primary": "#1a73e8", "background": "#ffffff", "text": "#111111"}
    respuesta = tools.set_brand(project, colors=colors)

    # La respuesta de escritura devuelve el estado del documento afectado.
    assert respuesta["document"]["colors"] == colors

    # Y al releer el contrato desde disco los colores coinciden.
    theme = contracts._load_contract(project, _THEME)
    assert theme["colors"] == colors


# ---------------------------------------------------------------------------
# 12.1 — Calidad de los mensajes de error específicos (vía run_intake_tool)
# ---------------------------------------------------------------------------

def _causa(respuesta) -> str:
    """Extrae el texto de causa de una respuesta de error traducida."""
    assert isinstance(respuesta, dict), f"esperaba dict de error, obtuve: {respuesta!r}"
    assert "causa" in respuesta, f"esperaba una respuesta de error accionable: {respuesta!r}"
    return respuesta["causa"]


def test_error_message_coordinate_out_of_range_mentions_range(tmp_path):
    """Una coordenada fuera de rango produce un error que menciona el rango (Req 12.3)."""
    project = _new_project(tmp_path)

    respuesta = tools.run_intake_tool(
        "add_place",
        {"project": str(project), "name": "X", "category": "c", "lat": 999.0, "lng": 0.0},
    )
    causa = _causa(respuesta).lower()

    assert "rango" in causa
    # El mensaje nombra los límites del rango de latitud.
    assert "90" in causa
    # No se escribió nada: el contrato no debe existir aún.
    assert not (project / "tourism-data.json").exists()


def test_error_message_invalid_domain_mentions_format(tmp_path):
    """Un dominio inválido produce un error que menciona el formato esperado (Req 12.3)."""
    project = _new_project(tmp_path)

    respuesta = tools.run_intake_tool(
        "set_site",
        {
            "project": str(project),
            "name": "Sitio",
            "region": "Potosí",
            "center": {"lat": -19.58, "lng": -65.75},
            "domain": "no es un dominio",
        },
    )
    causa = _causa(respuesta).lower()

    assert "formato" in causa
    assert "dominio" in causa


def test_error_message_empty_qa_field_names_field(tmp_path):
    """Un campo QA vacío produce un error que nombra el campo (Req 12.3)."""
    project = _new_project(tmp_path)

    respuesta = tools.run_intake_tool(
        "add_qa",
        {"project": str(project), "question": "   ", "answer": "una respuesta"},
    )
    causa = _causa(respuesta).lower()

    # El validador nombra el campo faltante (la pregunta / question).
    assert "pregunta" in causa or "question" in causa


def test_error_message_edit_missing_id_says_not_found(tmp_path):
    """`edit_item` sobre un id inexistente se rechaza como 'no encontrado' (Req 12.3)."""
    project = _new_project(tmp_path)
    # Necesitamos un tourism-data existente para llegar a la búsqueda por id.
    _seed_site(project)
    tools.add_place(project, name="Cerro Rico", category="atraccion", lat=-19.58, lng=-65.75)

    respuesta = tools.run_intake_tool(
        "edit_item",
        {"project": str(project), "id": "no-existe", "fields": {"name": "Nuevo"}},
    )
    causa = _causa(respuesta).lower()

    assert "no se encontr" in causa or "no encontr" in causa


def test_error_message_remove_missing_id_says_not_found(tmp_path):
    """`remove_item` sobre un id inexistente se rechaza como 'no encontrado' (Req 12.3)."""
    project = _new_project(tmp_path)
    _seed_site(project)
    tools.add_place(project, name="Cerro Rico", category="atraccion", lat=-19.58, lng=-65.75)

    respuesta = tools.run_intake_tool(
        "remove_item",
        {"project": str(project), "id": "no-existe"},
    )
    causa = _causa(respuesta).lower()

    assert "no se encontr" in causa or "no encontr" in causa


# ---------------------------------------------------------------------------
# 12.1 — Fallo de `build` con mensaje accionable, sin exponer secretos (Req 12.3)
# ---------------------------------------------------------------------------

def _prepare_incomplete_contract(project: Path) -> None:
    """Prepara un contrato sintácticamente válido pero incompleto para `build`.

    Escribe los tres documentos usando las propias intake tools (que validan
    antes de persistir), con un lugar en **borrador**: sin coordenadas y sin
    dirección. El build lo aceptará como draft al cargar, pero fallará en la
    comprobación accionable de coords (`schemas.check_places_have_coords`), sin
    tocar la red (no hay dirección que geocodificar).
    """
    tools.set_site(
        project,
        name="Potosí",
        region="Potosí",
        center_lat=-19.58,
        center_lng=-65.75,
    )
    # Lugar borrador: sin coords ni address -> incompleto para el build.
    tools.add_place(project, name="Lugar Sin Coords", category="atraccion")
    tools.configure_modules(project, selection=[{"key": "places", "enabled": True}])
    # Escribe theme.tokens.json válido (documento base) para que build pueda cargarlo.
    tools.set_brand(project)


def test_build_incomplete_contract_returns_actionable_error(tmp_path):
    """`build` sobre un contrato incompleto devuelve un mensaje accionable (Req 12.3)."""
    project = _new_project(tmp_path)
    _prepare_incomplete_contract(project)

    respuesta = tools.run_intake_tool(
        "build", {"project": str(project), "use_llm": False}
    )

    # No se devuelve un dist: se traduce el error del pipeline a algo accionable.
    assert isinstance(respuesta, dict)
    assert "dist" not in respuesta
    causa = _causa(respuesta).lower()
    # El error nombra la causa real (faltan coordenadas) de forma legible.
    assert "coordenada" in causa
    # Y ofrece una acción sugerida.
    assert respuesta.get("accion")


def test_build_error_does_not_expose_secrets(tmp_path, monkeypatch):
    """El error de `build` está redactado: ningún secreto configurado aparece (Req 12.3)."""
    project = _new_project(tmp_path)
    _prepare_incomplete_contract(project)

    # Inyectamos un secreto configurado y forzamos que aparezca en el mensaje de
    # error crudo, para verificar que la traducción lo enmascara.
    secreto = "supersecreto-de-prueba-123"
    from puriq import config

    monkeypatch.setenv("PURIQ_TEST_SECRET", secreto)
    # Registrar la variable como secreto por la vía pública (get_env(secret=True)),
    # para que `redact` conozca su valor y lo enmascare.
    config.get_env("PURIQ_TEST_SECRET", secret=True)

    def _boom(self, use_llm=True):
        raise RuntimeError(f"fallo inesperado con token {secreto}")

    monkeypatch.setattr(tools.Puriq, "build", _boom)

    respuesta = tools.run_intake_tool("build", {"project": str(project)})

    causa = _causa(respuesta)
    # El valor del secreto no debe aparecer textualmente en la respuesta.
    assert secreto not in causa


# ---------------------------------------------------------------------------
# 12.2 — Integración de `build`: delega en Puriq.build y envuelve dist/
# ---------------------------------------------------------------------------
# Estrategia (ver docstring del módulo): el ensamblado real de Astro (node/npm) y
# geocode no están garantizados en el entorno; se parchea `Puriq.build` (frontera
# externa) para devolver una ruta dist/ simulada y se verifica el cableado de la
# capa fina: la intake tool delega el `project` y `use_llm`, y envuelve el
# resultado como {"dist": str(path)}.

def test_build_delegates_to_puriq_and_wraps_dist(tmp_path, monkeypatch):
    """`build` delega en `Puriq.build` y devuelve `{"dist": str(path)}` (Req 12.1, 12.2)."""
    project = _new_project(tmp_path)
    fake_dist = project / "dist"

    llamadas: dict = {}

    def fake_build(self, use_llm=True):
        llamadas["project"] = self.project
        llamadas["use_llm"] = use_llm
        return fake_dist

    monkeypatch.setattr(tools.Puriq, "build", fake_build)

    resultado = tools.build(project, use_llm=False)

    # Envuelve la ruta dist/ como cadena bajo la clave "dist".
    assert resultado == {"dist": str(fake_dist)}
    # Delega correctamente el proyecto y el flag use_llm.
    assert llamadas["project"] == project
    assert llamadas["use_llm"] is False


def test_build_tool_routing_wraps_dist(tmp_path, monkeypatch):
    """`run_intake_tool('build', ...)` delega y envuelve dist/ (Req 12.1, 12.2)."""
    project = _new_project(tmp_path)
    fake_dist = project / "dist"

    def fake_build(self, use_llm=True):
        return fake_dist

    monkeypatch.setattr(tools.Puriq, "build", fake_build)

    resultado = tools.run_intake_tool("build", {"project": str(project)})

    assert resultado == {"dist": str(fake_dist)}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
