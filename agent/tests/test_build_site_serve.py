"""Pruebas unitarias de la previsualizacion (build_site.serve) (spec agent-tools).

Cubre los criterios de aceptacion:
  - Req 6.3: sin puerto indicado, serve usa el puerto por defecto 4322.
  - Req 6.2: sin `dist/`, serve reporta un error indicando ejecutar
    `puriq build` primero.

Para no abrir un socket ni bloquear el proceso sirviendo indefinidamente, se
parchea la frontera del servidor (`http.server.ThreadingHTTPServer`) con un
doble de prueba que registra la direccion de bind (de la que se extrae el
puerto) y cuyo `serve_forever` retorna de inmediato. Asi la prueba verifica el
puerto que `serve` pasa al servidor sin efectos de red reales.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

# El paquete `puriq` vive en agent/; aseguramos que este en sys.path al correr
# pytest desde cualquier directorio.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.tools import build_site  # noqa: E402


class _FakeServer:
    """Doble de prueba de ThreadingHTTPServer que no abre ningun socket.

    Registra la tupla `(host, port)` con la que se le construye para poder
    afirmar el puerto elegido, y hace que `serve_forever` retorne de inmediato
    (no bloquea). `shutdown`/`server_close` son no-ops.
    """

    #: Ultima direccion de bind con la que se construyo la instancia.
    last_server_address: tuple[str, int] | None = None

    def __init__(self, server_address, handler):
        # Guardamos la direccion en la clase para inspeccionarla en el test.
        type(self).last_server_address = server_address
        self.server_address = server_address
        self.handler = handler
        self.serve_forever_called = False
        self.shutdown_called = False
        self.closed = False

    def serve_forever(self):
        # No bloquea: registra la llamada y retorna de inmediato.
        self.serve_forever_called = True

    def shutdown(self):
        self.shutdown_called = True

    def server_close(self):
        self.closed = True


# --- Req 6.3: puerto por defecto 4322 --------------------------------------

def test_serve_uses_default_port_4322_when_no_port_given(monkeypatch):
    """Sin puerto indicado, serve enlaza el servidor en el puerto 4322 (Req 6.3)."""
    _FakeServer.last_server_address = None
    monkeypatch.setattr(
        "http.server.ThreadingHTTPServer", _FakeServer, raising=True
    )

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        # Un dist/ existente es requisito para llegar a crear el servidor.
        (project / "dist").mkdir()

        build_site.serve(project)  # sin argumento de puerto -> por defecto

    assert _FakeServer.last_server_address is not None
    host, port = _FakeServer.last_server_address
    assert port == 4322


def test_serve_uses_explicit_port_when_given(monkeypatch):
    """Con un puerto explicito, serve lo pasa al servidor tal cual (Req 6.1/6.3)."""
    _FakeServer.last_server_address = None
    monkeypatch.setattr(
        "http.server.ThreadingHTTPServer", _FakeServer, raising=True
    )

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "dist").mkdir()

        build_site.serve(project, port=8080)

    assert _FakeServer.last_server_address is not None
    _host, port = _FakeServer.last_server_address
    assert port == 8080


# --- Req 6.2: error cuando falta dist/ -------------------------------------

def test_serve_raises_when_dist_missing_mentions_puriq_build(monkeypatch):
    """Sin `dist/`, serve lanza FileNotFoundError indicando ejecutar
    `puriq build` primero, sin llegar a crear el servidor (Req 6.2)."""
    # Si por error se intentara crear el servidor, lo detectariamos aqui.
    _FakeServer.last_server_address = None
    monkeypatch.setattr(
        "http.server.ThreadingHTTPServer", _FakeServer, raising=True
    )

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        # No se crea project/dist a proposito.

        with pytest.raises(FileNotFoundError) as excinfo:
            build_site.serve(project)

    message = str(excinfo.value)
    assert "dist" in message
    assert "puriq build" in message
    # Nunca se debio construir el servidor cuando falta dist/.
    assert _FakeServer.last_server_address is None
