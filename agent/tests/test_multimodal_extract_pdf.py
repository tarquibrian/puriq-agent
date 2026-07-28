"""Pruebas de la Extract_PDF_Tool y del registro MCP (spec multimodal-ingest).

Implementa las tareas opcionales del Hito 6 del plan `multimodal-ingest`:

  - 6.2 -> Property 12: La Extract_PDF_Tool exige exactamente una fuente
           (Validates: Requirements 7.3).
  - 6.4 -> Property 13: El registro de tools es aditivo y conserva las existentes
           (Validates: Requirements 7.4, 7.5).
  - 6.3 -> Property 14: El Texto_Extraido se devuelve redactado
           (Validates: Requirements 11.2, 7.2).
  - 6.5 -> Ejemplos/integración de la tool y del MCP
           (Requirements 3.1, 7.1, 7.2, 7.4, 9.3).

Las pruebas de propiedad usan **Hypothesis** con un mínimo de 100 iteraciones,
operan sobre un proyecto temporal aislado por ejemplo y, cuando conviene,
parchean `intake/tools.extract_pdf_text` (el extractor que comparte el flujo web
y el MCP) para aislar la lógica de la tool del PDF_Extractor real. La cobertura
del PDF real (pypdf) se hace con un PDF de muestra construido en memoria, sin
depender de bibliotecas externas de generación (reportlab no está en el entorno).

Cada prueba de propiedad lleva el comentario de trazabilidad
`# Feature: multimodal-ingest, Property {N}: {texto}`.
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Asegurar que el paquete `puriq` sea importable al correr pytest desde cualquier dir.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq import config  # noqa: E402
from puriq.intake import ingest  # noqa: E402
from puriq.intake import tools  # noqa: E402
from puriq.mcp import server  # noqa: E402

#: Configuración común de PBT: >=100 iteraciones, sin deadline (E/S en tmp) y se
#: suprime el health-check de fixture de función (usamos `tmp_path` como raíz y
#: creamos un subdirectorio único por ejemplo, así que el aislamiento es real).
pbt = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- Nombres de tools registrados ANTES de esta fase (Property 13) -----------
#: Las 11 tools de pipeline y edición ya existentes antes del intake.
_EXISTING_TOOLS = {
    "scan_resources",
    "import_open_data",
    "generate_content",
    "build_site",
    "deploy",
    "manage_articles",
    "query_content",
    "edit_content",
    "delete_content",
    "bulk_update",
    "analyze_seo",
}

#: Las 12 intake tools previas a esta fase (Hito 1), incluida `attach_asset`.
#: `extract_pdf` NO está aquí: es la tool que esta fase agrega de forma aditiva.
_PREVIOUS_INTAKE_TOOLS = {
    "set_site",
    "configure_modules",
    "add_place",
    "add_event",
    "edit_item",
    "remove_item",
    "set_brand",
    "configure_landing",
    "add_qa",
    "attach_asset",
    "get_state",
    "build",
}

#: Todos los nombres registrados antes de agregar `extract_pdf` (11 + 12 = 23).
_PREEXISTING_TOOL_NAMES = sorted(_EXISTING_TOOLS | _PREVIOUS_INTAKE_TOOLS)


# --- Helpers -----------------------------------------------------------------
def _new_project(tmp_path: Path) -> Path:
    """Crea un subdirectorio único bajo `tmp_path` (aísla la E/S por ejemplo)."""
    return Path(tempfile.mkdtemp(dir=tmp_path))


def _build_simple_pdf(text: str) -> bytes:
    """Construye en memoria un PDF mínimo válido con `text` como contenido.

    Emite un único objeto de página con un content stream que dibuja el texto con
    un operador `Tj` y la fuente estándar Helvetica, de modo que `pypdf` pueda
    extraerlo con `page.extract_text()`. No depende de bibliotecas externas de
    generación de PDF (reportlab no está en el entorno).
    """
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref_pos = out.tell()
    n = len(objs) + 1
    out.write(b"xref\n0 %d\n" % n)
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\n" % n)
    out.write(b"startxref\n%d\n%%%%EOF" % xref_pos)
    return out.getvalue()


# =============================================================================
# Property 12 (task 6.2): La Extract_PDF_Tool exige exactamente una fuente
# =============================================================================
# Feature: multimodal-ingest, Property 12: La Extract_PDF_Tool exige exactamente una fuente
@pbt
@given(
    has_content=st.booleans(),
    has_source=st.booleans(),
    payload=st.binary(min_size=1, max_size=128),
)
def test_p12_extract_pdf_requires_exactly_one_source(
    tmp_path, has_content, has_source, payload
):
    """`extract_pdf` procede solo con exactamente una fuente; con ninguna o ambas rechaza.

    Se parchea el PDF_Extractor (`tools.extract_pdf_text`) para el caso válido, de
    modo que el foco de la propiedad sea la lógica de fuente única, no el parseo
    real del PDF.

    Validates: Requirements 7.3
    """
    project = _new_project(tmp_path)
    kwargs: dict[str, str] = {}
    if has_content:
        kwargs["content_base64"] = base64.b64encode(payload).decode("ascii")
    if has_source:
        src = project / "entrada.pdf"
        src.write_bytes(payload)
        kwargs["source_path"] = str(src)

    with mock.patch.object(tools, "extract_pdf_text", return_value="TEXTO EXTRAIDO"):
        if has_content ^ has_source:
            # Exactamente una fuente -> procede y devuelve el texto redactado.
            result = tools.extract_pdf(project, **kwargs)
            assert result == {"text": "TEXTO EXTRAIDO"}
        else:
            # Ninguna o ambas -> rechazo accionable (ValueError).
            with pytest.raises(ValueError):
                tools.extract_pdf(project, **kwargs)


# =============================================================================
# Property 13 (task 6.4): El registro de tools es aditivo y conserva las existentes
# =============================================================================
# Feature: multimodal-ingest, Property 13: El registro de tools es aditivo y conserva las existentes
@pbt
@given(name=st.sampled_from(_PREEXISTING_TOOL_NAMES))
def test_p13_tool_registry_is_additive(name):
    """Toda tool previa sigue en `TOOL_SPECS` tras agregar `extract_pdf`, que además está presente.

    Validates: Requirements 7.4, 7.5
    """
    registered = {spec["name"] for spec in server.TOOL_SPECS}

    # La tool previa (pipeline/edición o intake, incluida attach_asset) se conserva.
    assert name in registered, name

    # La Extract_PDF_Tool quedó registrada de forma aditiva por MCP...
    assert "extract_pdf" in registered
    # ...y también en la superficie de intake (INTAKE_TOOL_NAMES).
    assert "extract_pdf" in tools.INTAKE_TOOL_NAMES
    # attach_asset se conserva explícitamente (Req 7.4).
    assert "attach_asset" in tools.INTAKE_TOOL_NAMES
    assert "attach_asset" in registered


# =============================================================================
# Property 14 (task 6.3): El Texto_Extraido se devuelve redactado
# =============================================================================
_ASCII_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_secret_value = st.text(alphabet=_ASCII_ALNUM, min_size=12, max_size=30)


class _RegisteredSecret:
    """Registra un valor como secreto (vía `config.get_env(secret=True)`) y restaura.

    Sigue el patrón del Hito 1 (`test_intake_tools_properties._registered_secret`):
    fija una variable de entorno con el valor, la registra como secreto para que
    `redact`/`redact_value` la enmascaren, y al salir restaura el conjunto de
    nombres de secreto y la variable de entorno.
    """

    _ENV_NAME = "PURIQ_TEST_SECRET_P14_EXTRACT"

    def __init__(self, value: str) -> None:
        self._value = value

    def __enter__(self) -> "_RegisteredSecret":
        self._saved_env = os.environ.get(self._ENV_NAME)
        self._saved_names = set(config._secret_names)
        os.environ[self._ENV_NAME] = self._value
        config.get_env(self._ENV_NAME, secret=True)
        return self

    def __exit__(self, *exc: object) -> None:
        config._secret_names = self._saved_names
        if self._saved_env is None:
            os.environ.pop(self._ENV_NAME, None)
        else:
            os.environ[self._ENV_NAME] = self._saved_env


# Feature: multimodal-ingest, Property 14: El Texto_Extraido y el Contenido_Derivado se devuelven redactados
@pbt
@given(
    secret=_secret_value,
    prefix=st.text(max_size=20),
    suffix=st.text(max_size=20),
)
def test_p14_extracted_text_is_redacted(tmp_path, secret, prefix, suffix):
    """Si el Texto_Extraido contuviera un secreto registrado, la salida no lo expone crudo.

    Se parchea el PDF_Extractor para devolver un texto que embebe el valor del
    secreto; `extract_pdf` debe devolverlo redactado (Req 11.2), sin el valor crudo.

    Validates: Requirements 11.2, 7.2
    """
    project = _new_project(tmp_path)
    content_b64 = base64.b64encode(b"pdf-bytes-irrelevantes").decode("ascii")
    texto_con_secreto = f"{prefix}{secret}{suffix}"

    with _RegisteredSecret(secret):
        with mock.patch.object(
            tools, "extract_pdf_text", return_value=texto_con_secreto
        ):
            result = tools.extract_pdf(project, content_base64=content_b64)
        serialized = json.dumps(result, ensure_ascii=False)
        assert secret not in serialized


# =============================================================================
# Ejemplos e integración (task 6.5)
# =============================================================================
def _extract_pdf_spec() -> dict:
    """Devuelve la spec de `extract_pdf` tal como la expone el MCP (`TOOL_SPECS`)."""
    return next(s for s in server.TOOL_SPECS if s["name"] == "extract_pdf")


def test_extract_pdf_in_mcp_tool_specs_with_input_schema():
    """`extract_pdf` está en `TOOL_SPECS` del MCP con su inputSchema (Req 7.1)."""
    spec = _extract_pdf_spec()
    schema = spec["inputSchema"]

    assert schema["type"] == "object"
    props = schema["properties"]
    # Todas opcionales: `project` cae al ultimo proyecto abierto con `start.sh`,
    # y el PDF llega por `content_base64` O por `source_path`.
    assert "project" in props
    assert "content_base64" in props
    assert "source_path" in props
    assert schema.get("required", []) == []
    # Superficie cerrada.
    assert schema["additionalProperties"] is False
    # Tiene descripción y handler cableado.
    assert spec["description"].strip()
    assert callable(spec["handler"])


def test_attach_asset_still_registered():
    """`attach_asset` sigue presente en el MCP y en las intake tools (Req 7.4)."""
    registered = {spec["name"] for spec in server.TOOL_SPECS}
    assert "attach_asset" in registered
    assert "attach_asset" in tools.INTAKE_TOOL_NAMES


def test_extract_pdf_delegates_to_extractor_and_returns_text():
    """`extract_pdf` delega en el PDF_Extractor y devuelve `{text}` (Req 7.2)."""
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        raw = b"bytes-del-pdf-de-prueba"
        content_b64 = base64.b64encode(raw).decode("ascii")

        with mock.patch.object(
            tools, "extract_pdf_text", return_value="TEXTO DEL EXTRACTOR"
        ) as extractor:
            result = tools.extract_pdf(project, content_base64=content_b64)

        assert result == {"text": "TEXTO DEL EXTRACTOR"}
        # Delegó exactamente una vez con los bytes decodificados del PDF.
        extractor.assert_called_once_with(raw)


def test_extract_pdf_via_run_intake_tool_returns_text():
    """El MCP enruta `extract_pdf` por `run_intake_tool` y obtiene `{text}` (Req 7.2)."""
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        content_b64 = base64.b64encode(b"pdf").decode("ascii")

        with mock.patch.object(tools, "extract_pdf_text", return_value="OK"):
            result = tools.run_intake_tool(
                "extract_pdf",
                {"project": str(project), "content_base64": content_b64},
            )

        assert result == {"text": "OK"}


def test_extract_pdf_text_reads_real_pdf():
    """PDF real de muestra: `extract_pdf_text` (pypdf) devuelve su texto (Req 3.1, 9.3)."""
    esperado = "Hola Potosi Turismo 123"
    data = _build_simple_pdf(esperado)

    texto = ingest.extract_pdf_text(data)

    assert esperado in texto


def test_extract_pdf_end_to_end_with_real_pdf_source_path():
    """`extract_pdf` con un PDF real por `source_path` devuelve su texto (Req 3.1, 7.2, 9.3)."""
    esperado = "Folleto Turistico Sucre"
    data = _build_simple_pdf(esperado)

    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        pdf_path = project / "folleto.pdf"
        pdf_path.write_bytes(data)

        result = tools.extract_pdf(project, source_path=str(pdf_path))

        assert "text" in result
        assert esperado in result["text"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
