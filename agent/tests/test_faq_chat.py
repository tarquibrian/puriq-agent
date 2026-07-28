"""Pruebas del asistente del sitio publicado (`puriq.faq_chat`).

El módulo redacta la respuesta que ve un visitante del sitio, hablando en nombre
del destino o del emprendimiento. Lo que se verifica acá es sobre todo lo que NO
debe pasar: responder sin material, inventar, o convertir el endpoint público en
una llamada cara al modelo.

Todas las pruebas inyectan un proveedor doble; ninguna toca la red.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq import faq_chat  # noqa: E402

CONOCIMIENTO = [
    {"question": "Como llego al Salar?", "answer": "Por bus desde Uyuni."},
    {"question": "Hay que aclimatarse?", "answer": "Si, esta a 3660 msnm."},
]


class ProveedorDoble:
    """Proveedor de LLM que registra el prompt y devuelve una respuesta fija."""

    def __init__(self, respuesta: str = "una respuesta"):
        self.respuesta = respuesta
        self.prompt: str | None = None
        self.llamadas = 0

    def complete(self, prompt: str) -> str:
        self.prompt = prompt
        self.llamadas += 1
        return self.respuesta


def test_incluye_todo_el_conocimiento_en_el_prompt():
    """El prompt lleva las Q&A completas: no hay recuperacion previa que filtre."""
    doble = ProveedorDoble()
    faq_chat.answer_question("Como llego?", CONOCIMIENTO, provider=doble)

    for entrada in CONOCIMIENTO:
        assert entrada["question"] in doble.prompt
        assert entrada["answer"] in doble.prompt


def test_el_prompt_prohibe_completar_con_conocimiento_propio():
    """La instruccion de no inventar viaja en el prompt (Req: no desinformar)."""
    doble = ProveedorDoble()
    faq_chat.answer_question("Cuanto sale?", CONOCIMIENTO, provider=doble)

    assert "ÚNICAMENTE" in doble.prompt
    assert faq_chat.FALLBACK in doble.prompt


def test_usa_la_persona_configurada_como_tono():
    doble = ProveedorDoble()
    faq_chat.answer_question(
        "Hola", CONOCIMIENTO, persona="cercano y breve", provider=doble
    )
    assert "cercano y breve" in doble.prompt


def test_sin_conocimiento_responde_el_fallback_sin_llamar_al_modelo():
    """Pedirle una respuesta sin material solo puede terminar en una invencion."""
    doble = ProveedorDoble()
    assert faq_chat.answer_question("Que hay?", [], provider=doble) == faq_chat.FALLBACK
    assert doble.llamadas == 0


def test_una_respuesta_vacia_del_modelo_cae_al_fallback():
    """Mejor decir que no se sabe que devolverle al visitante una burbuja vacia."""
    doble = ProveedorDoble(respuesta="   ")
    assert (
        faq_chat.answer_question("Como llego?", CONOCIMIENTO, provider=doble)
        == faq_chat.FALLBACK
    )


def test_pregunta_vacia_se_rechaza():
    with pytest.raises(faq_chat.EmptyQuestionError):
        faq_chat.answer_question("   ", CONOCIMIENTO, provider=ProveedorDoble())


def test_pregunta_demasiado_larga_se_rechaza_antes_de_llamar_al_modelo():
    """El endpoint queda expuesto al publico: el tope es de coste, no estetico."""
    doble = ProveedorDoble()
    with pytest.raises(faq_chat.QuestionTooLongError):
        faq_chat.answer_question(
            "x" * (faq_chat.MAX_QUESTION_CHARS + 1), CONOCIMIENTO, provider=doble
        )
    assert doble.llamadas == 0


def test_la_respuesta_se_devuelve_recortada():
    doble = ProveedorDoble(respuesta="  Por bus desde Uyuni.\n")
    assert (
        faq_chat.answer_question("Como llego?", CONOCIMIENTO, provider=doble)
        == "Por bus desde Uyuni."
    )
