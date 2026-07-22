"""Lógica pura de validación de Q&A y del catálogo de destinos de deploy (Tarea 6.1).

Dos validadores **puros** (sin E/S) del wizard, aptos para property-based testing:

- ``validate_qa_entry``: rechaza un ``QA_Entry`` cuya pregunta o respuesta esté
  vacía o sea solo espacios en blanco, con un mensaje que nombra el campo
  faltante (Req 5.4).
- ``validate_deploy_target``: acepta un destino de publicación solo si pertenece
  al catálogo soportado; cualquier otro se rechaza con un mensaje que lista los
  destinos válidos (Req 10.2).

El catálogo de destinos **reutiliza** la única fuente de verdad del proyecto:
``puriq.tools.deploy.ADAPTERS`` (derivada del registro de adaptadores de deploy).
Así el wizard no mantiene una lista divergente de la que efectivamente puede
publicar el core (invariante de capa fina).
"""
from __future__ import annotations

from puriq.tools.deploy import ADAPTERS

# Catálogo de destinos de publicación soportados (Req 10.2). Se toma tal cual de
# `puriq.tools.deploy.ADAPTERS` (fuente de verdad única: el registro de
# adaptadores de deploy), en lugar de hardcodear una lista que pueda divergir.
DEPLOY_TARGETS: tuple[str, ...] = tuple(ADAPTERS)


class QAValidationError(ValueError):
    """Error accionable: un ``QA_Entry`` tiene la pregunta o la respuesta vacía.

    El mensaje nombra el campo faltante (``pregunta``/``respuesta``) para que el
    Wizard_UI pueda mostrar la corrección en el paso correspondiente (Req 5.4, 7.3).
    """


class DeployTargetError(ValueError):
    """Error accionable: el destino de publicación no está en el catálogo.

    El mensaje lista los destinos válidos para que el Wizard_UI ofrezca una
    corrección (Req 10.2, 7.3).
    """


def _is_blank(value: object) -> bool:
    """Indica si `value` está ausente o es una cadena vacía / solo espacios.

    Trata ``None`` y cualquier valor no-string como faltante, y considera vacío
    cualquier string cuyo contenido sea solo espacios en blanco.
    """
    if value is None:
        return True
    if not isinstance(value, str):
        return True
    return not value.strip()


def validate_qa_entry(entry: object) -> dict[str, str]:
    """Valida un ``QA_Entry`` y devuelve su forma normalizada (Req 5.4).

    Un ``QA_Entry`` válido es un mapping con las claves ``question`` y ``answer``,
    ambas con texto no vacío (ignorando espacios en blanco). Esta función es
    **pura**: no lee ni escribe disco y no muta la entrada; solo valida y devuelve
    un nuevo dict con los valores recortados (``strip``).

    Args:
        entry: el ``QA_Entry`` a validar; se espera un mapping con ``question`` y
            ``answer``.

    Returns:
        Un dict ``{"question": <pregunta>, "answer": <respuesta>}`` con los textos
        recortados de espacios en los extremos.

    Raises:
        QAValidationError: si `entry` no es un mapping, o si la pregunta o la
            respuesta está vacía o es solo espacios; el mensaje nombra el campo
            faltante (Req 5.4).
    """
    if not isinstance(entry, dict):
        raise QAValidationError(
            "El QA_Entry debe incluir una 'question' (pregunta) y una 'answer' "
            "(respuesta) no vacías."
        )

    question = entry.get("question")
    answer = entry.get("answer")

    if _is_blank(question):
        raise QAValidationError(
            "La pregunta (question) del QA_Entry no puede estar vacía."
        )
    if _is_blank(answer):
        raise QAValidationError(
            "La respuesta (answer) del QA_Entry no puede estar vacía."
        )

    return {"question": question.strip(), "answer": answer.strip()}


def validate_deploy_target(target: object) -> str:
    """Valida que `target` pertenezca al catálogo de destinos soportados (Req 10.2).

    Función **pura**: no realiza E/S; solo comprueba pertenencia al catálogo
    ``DEPLOY_TARGETS`` (derivado de ``puriq.tools.deploy.ADAPTERS``).

    Args:
        target: nombre del destino de publicación elegido por el usuario.

    Returns:
        El mismo `target` cuando es un destino soportado.

    Raises:
        DeployTargetError: si `target` no es un string o no pertenece al catálogo;
            el mensaje lista los destinos válidos (Req 10.2).
    """
    validos = ", ".join(DEPLOY_TARGETS)
    if not isinstance(target, str) or target not in DEPLOY_TARGETS:
        raise DeployTargetError(
            f"Destino de publicación no soportado: {target!r}. "
            f"Destinos válidos: {validos}."
        )
    return target
