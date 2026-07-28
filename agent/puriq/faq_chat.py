"""Asistente del sitio publicado: responde con la información oficial del destino.

El chatweb del sitio generado tiene dos caminos (ver `template/src/modules/chatweb/Chat.astro`):

  A) **Sin `apiEndpoint`** — recuperación 100% client-side por solapamiento de
     tokens sobre el FAQ. No necesita red ni credenciales, pero no *entiende* la
     pregunta: devuelve la entrada que más palabras comparte, tal cual está
     escrita. Preguntar "¿qué es el Cerro Rico?" podía traer la respuesta sobre
     visitar las minas.
  B) **Con `apiEndpoint`** — se consulta un servicio que sí redacta. Este módulo
     es ese servicio.

**Por qué no hay vector store.** La base de conocimiento de un destino chico son
decenas de pares Q&A: la de Potosí ocupa ~420 tokens. Entra entera en el contexto
del modelo, así que no hay nada que "recuperar" — el problema no era encontrar el
fragmento, era redactar. Un índice vectorial (p. ej. Bedrock Knowledge Bases sobre
OpenSearch) agregaría infraestructura con costo continuo para resolver un problema
que a esta escala no existe. Si algún día la base creciera a miles de documentos,
la costura para meter recuperación previa es `knowledge`: basta con pasarle un
subconjunto ya filtrado.

**Sin transporte a propósito.** Acá no hay FastAPI ni Lambda: sólo `question` +
`knowledge` -> texto. El endpoint local del wizard y un futuro handler en la nube
comparten esta misma función, de modo que el asistente responde igual en los dos
lados (el mismo criterio de "un núcleo, varias superficies" del resto del agente).
"""
from __future__ import annotations

from puriq.tools.generate_content import LLMProvider, get_provider

#: Tope de caracteres de la pregunta. El endpoint queda expuesto al público del
#: sitio, así que una pregunta desmedida no debe convertirse en una llamada cara
#: al modelo. Es un limite de coste, no de validación semántica.
MAX_QUESTION_CHARS = 500

#: Qué contesta el asistente cuando la pregunta no está cubierta. Se devuelve sin
#: llamar al modelo cuando el sitio no tiene conocimiento cargado.
FALLBACK = (
    "No tengo esa información todavía. Escribinos y con gusto te respondemos."
)


class EmptyQuestionError(ValueError):
    """La pregunta llegó vacía."""


class QuestionTooLongError(ValueError):
    """La pregunta excede `MAX_QUESTION_CHARS`."""


def build_prompt(question: str, knowledge: list[dict], persona: str | None = None) -> str:
    """Arma el prompt: la información oficial como único material permitido.

    El encuadre es deliberadamente restrictivo. Este asistente habla en nombre de
    un gobierno o de un emprendimiento: inventar un horario, un precio o una
    recomendación de seguridad no es un detalle estético, es desinformar a un
    visitante que va a tomar decisiones con eso. Por eso se le prohíbe completar
    con conocimiento propio y se le da una salida explícita para decir que no
    sabe, que es la respuesta correcta cuando el dato no está.
    """
    voz = (persona or "").strip() or "institucional y cálido"
    bloques = "\n\n".join(
        f"P: {e['question']}\nR: {e['answer']}" for e in knowledge
    )
    return f"""\
Sos el asistente del sitio turístico y respondés preguntas de visitantes.
Tu tono es {voz}. Respondé en español, en 3 frases como máximo.

INFORMACIÓN OFICIAL (es lo único que sabés):

{bloques}

REGLAS:
- Respondé ÚNICAMENTE con la información de arriba. No la completes con lo que
  sepas por tu cuenta, aunque estés seguro.
- Si la pregunta no está cubierta, respondé exactamente: "{FALLBACK}"
- No inventes horarios, precios, teléfonos ni direcciones.
- No repitas la pregunta ni digas "según la información oficial": contestá
  directo, como lo haría alguien que atiende al visitante.

PREGUNTA DEL VISITANTE: {question}

RESPUESTA:"""


def answer_question(
    question: str,
    knowledge: list[dict],
    *,
    persona: str | None = None,
    provider: LLMProvider | None = None,
) -> str:
    """Responde `question` usando sólo `knowledge`; devuelve el texto de la respuesta.

    Args:
        question: la pregunta del visitante.
        knowledge: pares ``{"question", "answer"}`` (ver `build_site.load_knowledge`).
        persona: tono del asistente (`site.config.json -> modules.chatweb.persona`).
        provider: proveedor de LLM; por defecto el que resuelva `PURIQ_LLM_MODE`.
            Se inyecta para poder probar sin red.

    Raises:
        EmptyQuestionError: si la pregunta viene vacía.
        QuestionTooLongError: si supera `MAX_QUESTION_CHARS`.

    Sin conocimiento cargado se devuelve el fallback SIN llamar al modelo: pedirle
    que conteste sin material sólo puede terminar en una invención.
    """
    pregunta = (question or "").strip()
    if not pregunta:
        raise EmptyQuestionError("La pregunta está vacía.")
    if len(pregunta) > MAX_QUESTION_CHARS:
        raise QuestionTooLongError(
            f"La pregunta supera el máximo de {MAX_QUESTION_CHARS} caracteres."
        )
    if not knowledge:
        return FALLBACK

    motor = provider or get_provider()
    respuesta = motor.complete(build_prompt(pregunta, knowledge, persona))
    return (respuesta or "").strip() or FALLBACK
