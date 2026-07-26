"""Paridad de comportamiento tras reubicar helpers del wizard (Tarea 1.5, Req 1.2).

Verifica que la reubicacion de helpers de `wizard/server.py` a modulos neutrales
(sin FastAPI) preservo su comportamiento (DD-3, DD-4) y que el servidor web ahora
importa esos simbolos de los modulos reubicados en vez de definirlos localmente:

- `wizard/asset_store.py`: `next_available_asset` y `append_image` (Req 11.4, 11.5).
- `wizard/qa_store.py`: `append_qa_entry` y `register_knowledge_source` (Req 5.1, 5.2, 5.3).
- `wizard/assets.py`: constante `MAX_ASSET_BYTES` (Req 4.5, 10.3).
- `config.py`: `redact_value` recursivo (Req 9.3, 12.2).
- Paridad de import: `wizard/server.py` reexporta EXACTAMENTE los mismos objetos.

Las pruebas de E/S operan sobre un proyecto temporal (`tmp_path`), sin levantar la
app FastAPI ni tocar la red. La prueba de paridad importa `wizard/server.py`; si el
import fallara por una dependencia ausente, se documenta con un skip explicito.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from puriq import config
from puriq.wizard import assets, asset_store, contracts, qa_store


# --- Helpers de siembra del proyecto temporal --------------------------------

def _seed_tourism_data(project: Path, places=None, events=None) -> None:
    """Escribe `tourism-data.json` con Places/Events validos minimos."""
    doc = {
        "site": {
            "name": "Uyuni",
            "region": "Potosi",
            "defaultLocale": "es",
            "center": {"lat": -20.46, "lng": -66.82},
        },
        "places": places or [],
        "events": events or [],
    }
    (project / contracts.DATA).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )


def _valid_place(pid: str) -> dict:
    return {
        "id": pid,
        "name": pid.replace("-", " ").title(),
        "category": "atractivo",
        "coords": {"lat": -20.46, "lng": -66.82},
    }


# --- asset_store.next_available_asset (Req 11.4) -----------------------------

def test_next_available_asset_returns_name_when_free(tmp_path):
    """Si el nombre no existe, se devuelve tal cual y su ruta resuelta contenida."""
    name, path = asset_store.next_available_asset(tmp_path, "logo.png")
    assert name == "logo.png"
    assert path == (tmp_path / "assets" / "logo.png").resolve()


def test_next_available_asset_disambiguates_collision_with_numeric_suffix(tmp_path):
    """Un nombre colisionante se desambigua con sufijo numerico sin pisar el previo."""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "logo.png").write_bytes(b"x")

    name, path = asset_store.next_available_asset(tmp_path, "logo.png")
    assert name == "logo-1.png"
    assert not path.exists()  # devuelve un nombre libre, no crea el archivo

    # Con logo.png y logo-1.png ocupados, avanza al siguiente libre.
    (assets_dir / "logo-1.png").write_bytes(b"y")
    name2, _ = asset_store.next_available_asset(tmp_path, "logo.png")
    assert name2 == "logo-2.png"


def test_next_available_asset_rejects_path_escaping_assets(tmp_path):
    """Un nombre que escapa de /assets se rechaza via resolve_within_assets (Req 11.4)."""
    with pytest.raises(ValueError):
        asset_store.next_available_asset(tmp_path, "../evil.png")


# --- asset_store.append_image (Req 11.5) -------------------------------------

def test_append_image_appends_without_duplicating(tmp_path):
    """append_image anexa la ruta a `images` y no la duplica en llamadas repetidas."""
    _seed_tourism_data(tmp_path, places=[_valid_place("plaza-mayor")])

    merged = asset_store.append_image(
        tmp_path, "places", "plaza-mayor", "assets/plaza.jpg"
    )
    place = next(p for p in merged["places"] if p["id"] == "plaza-mayor")
    assert place["images"] == ["assets/plaza.jpg"]

    # Reasociar la MISMA ruta no la duplica.
    merged2 = asset_store.append_image(
        tmp_path, "places", "plaza-mayor", "assets/plaza.jpg"
    )
    place2 = next(p for p in merged2["places"] if p["id"] == "plaza-mayor")
    assert place2["images"] == ["assets/plaza.jpg"]

    # Una ruta distinta si se anexa.
    merged3 = asset_store.append_image(
        tmp_path, "places", "plaza-mayor", "assets/plaza-2.jpg"
    )
    place3 = next(p for p in merged3["places"] if p["id"] == "plaza-mayor")
    assert place3["images"] == ["assets/plaza.jpg", "assets/plaza-2.jpg"]

    # La persistencia en disco refleja el resultado.
    on_disk = json.loads((tmp_path / contracts.DATA).read_text(encoding="utf-8"))
    disk_place = next(p for p in on_disk["places"] if p["id"] == "plaza-mayor")
    assert disk_place["images"] == ["assets/plaza.jpg", "assets/plaza-2.jpg"]


def test_append_image_raises_when_entity_id_missing(tmp_path):
    """Si el id no existe, append_image lanza ValueError accionable (no crea entidad)."""
    _seed_tourism_data(tmp_path, places=[_valid_place("plaza-mayor")])
    with pytest.raises(ValueError):
        asset_store.append_image(tmp_path, "places", "no-existe", "assets/x.jpg")


# --- qa_store.append_qa_entry (Req 5.1, 5.3) ---------------------------------

def test_append_qa_entry_creates_file_and_returns_relpath(tmp_path):
    """Crea content/qa.json, anexa sin borrar y devuelve la ruta relativa."""
    rel = qa_store.append_qa_entry(
        tmp_path, {"question": "Q1?", "answer": "A1"}
    )
    assert rel == "content/qa.json"

    qa_path = tmp_path / "content" / "qa.json"
    assert qa_path.exists()
    assert json.loads(qa_path.read_text(encoding="utf-8")) == [
        {"question": "Q1?", "answer": "A1"}
    ]

    # Una segunda entrada se ANEXA (no borra la anterior).
    rel2 = qa_store.append_qa_entry(
        tmp_path, {"question": "Q2?", "answer": "A2"}
    )
    assert rel2 == "content/qa.json"
    entries = json.loads(qa_path.read_text(encoding="utf-8"))
    assert entries == [
        {"question": "Q1?", "answer": "A1"},
        {"question": "Q2?", "answer": "A2"},
    ]


def test_append_qa_entry_is_idempotent_for_repeated_entry(tmp_path):
    """Una entrada repetida no se duplica; entradas distintas se siguen anexando.

    Criterio de deduplicacion: misma pregunta (recortada e insensible a
    mayusculas/minusculas) y misma respuesta (recortada). El llamado repetido no
    lanza y devuelve igual la ruta relativa `content/qa.json`.
    """
    qa_path = tmp_path / "content" / "qa.json"

    rel = qa_store.append_qa_entry(tmp_path, {"question": "¿Horario?", "answer": "9 a 18h"})
    assert rel == "content/qa.json"

    # Exactamente la misma entrada: no se duplica.
    rel_dup = qa_store.append_qa_entry(
        tmp_path, {"question": "¿Horario?", "answer": "9 a 18h"}
    )
    assert rel_dup == "content/qa.json"
    assert json.loads(qa_path.read_text(encoding="utf-8")) == [
        {"question": "¿Horario?", "answer": "9 a 18h"}
    ]

    # Misma entrada con espacios y capitalizacion distinta en la pregunta:
    # tampoco se duplica.
    qa_store.append_qa_entry(
        tmp_path, {"question": "  ¿HORARIO?  ", "answer": "  9 a 18h  "}
    )
    assert len(json.loads(qa_path.read_text(encoding="utf-8"))) == 1

    # Una entrada distinta (misma pregunta, otra respuesta) SI se anexa.
    qa_store.append_qa_entry(tmp_path, {"question": "¿Horario?", "answer": "10 a 17h"})
    # Y una pregunta nueva tambien.
    qa_store.append_qa_entry(tmp_path, {"question": "¿Precio?", "answer": "20 Bs"})
    assert json.loads(qa_path.read_text(encoding="utf-8")) == [
        {"question": "¿Horario?", "answer": "9 a 18h"},
        {"question": "¿Horario?", "answer": "10 a 17h"},
        {"question": "¿Precio?", "answer": "20 Bs"},
    ]


# --- qa_store.register_knowledge_source (Req 5.2) ----------------------------

def test_register_knowledge_source_creates_chatweb_module(tmp_path):
    """Sin chatweb previo, crea el modulo con enabled/order y el knowledgeSource."""
    merged = qa_store.register_knowledge_source(tmp_path, "content/qa.json")
    chatweb = merged["modules"]["chatweb"]
    assert chatweb["knowledgeSource"] == "content/qa.json"
    assert chatweb["enabled"] is True
    assert isinstance(chatweb["order"], int) and chatweb["order"] >= 1


def test_register_knowledge_source_order_is_next_after_max(tmp_path):
    """Con otros modulos presentes, el chatweb nuevo toma order = max + 1."""
    doc = {
        "layout": "clasico",
        "modules": {
            "map": {"enabled": True, "order": 1},
            "places": {"enabled": True, "order": 3},
        },
    }
    (tmp_path / contracts.CONFIG).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )

    merged = qa_store.register_knowledge_source(tmp_path, "content/qa.json")
    assert merged["modules"]["chatweb"]["order"] == 4
    # No se destruyen los modulos previos.
    assert merged["modules"]["map"]["order"] == 1
    assert merged["modules"]["places"]["order"] == 3


def test_register_knowledge_source_preserves_existing_enabled_and_order(tmp_path):
    """Si chatweb ya existe con enabled/order, solo se actualiza knowledgeSource."""
    doc = {
        "layout": "clasico",
        "modules": {
            "chatweb": {"enabled": False, "order": 7, "knowledgeSource": "viejo.json"},
        },
    }
    (tmp_path / contracts.CONFIG).write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )

    merged = qa_store.register_knowledge_source(tmp_path, "content/qa.json")
    chatweb = merged["modules"]["chatweb"]
    assert chatweb["knowledgeSource"] == "content/qa.json"
    assert chatweb["enabled"] is False  # preservado
    assert chatweb["order"] == 7  # preservado


# --- assets.MAX_ASSET_BYTES (Req 4.5, 10.3) ----------------------------------

def test_max_asset_bytes_lives_in_assets_with_expected_value():
    """MAX_ASSET_BYTES vive en wizard/assets.py y vale 10 MiB."""
    assert hasattr(assets, "MAX_ASSET_BYTES")
    assert assets.MAX_ASSET_BYTES == 10 * 1024 * 1024


# --- config.redact_value recursivo (Req 9.3, 12.2) ---------------------------

@pytest.fixture()
def _isolated_secret(monkeypatch):
    """Registra un secreto de prueba en config y aisla el estado global del modulo."""
    monkeypatch.setattr(config, "_dotenv_loaded", True)
    original = set(config._secret_names)
    secret = "SUPER-SECRET-VALUE-123"
    monkeypatch.setenv("PURIQ_TEST_SECRET", secret)
    config.get_env("PURIQ_TEST_SECRET", secret=True)  # registra como secreto
    yield secret
    config._secret_names.clear()
    config._secret_names.update(original)


def test_redact_value_exists_in_config():
    """redact_value vive en config.py."""
    assert hasattr(config, "redact_value")


def test_redact_value_masks_secrets_recursively(_isolated_secret):
    """redact_value enmascara strings en dict/list/tuple de forma recursiva."""
    secret = _isolated_secret
    structure = {
        "msg": f"conexion con {secret}",
        "nested": {"key": secret},
        "items": [secret, "sano", {"deep": secret}],
        "tup": (secret, "ok"),
    }
    result = config.redact_value(structure)

    assert secret not in result["msg"] and "***" in result["msg"]
    assert result["nested"]["key"] == "***"
    assert result["items"][0] == "***"
    assert result["items"][1] == "sano"
    assert result["items"][2]["deep"] == "***"
    # Las tuplas se devuelven como listas (forma serializable a JSON).
    assert isinstance(result["tup"], list)
    assert result["tup"] == ["***", "ok"]


def test_redact_value_leaves_non_strings_untouched(_isolated_secret):
    """Los valores no-string (numeros, bool, None) se devuelven sin cambios."""
    structure = {"n": 42, "b": True, "none": None, "f": 3.14}
    assert config.redact_value(structure) == {"n": 42, "b": True, "none": None, "f": 3.14}


# --- Paridad de import: server.py reexporta los helpers reubicados -----------

def test_server_reexports_relocated_symbols():
    """wizard/server.py importa los helpers de los modulos reubicados, no los define."""
    try:
        from puriq.wizard import server
    except Exception as exc:  # pragma: no cover - documenta dependencia ausente
        pytest.skip(f"No se pudo importar wizard/server.py: {exc!r}")

    # Mismo objeto -> el servidor reexporta, no redefine (paridad DD-3/DD-4).
    assert server.next_available_asset is asset_store.next_available_asset
    assert server.append_image is asset_store.append_image
    assert server.append_qa_entry is qa_store.append_qa_entry
    assert server.register_knowledge_source is qa_store.register_knowledge_source
    assert server.MAX_ASSET_BYTES is assets.MAX_ASSET_BYTES
    assert server.redact_value is config.redact_value
