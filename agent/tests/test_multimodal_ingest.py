"""Pruebas del Ingest_Router del spec `multimodal-ingest` (Tareas 2.2-2.7).

Cubre las invariantes de la Pieza 5 (ingesta e interpretación de archivos) sobre
`puriq.intake.ingest`: clasificación por extensión, rechazo sin efectos de tipos
no soportados, validación de imagen contra `normalize_asset_name`, validación de
tamaño previa a cualquier efecto y no-persistencia del binario del PDF, más
ejemplos del router y del extractor.

Las pruebas de propiedad usan Hypothesis con un mínimo de 100 iteraciones y operan
sobre las funciones puras del router (no tocan el bucle del chat ni la red). Cada
prueba de propiedad lleva el comentario
`# Feature: multimodal-ingest, Property {N}: ...`.
"""
from __future__ import annotations

import base64
import builtins
import io
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Aseguramos que `puriq` sea importable (mismo patrón que las pruebas existentes).
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.intake import ingest  # noqa: E402
from puriq.intake.ingest import (  # noqa: E402
    FileKind,
    IncomingFile,
    classify_file,
    extract_pdf_text,
    prepare_incoming,
)
from puriq.wizard.assets import IMAGE_EXTS, normalize_asset_name  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: construcción de PDFs mínimos reales (con y sin texto extraíble)
# ---------------------------------------------------------------------------

def _build_pdf(content_stream: bytes) -> bytes:
    """Ensambla un PDF de una página con `content_stream` como flujo de contenido.

    Produce un PDF 1.4 válido y mínimo (catálogo + páginas + página + contenido +
    fuente Helvetica), suficiente para que `pypdf` lo parse y extraiga el texto que
    el flujo dibuje con un operador `Tj`.
    """
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length "
        + str(len(content_stream)).encode()
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_pos = out.tell()
    n = len(objs) + 1
    out.write(f"xref\n0 {n}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        b"trailer\n<< /Size "
        + str(n).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_pos).encode()
        + b"\n%%EOF"
    )
    return out.getvalue()


def _pdf_with_text(text: str) -> bytes:
    """PDF de una página que dibuja `text` (sin caracteres que rompan la sintaxis)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    return _build_pdf(stream)


def _pdf_without_text() -> bytes:
    """PDF de una página sin ningún operador de texto (extracción vacía)."""
    return _build_pdf(b"")


def _snapshot(root: Path) -> set[str]:
    """Conjunto de rutas relativas de todos los archivos bajo `root`."""
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# Estrategias
# ---------------------------------------------------------------------------

_IMAGE_EXT_LIST = sorted(IMAGE_EXTS)  # p. ej. ['.avif', '.gif', '.jpeg', ...]

# Extensión "otra": letras minúsculas, no imagen y no .pdf.
_other_ext = (
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=5)
    .map(lambda s: "." + s)
    .filter(lambda e: e not in IMAGE_EXTS and e != ".pdf")
)

# Una extensión cualquiera de las categorías relevantes (imagen / pdf / otra / sin).
_ext_choice = st.one_of(
    st.sampled_from(_IMAGE_EXT_LIST),
    st.just(".pdf"),
    _other_ext,
    st.just(""),
)

# Stem no vacío y sin separadores ni punto (para que el sufijo sea exactamente la
# extensión elegida y no haya ambigüedad de "archivo oculto" con punto inicial).
_stem = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122, blacklist_characters="/\\.:"),
    min_size=1,
    max_size=12,
)

# Alfabeto seguro para texto embebido en un flujo PDF (sin '(', ')', '\\').
_PDF_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,-"


@st.composite
def _named_file(draw):
    """Genera `(filename, kind_esperado)` cubriendo imagen, pdf, otro y sin ext."""
    stem = draw(_stem)
    ext = draw(_ext_choice)
    if ext and draw(st.booleans()):
        ext = ext.upper()  # ejercitar la insensibilidad a mayúsculas
    filename = stem + ext
    low = ext.lower()
    if low in IMAGE_EXTS:
        kind = FileKind.IMAGE
    elif low == ".pdf":
        kind = FileKind.PDF
    else:
        kind = FileKind.UNSUPPORTED
    return filename, kind


def _normalize_raises(filename: str) -> bool:
    """True si `normalize_asset_name(filename, IMAGE_EXTS)` lanza `ValueError`."""
    try:
        normalize_asset_name(filename, IMAGE_EXTS)
        return False
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# Property 1 (Tarea 2.2): clasificación por extensión total y correcta
# ---------------------------------------------------------------------------
# Feature: multimodal-ingest, Property 1: La clasificación por extensión es total y correcta
@settings(max_examples=200, deadline=None)
@given(_named_file())
def test_property_1_classification_total_and_correct(nf):
    """`classify_file` devuelve IMAGE sii ext∈IMAGE_EXTS, PDF sii '.pdf', UNSUPPORTED si no.

    Validates: Requirements 1.1
    """
    filename, expected = nf
    assert classify_file(filename) is expected


# ---------------------------------------------------------------------------
# Property 2 (Tarea 2.3): los no soportados se rechazan sin efectos
# ---------------------------------------------------------------------------
# Feature: multimodal-ingest, Property 2: Los archivos no soportados se rechazan sin efectos
@settings(max_examples=150, deadline=None)
@given(_named_file(), st.binary(max_size=64))
def test_property_2_unsupported_rejected_without_effects(nf, content):
    """Un archivo no soportado produce un rechazo que lista los tipos y no genera artefactos.

    Validates: Requirements 1.4, 1.5
    """
    filename, kind = nf
    assume(kind is FileKind.UNSUPPORTED)

    result = prepare_incoming(
        [IncomingFile(filename, content)], supports_vision=True
    )

    # No hay ningún efecto: ni imagen, ni binario registrado, ni texto de PDF.
    assert result.image_blocks == []
    assert result.asset_binaries == {}
    assert result.pdf_texts == []
    # Se rechaza con un mensaje accionable que lista los tipos soportados (incl. PDF).
    assert result.rejected
    msg = " ".join(result.rejected).lower()
    assert ".pdf" in msg
    assert any(ext in msg for ext in _IMAGE_EXT_LIST)


# ---------------------------------------------------------------------------
# Property 3 (Tarea 2.4): la validación de imagen coincide con normalize_asset_name
# ---------------------------------------------------------------------------
# Feature: multimodal-ingest, Property 3: La validación de imagen coincide con normalize_asset_name
@settings(max_examples=200, deadline=None)
@given(_named_file())
def test_property_3_image_validation_matches_normalize(nf):
    """La imagen se acepta exactamente cuando `normalize_asset_name` no lanza; si no, se rechaza.

    Validates: Requirements 10.1, 10.2
    """
    filename, kind = nf
    # El dominio de la propiedad es el de imágenes vs no soportados (los PDF tienen
    # su propio tratamiento, ajeno a normalize_asset_name).
    assume(kind is not FileKind.PDF)

    raises = _normalize_raises(filename)
    result = prepare_incoming(
        [IncomingFile(filename, b"x")], supports_vision=False
    )

    if raises:
        # Rechazada: sin binario registrado, con mensaje que lista los formatos.
        assert result.asset_binaries == {}
        assert result.image_blocks == []
        assert result.rejected
        msg = " ".join(result.rejected).lower()
        assert any(ext in msg for ext in _IMAGE_EXT_LIST)
    else:
        # Aceptada: el binario queda registrado bajo su nombre normalizado.
        assert result.asset_binaries
        assert result.rejected == []


# ---------------------------------------------------------------------------
# Property 4 (Tarea 2.5): el tamaño se valida antes de cualquier efecto
# ---------------------------------------------------------------------------
# Feature: multimodal-ingest, Property 4: El tamaño se valida antes de cualquier efecto
@settings(max_examples=150, deadline=None)
@given(kind=st.sampled_from(["image", "pdf"]), extra=st.integers(min_value=1, max_value=256))
def test_property_4_size_validated_before_any_effect(kind, extra):
    """Un archivo sobredimensionado se rechaza indicando el límite, sin efectos ni extracción.

    Validates: Requirements 10.3, 10.4, 10.5
    """
    limit = 50  # límite reducido (parcheado) para no asignar megabytes por ejemplo

    if kind == "image":
        content = b"a" * (limit + extra)
        with mock.patch.object(ingest, "MAX_ASSET_BYTES", limit):
            result = prepare_incoming(
                [IncomingFile("foto.jpg", content)], supports_vision=True
            )
        # Ningún efecto: no se registró el binario ni se generó bloque de imagen.
        assert result.asset_binaries == {}
        assert result.image_blocks == []
        assert result.pdf_texts == []
    else:  # pdf
        content = b"%PDF-1.4\n" + b"a" * (limit + extra)
        spy = mock.Mock(return_value="no-debe-llamarse")
        with mock.patch.object(ingest, "MAX_PDF_BYTES", limit), mock.patch.object(
            ingest, "extract_pdf_text", spy
        ):
            result = prepare_incoming(
                [IncomingFile("doc.pdf", content)], supports_vision=True
            )
        # El extractor NO se invoca cuando el tamaño excede el límite.
        spy.assert_not_called()
        assert result.pdf_texts == []
        assert result.asset_binaries == {}
        assert result.image_blocks == []

    # En ambos casos: rechazo con un mensaje que indica el límite de tamaño.
    assert result.rejected
    assert "máximo" in " ".join(result.rejected).lower()


# ---------------------------------------------------------------------------
# Property 5 (Tarea 2.6): el binario del PDF nunca se persiste
# ---------------------------------------------------------------------------
# Feature: multimodal-ingest, Property 5: El binario del PDF nunca se persiste
@settings(max_examples=100, deadline=None)
@given(text=st.text(alphabet=_PDF_SAFE, min_size=1, max_size=60))
def test_property_5_pdf_binary_never_persisted(text):
    """Ingerir un PDF no crea ningún archivo bajo el proyecto: la extracción es en memoria.

    Validates: Requirements 3.5, 11.5
    """
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "assets").mkdir()
        before = _snapshot(project)

        pdf_bytes = _pdf_with_text(text)
        prepare_incoming(
            [IncomingFile("folleto.pdf", pdf_bytes)], supports_vision=False
        )

        after = _snapshot(project)

    # El sistema de archivos del proyecto no cambió: nada se escribió en disco.
    assert before == after


# ---------------------------------------------------------------------------
# Ejemplos (Tarea 2.7): router y extractor
# ---------------------------------------------------------------------------

def test_example_valid_image_produces_block_and_binary_no_extraction():
    """Imagen válida con visión → ImageBlock + asset_binaries, sin invocar el extractor (Req 1.2)."""
    content = b"\xff\xd8\xff\xe0binary-jpeg-bytes"
    spy = mock.Mock()
    with mock.patch.object(ingest, "extract_pdf_text", spy):
        result = prepare_incoming(
            [IncomingFile("cerro.jpg", content)], supports_vision=True
        )

    # El extractor de PDF nunca se toca para una imagen.
    spy.assert_not_called()
    # Se registra el binario bajo su nombre normalizado, para la inyección del agente.
    assert result.asset_binaries == {"cerro.jpg": content}
    # Con visión, se emite un bloque de imagen con base64 y media type raster.
    assert len(result.image_blocks) == 1
    block = result.image_blocks[0]
    assert block.media_type == "image/jpeg"
    assert block.data == base64.b64encode(content).decode("ascii")
    # No hay texto de PDF ni rechazos.
    assert result.pdf_texts == []
    assert result.rejected == []


def test_example_pdf_invokes_extractor_not_image_path():
    """Un PDF invoca el extractor y no el tratamiento de imagen (Req 1.3)."""
    content = b"%PDF-1.4 contenido binario"
    spy = mock.Mock(return_value="Texto del folleto")
    with mock.patch.object(ingest, "extract_pdf_text", spy):
        result = prepare_incoming(
            [IncomingFile("folleto.pdf", content)], supports_vision=True
        )

    # Se delegó en el extractor con los bytes del PDF.
    spy.assert_called_once_with(content)
    # El texto extraído entra como contexto; no se trató como imagen.
    assert result.pdf_texts == ["Texto del folleto"]
    assert result.image_blocks == []
    assert result.asset_binaries == {}
    assert result.rejected == []


def test_example_pdf_without_text_returns_actionable_message():
    """Un PDF sin texto legible produce un mensaje accionable en `rejected` (Req 3.6)."""
    result = prepare_incoming(
        [IncomingFile("escaneado.pdf", _pdf_without_text())], supports_vision=False
    )

    assert result.pdf_texts == []
    assert result.rejected
    msg = " ".join(result.rejected).lower()
    assert "no se pudo extraer" in msg
    assert "texto" in msg


def test_example_missing_pdf_extra_names_pdf_extra():
    """Si falta la PDF_Library, el error nombra el extra `pdf` a instalar (Req 9.4)."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf" or name.startswith("pypdf."):
            raise ModuleNotFoundError("No module named 'pypdf'")
        return real_import(name, *args, **kwargs)

    # extract_pdf_text debe re-lanzar un ModuleNotFoundError que nombre el extra.
    with mock.patch.dict(sys.modules, {}, clear=False):
        sys.modules.pop("pypdf", None)
        with mock.patch.object(builtins, "__import__", fake_import):
            with pytest.raises(ModuleNotFoundError) as excinfo:
                extract_pdf_text(b"%PDF-1.4 whatever")

    mensaje = str(excinfo.value)
    assert "pypdf" in mensaje
    assert "puriq[pdf]" in mensaje

    # Y a nivel del router, el fallo del extra se acumula como rechazo accionable.
    with mock.patch.dict(sys.modules, {}, clear=False):
        sys.modules.pop("pypdf", None)
        with mock.patch.object(builtins, "__import__", fake_import):
            result = prepare_incoming(
                [IncomingFile("folleto.pdf", b"%PDF-1.4 whatever")],
                supports_vision=False,
            )

    assert result.pdf_texts == []
    assert result.rejected
    assert "puriq[pdf]" in " ".join(result.rejected)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
