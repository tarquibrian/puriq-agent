"""Pruebas de integración del build de Astro en Build_Site (Req 5.7, 5.8, 5.9).

Verifican el paso final de `build_site.assemble`: la ejecución del build de
Astro (`npm run build`) y el manejo de su resultado. El proceso de Node/npm es
una **frontera externa**: se mockea `subprocess.run` (parcheado en el módulo
`build_site`) para no ejecutar un build real. También se parchea
`shutil.which` para simular que `npm` está disponible en el PATH y
`TEMPLATE_DIR` para copiar una Template mínima en vez de la real.

Ejemplos cubiertos (1-2):
  1. Build exitoso: `subprocess.run` devuelve código 0 y crea `dist/` en el
     directorio de trabajo; `assemble` promueve la salida a `project/dist` y
     devuelve esa ruta (Req 5.7, 5.8).
  2. Build con error: `subprocess.run` del build devuelve código != 0 con
     stdout/stderr capturados; `assemble` reporta un error que incluye esa
     salida relevante del proceso (Req 5.9).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

# El paquete `puriq` vive en agent/; aseguramos que esté en sys.path al correr
# pytest desde cualquier directorio.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.tools import build_site  # noqa: E402


# --- Fixtures y helpers -----------------------------------------------------

# Contrato mínimo VÁLIDO contra los tres esquemas (schemas/), suficiente para
# que `_write_contract` (que valida antes del build) llegue al paso del build.
_DATA = {
    "site": {
        "name": "Potosí",
        "region": "Potosí",
        "defaultLocale": "es",
        "center": {"lat": -19.58, "lng": -65.75},
    },
    "places": [
        {
            "id": "cerro-rico",
            "name": "Cerro Rico",
            "category": "atraccion",
            "coords": {"lat": -19.5847, "lng": -65.7534},
        }
    ],
}

_CONFIG = {
    "layout": "clasico",
    "modules": {
        "places": {"enabled": True, "order": 1},
        "events": {"enabled": False, "order": 2},
    },
}

_THEME = {
    "colors": {"primary": "#1a73e8", "background": "#ffffff", "text": "#111111"},
    "typography": {"headingFont": "Inter", "bodyFont": "Inter"},
}


def _make_minimal_template(root: Path) -> Path:
    """Crea una Template Astro mínima para que `_prepare_workdir` la copie.

    Solo necesita ser un directorio copiable (con `src/`); no lleva
    `node_modules` ni `dist` (que `assemble` regenera/mueve localmente).
    """
    template = root / "template"
    (template / "src").mkdir(parents=True)
    (template / "package.json").write_text('{"name": "tmpl", "scripts": {"build": "astro build"}}')
    (template / "astro.config.mjs").write_text("export default {};\n")
    return template


def _fake_run_factory(*, build_returncode=0, build_stdout="", build_stderr=""):
    """Devuelve un doble de `subprocess.run` que simula npm sin ejecutarlo.

    - Comandos de instalación (`npm install` / `npm ci`): siempre código 0.
    - Comando de build (`npm run build`): usa `build_returncode`/salida dados;
      si el build es exitoso, crea `<cwd>/dist` como haría Astro.
    """
    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None, capture_output=False, text=False, **kwargs):
        calls.append(list(cmd))
        if "build" in cmd:
            if build_returncode == 0:
                dist = Path(cwd) / "dist"
                dist.mkdir(parents=True, exist_ok=True)
                (dist / "index.html").write_text("<!doctype html><html></html>")
            return subprocess.CompletedProcess(
                cmd, build_returncode, stdout=build_stdout, stderr=build_stderr
            )
        # Instalación de dependencias: éxito.
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    fake_run.calls = calls  # type: ignore[attr-defined]
    return fake_run


# --- Ejemplo 1: build exitoso deja dist/ y devuelve su ruta -----------------

def test_assemble_successful_build_leaves_dist(tmp_path, monkeypatch):
    """Un build exitoso (código 0) deja la salida en `project/dist` y `assemble`
    devuelve esa ruta (Req 5.7, 5.8)."""
    template = _make_minimal_template(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(build_site, "TEMPLATE_DIR", template)

    fake_run = _fake_run_factory(build_returncode=0, build_stdout="built ok")

    with mock.patch.object(build_site.shutil, "which", return_value="/usr/bin/npm"), \
            mock.patch.object(build_site.subprocess, "run", side_effect=fake_run):
        result = build_site.assemble(project, _DATA, _CONFIG, _THEME)

    # Req 5.8: devuelve project/dist y la salida quedó materializada allí.
    assert result == project / "dist"
    assert result.is_dir()
    assert (result / "index.html").exists()

    # Req 5.7: se ejecutó el build de Astro vía `npm run build`.
    assert any("build" in cmd for cmd in fake_run.calls)


# --- Ejemplo 2: build con error reporta la salida del proceso ---------------

def test_assemble_failed_build_reports_process_output(tmp_path, monkeypatch):
    """Un build con error (código != 0) hace que `assemble` reporte un
    RuntimeError que incluye la salida relevante del proceso (Req 5.9)."""
    template = _make_minimal_template(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(build_site, "TEMPLATE_DIR", template)

    fake_run = _fake_run_factory(
        build_returncode=1,
        build_stdout="Building...",
        build_stderr="Error: Could not resolve component './Missing.astro'",
    )

    with mock.patch.object(build_site.shutil, "which", return_value="/usr/bin/npm"), \
            mock.patch.object(build_site.subprocess, "run", side_effect=fake_run):
        with pytest.raises(RuntimeError) as excinfo:
            build_site.assemble(project, _DATA, _CONFIG, _THEME)

    mensaje = str(excinfo.value)
    # Req 5.9: el error incluye la salida relevante del proceso de build.
    assert "Could not resolve component './Missing.astro'" in mensaje
    assert "Building..." in mensaje

    # No se promovió ningún dist/ al proyecto ante un build fallido.
    assert not (project / "dist").exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
