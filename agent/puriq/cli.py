"""Interfaz de linea de comandos de Puriq.

Comandos principales:
    puriq init      -> abre el wizard web local (flujo principal para no-tecnicos)
    puriq build     -> construye el sitio desde los archivos del proyecto (headless)
    puriq preview   -> sirve el sitio construido en local
    puriq deploy    -> publica el sitio (aws-amplify | s3-cloudfront | static-export)

El CLI es una capa fina: toda la logica vive en puriq.core.

Manejo de errores transversal (Req 9): cada comando se envuelve con
`@manejar_errores`, que captura las excepciones que lanzan las tools/core, las
presenta con `rich` como un mensaje descriptivo (causa + accion sugerida),
enmascara los valores de secretos con `puriq.config.redact` y termina el proceso
con codigo distinto de cero. No se capturan errores de forma silenciosa: siempre
se muestra un mensaje claro y se sale con codigo 1.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Callable, TypeVar

import typer
from rich import print
from rich.console import Console
from rich.table import Table

from puriq import config
from puriq.errors import describir_error

app = typer.Typer(help="Puriq: de recursos turisticos dispersos a un sitio profesional.")

# Consola dedicada a stderr para los mensajes de error (no contamina stdout).
_err_console = Console(stderr=True)

F = TypeVar("F", bound=Callable[..., object])

# La traduccion de errores vive ahora en `puriq.errors` (DD-4), como unica fuente
# de verdad compartida entre el CLI y el wizard web. Se mantiene el alias
# `_describir_error` para no romper el contrato interno del CLI ni sus pruebas.
_describir_error = describir_error


def manejar_errores(func: F) -> F:
    """Decorador que captura excepciones de un comando y las presenta con `rich`.

    Envuelve la ejecucion de un comando del CLI: si la tool/core lanza una
    excepcion, la traduce a un mensaje descriptivo (causa + accion sugerida),
    enmascara los secretos con `redact` (Req 9.3) y sale con codigo 1 (Req 9.1).
    Deja pasar `typer.Exit` y `typer.Abort` sin envolverlas para preservar el
    control de flujo propio de typer.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            # Flujo de control de typer: no es un error de tool.
            raise
        except Exception as exc:  # noqa: BLE001 - se re-lanza como salida controlada
            causa, accion = _describir_error(exc)
            # Enmascarar SIEMPRE antes de imprimir para no exponer secretos.
            causa = config.redact(causa)
            _err_console.print(f"[bold red]Error:[/] {causa}")
            if accion:
                accion = config.redact(accion)
                _err_console.print(f"[yellow]Sugerencia:[/] {accion}")
            raise typer.Exit(code=1)

    return wrapper  # type: ignore[return-value]


#: Motores de LLM que sirven para conversar. `local` (Ollama) queda fuera a
#: proposito: es text-only, no admite tool-use, y elegirlo aca prometeria un chat
#: que despues no funciona.
_MOTORES = {
    "openai": (
        "OpenAI, Azure, Groq, OpenRouter, LM Studio…",
        [
            ("PURIQ_OPENAI_API_KEY", "Clave de API", True),
            ("PURIQ_OPENAI_BASE_URL", "URL base", False),
            ("PURIQ_OPENAI_MODEL", "Modelo (en Azure, el deployment)", False),
        ],
    ),
    "bedrock": (
        "Amazon Bedrock (familia Claude)",
        [
            ("AWS_ACCESS_KEY_ID", "Access key de AWS", True),
            ("AWS_SECRET_ACCESS_KEY", "Secret key de AWS", True),
            ("AWS_REGION", "Región", False),
        ],
    ),
}


@app.command()
@manejar_errores
def demo(port: int = 4322):
    """Genera y sirve un sitio de ejemplo completo, sin credenciales."""
    import shutil
    import tempfile

    from puriq import paths
    from puriq.core import Puriq

    origen = paths.examples_dir() / "potosi-bo"
    if not origen.is_dir():
        print("[red]No se encontró el proyecto de ejemplo.[/]")
        raise typer.Exit(code=1)

    # Se copia a un temporal en vez de construir sobre el original: instalado con
    # pipx el ejemplo vive dentro del paquete, que es de solo lectura y no debe
    # ensuciarse con `dist/` ni con las dependencias de la plantilla.
    destino = Path(tempfile.mkdtemp(prefix="puriq-demo-")) / "potosi-bo"
    shutil.copytree(origen, destino)

    print("[bold]Generando el sitio de ejemplo…[/] (no usa ningún LLM)")
    Puriq(destino).build(use_llm=False)
    print(f"[bold green]Listo.[/] Abrí http://localhost:{port} — Ctrl+C para cortar.")
    Puriq(destino).preview(port=port)


@app.command("mcp-connect")
@manejar_errores
def mcp_connect(si: bool = typer.Option(False, "--si", help="No preguntar; asumir que sí.")):
    """Registra Puriq en los clientes MCP instalados (Claude Desktop, Kiro…)."""
    import sys

    from puriq import mcp_connect as conectar

    raise typer.Exit(code=conectar.main(python=sys.executable, asumir_si=si))


@app.command()
@manejar_errores
def config_llm():
    """Configura tu clave de LLM para el chat del asistente (opcional)."""
    # Sin este comando, quien instala Puriq con pipx no tiene forma razonable de
    # configurar credenciales: no hay `.env.example` que copiar y los nombres de
    # las variables no se pueden adivinar. Se escribe en `~/.puriq/.env`, que es
    # lo primero que lee `config._dotenv_path()` y sobrevive a las
    # actualizaciones del paquete.
    from puriq.config import _dotenv_candidates

    destino = _dotenv_candidates()[0]

    print()
    print("[bold]Esto es opcional.[/] Puriq funciona sin ninguna clave:")
    print("  · [green]puriq demo[/] genera un sitio completo sin credenciales.")
    print("  · [green]puriq mcp-connect[/] conecta Puriq a Claude Desktop o Kiro,")
    print("    y ahí el modelo lo pone tu cliente con su propia suscripción.")
    print()
    print("Una clave sólo hace falta para el [bold]chat integrado[/] del asistente web.")
    print()

    for clave, (titulo, _) in _MOTORES.items():
        print(f"  [bold]{clave}[/] — {titulo}")
    print()

    motor = typer.prompt("¿Qué motor querés usar?", default="openai").strip().lower()
    if motor not in _MOTORES:
        print(f"[red]Motor desconocido:[/] {motor}. Opciones: {', '.join(_MOTORES)}.")
        raise typer.Exit(code=1)

    valores = {"PURIQ_LLM_MODE": motor}
    for var, etiqueta, secreto in _MOTORES[motor][1]:
        # `hide_input` en las claves: no tienen por que quedar en el scrollback
        # de la terminal ni en una captura de pantalla.
        valor = typer.prompt(f"  {etiqueta}", default="", hide_input=secreto).strip()
        if valor:
            valores[var] = valor

    destino.parent.mkdir(parents=True, exist_ok=True)
    # Se fusiona con lo que ya hubiera: el archivo puede tener otras variables
    # (destino de deploy, índice de geocoding) que no vienen al caso aquí.
    previas: dict[str, str] = {}
    if destino.is_file():
        for linea in destino.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, _, v = linea.partition("=")
                previas[k.strip()] = v.strip()
    previas.update(valores)

    destino.write_text(
        "# Credenciales de Puriq. No compartas este archivo.\n"
        + "".join(f"{k}={v}\n" for k, v in previas.items()),
        encoding="utf-8",
    )
    destino.chmod(0o600)

    print()
    print(f"[bold green]Listo.[/] Guardado en {destino} (sólo lectura para vos).")
    print("Abrí el asistente con [green]puriq init[/] y usá el chat.")


@app.command()
@manejar_errores
def init(port: int = 4321):
    """Lanza el asistente web local (wizard) para configurar el proyecto."""
    from puriq.wizard.server import serve

    print(f"[bold green]Puriq[/] wizard en http://localhost:{port}")
    serve(port=port)


@app.command()
@manejar_errores
def collect(project: Path = Path("."), resources: Path = Path("raw"), enrich: bool = False):
    """Lee los recursos crudos (site.json + CSVs) y genera un tourism-data.json validado."""
    from puriq.core import Puriq

    res = project / resources if not resources.is_absolute() else resources
    Puriq(project).collect(res, enrich=enrich)
    print(f"[bold green]Puriq[/] contrato generado en {project / 'tourism-data.json'}")


@app.command()
@manejar_errores
def build(project: Path = Path("."), use_llm: bool = True):
    """Valida el contrato, genera contenido y ensambla el sitio estatico."""
    from puriq.core import Puriq

    # El build tarda (instala dependencias y corre Astro) y antes no imprimia
    # NADA: no se distinguia un build en curso de uno colgado, y los avisos que
    # el core emite por `progress` -como las imagenes declaradas que faltan- solo
    # los veia el wizard. Se pasa un `progress` que los muestra en la terminal.
    def mostrar(mensaje: str) -> None:
        if mensaje.lower().startswith("aviso"):
            print(f"[yellow]{mensaje}[/]")
        else:
            print(f"[dim]{mensaje}[/]")

    dist = Puriq(project).build(use_llm=use_llm, progress=mostrar)
    print(f"[bold green]Puriq[/] sitio construido en {dist}")


@app.command()
@manejar_errores
def preview(project: Path = Path("."), port: int = 4322):
    """Sirve el sitio ya construido para previsualizarlo."""
    from puriq.core import Puriq

    Puriq(project).preview(port=port)


@app.command()
@manejar_errores
def deploy(project: Path = Path("."), target: str = "aws-amplify"):
    """Publica el sitio construido en el destino elegido."""
    from puriq.core import Puriq

    Puriq(project).deploy(target=target)


# --- gestión de contenido (content-management) -----------------------------
# Subcomandos finos que delegan en los mismos métodos de `puriq.core.Puriq` que
# usa el MCP (Req 11.3), sin duplicar lógica. Cada uno se envuelve con
# `@manejar_errores` (mensajes descriptivos + `config.redact`, Req 12.4) e
# instancia `Puriq(project)` con la misma opción `project: Path = Path(".")` que
# los comandos existentes.
#
# Entrada de campos en la edición (documentado): los comandos de Places/Events
# (`edit`) aceptan la opción repetible `--set clave=valor` (p. ej.
# `--set name="Plaza Mayor" --set category=parque`). Para campos de tipo lista
# (`tags`, `images`) el valor se separa por comas: `--set tags=cultura,historia`.
# Los comandos de artículos exponen los campos como opciones dedicadas y las
# etiquetas se pasan como lista separada por comas en `--tags`.


def _split_tags(value: str | None) -> list[str] | None:
    """Convierte una cadena separada por comas en una lista de etiquetas.

    Devuelve ``None`` cuando no se aporta valor (para no tocar el campo en un
    merge) y una lista (posiblemente vacía) cuando sí se aporta.
    """
    if value is None:
        return None
    return [t.strip() for t in value.split(",") if t.strip()]


# Campos que se interpretan como listas al parsear `--set clave=valor`.
_LIST_FIELDS = {"tags", "images"}


def _parse_set(pairs: list[str]) -> dict:
    """Parsea opciones repetibles ``--set clave=valor`` a un dict de campos.

    Los campos en ``_LIST_FIELDS`` (``tags``, ``images``) se separan por comas en
    una lista; el resto se toman como cadenas. Un par sin ``=`` o con clave vacía
    es un error de uso accionable.
    """
    fields: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(
                f"formato inválido en --set '{pair}': se espera 'clave=valor'."
            )
        key, _, raw = pair.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"clave vacía en --set '{pair}'.")
        if key in _LIST_FIELDS:
            fields[key] = [v.strip() for v in raw.split(",") if v.strip()]
        else:
            fields[key] = raw
    return fields


def _print_articles(articles: list[dict]) -> None:
    """Imprime la lista de artículos como una tabla `rich` legible."""
    if not articles:
        print("[yellow]Sin artículos que coincidan con los filtros.[/]")
        return
    table = Table(title="Artículos")
    table.add_column("id", style="cyan")
    table.add_column("título")
    table.add_column("fecha")
    table.add_column("categoría")
    table.add_column("etiquetas")
    for a in articles:
        table.add_row(
            str(a.get("id", "")),
            str(a.get("title", "")),
            str(a.get("date", "")),
            str(a.get("category") or ""),
            ", ".join(a.get("tags") or []),
        )
    print(table)


def _print_items(items: list[dict], kind: str) -> None:
    """Imprime Places/Events consultados como una tabla `rich` legible."""
    if not items:
        print(f"[yellow]Sin resultados para {kind}.[/]")
        return
    third = "categoría" if kind == "places" else "startDate"
    third_key = "category" if kind == "places" else "startDate"
    table = Table(title=kind)
    table.add_column("id", style="cyan")
    table.add_column("name")
    table.add_column(third)
    for item in items:
        table.add_row(
            str(item.get("id", "")),
            str(item.get("name", "")),
            str(item.get(third_key) or ""),
        )
    print(table)


def _print_seo(result: dict) -> None:
    """Imprime el resultado de `analyze_seo` (issues) como tabla `rich`."""
    issues = result.get("issues") or []
    if result.get("ok") or not issues:
        print("[bold green]SEO:[/] sin problemas detectados.")
        return
    table = Table(title="Problemas SEO")
    table.add_column("elemento", style="cyan")
    table.add_column("regla")
    table.add_column("sugerencia")
    for issue in issues:
        if isinstance(issue, dict):
            table.add_row(
                str(issue.get("element", "")),
                str(issue.get("rule", "")),
                str(issue.get("message", "")),
            )
        else:
            table.add_row("", "", str(issue))
    print(table)


# -- artículos del blog --

@app.command("article-create")
@manejar_errores
def article_create(
    title: str,
    body: str = typer.Option(None, "--body", help="Cuerpo markdown; si se omite lo redacta el LLM."),
    date: str = typer.Option(None, "--date", help="Fecha ISO YYYY-MM-DD; por defecto hoy."),
    tags: str = typer.Option(None, "--tags", help="Etiquetas separadas por comas."),
    category: str = typer.Option(None, "--category"),
    summary: str = typer.Option(None, "--summary"),
    project: Path = Path("."),
):
    """Crea un artículo del blog en content/ y muestra su id y ruta."""
    from puriq.core import Puriq

    result = Puriq(project).create_article(
        title=title,
        body=body,
        date=date,
        tags=_split_tags(tags),
        category=category,
        summary=summary,
    )
    print(f"[bold green]Puriq[/] artículo creado: {result['id']} -> {result['path']}")


@app.command("article-list")
@manejar_errores
def article_list(
    date_from: str = typer.Option(None, "--date-from"),
    date_to: str = typer.Option(None, "--date-to"),
    tag: str = typer.Option(None, "--tag"),
    category: str = typer.Option(None, "--category"),
    project: Path = Path("."),
):
    """Lista y filtra los artículos del blog."""
    from puriq.core import Puriq

    articles = Puriq(project).list_articles(
        date_from=date_from, date_to=date_to, tag=tag, category=category
    )
    _print_articles(articles)


@app.command("article-edit")
@manejar_errores
def article_edit(
    id: str,
    title: str = typer.Option(None, "--title"),
    summary: str = typer.Option(None, "--summary"),
    category: str = typer.Option(None, "--category"),
    date: str = typer.Option(None, "--date"),
    tags: str = typer.Option(None, "--tags", help="Etiquetas separadas por comas."),
    body: str = typer.Option(None, "--body"),
    project: Path = Path("."),
):
    """Edita (merge de campos) un artículo existente; solo toca los campos dados."""
    from puriq.core import Puriq

    fields: dict = {}
    if title is not None:
        fields["title"] = title
    if summary is not None:
        fields["summary"] = summary
    if category is not None:
        fields["category"] = category
    if date is not None:
        fields["date"] = date
    if tags is not None:
        fields["tags"] = _split_tags(tags)
    if body is not None:
        fields["body"] = body

    result = Puriq(project).edit_article(id=id, **fields)
    print(f"[bold green]Puriq[/] artículo editado: {result['id']}")


@app.command("article-delete")
@manejar_errores
def article_delete(id: str, project: Path = Path(".")):
    """Elimina el artículo con ese id."""
    from puriq.core import Puriq

    result = Puriq(project).delete_article(id=id)
    print(f"[bold green]Puriq[/] artículo eliminado: {result['id']}")


# -- Places / Events del contrato --

@app.command("query")
@manejar_errores
def query(
    kind: str,
    category: str = typer.Option(None, "--category"),
    tag: str = typer.Option(None, "--tag"),
    name: str = typer.Option(None, "--name"),
    date_from: str = typer.Option(None, "--date-from"),
    date_to: str = typer.Option(None, "--date-to"),
    project: Path = Path("."),
):
    """Consulta (solo lectura) Places o Events del contrato. kind: places|events."""
    from puriq.core import Puriq

    results = Puriq(project).query(
        kind,
        category=category,
        tag=tag,
        name=name,
        date_from=date_from,
        date_to=date_to,
    )
    _print_items(results, kind)


@app.command("edit")
@manejar_errores
def edit(
    id: str,
    set_values: list[str] = typer.Option(
        [],
        "--set",
        help="Campo a modificar como 'clave=valor' (repetible). Listas por comas: --set tags=a,b",
    ),
    project: Path = Path("."),
):
    """Edita (merge de campos) un Place o Event por id usando --set clave=valor."""
    from puriq.core import Puriq

    fields = _parse_set(set_values)
    if not fields:
        raise ValueError(
            "no se indicó ningún campo; use --set clave=valor (repetible)."
        )
    new_id = Puriq(project).edit(id, fields)
    print(f"[bold green]Puriq[/] elemento editado: {new_id}")


@app.command("delete")
@manejar_errores
def delete(id: str, project: Path = Path(".")):
    """Elimina un Place o Event por id (limpia referencias placeId colgantes)."""
    from puriq.core import Puriq

    result = Puriq(project).delete(id)
    afectados = result.get("affectedEvents") or []
    print(f"[bold green]Puriq[/] elemento eliminado: {result['id']}")
    if afectados:
        print(f"[yellow]Events afectados (placeId limpiado):[/] {', '.join(afectados)}")


@app.command("bulk-update")
@manejar_errores
def bulk_update(
    csv_path: Path,
    kind: str = typer.Option("places", "--kind", help="places | events"),
    project: Path = Path("."),
):
    """Fusiona un CSV de Places/Events en el contrato por id (alta/actualización)."""
    from puriq.core import Puriq

    result = Puriq(project).bulk_update(csv_path, kind)
    skipped = result.get("skipped") or []
    print(
        f"[bold green]Puriq[/] fusión completada: "
        f"{result.get('added', 0)} altas, {result.get('updated', 0)} actualizaciones."
    )
    if skipped:
        print(f"[yellow]Filas omitidas (sin id ni name):[/] {skipped}")


@app.command("seo")
@manejar_errores
def seo(project: Path = Path(".")):
    """Analiza el SEO del contenido/salida local (solo lectura)."""
    from puriq.core import Puriq

    result = Puriq(project).analyze_seo()
    _print_seo(result)


if __name__ == "__main__":
    app()
