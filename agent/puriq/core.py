"""Orquestacion del agente Puriq.

Pipeline de build (ver docs/PROYECTO-puriq.md, seccion 6.2):

    recursos + datos abiertos
        -> scan_resources / import_open_data   (produce tourism-data.json)
        -> generate_content (LLM Bedrock)       (rellena descripciones, SEO, i18n)
        -> geocode                              (direcciones -> coords)
        -> build_site                           (activa modulos + aplica tema)
        -> preview / deploy

El agente NUNCA escribe codigo de los modulos: compone y configura modulos
pre-construidos y probados. El LLM solo trabaja sobre contenido y configuracion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from puriq import schemas
from puriq.tools import (
    _persist,
    analyze_seo as analyze_seo_tool,
    build_site,
    bulk_update as bulk_update_tool,
    delete_content,
    deploy as deploy_tool,
    edit_content,
    generate_content,
    geocode,
    import_open_data,
    manage_articles,
    query_content,
    scan_resources,
)

DATA = "tourism-data.json"
CONFIG = "site.config.json"
THEME = "theme.tokens.json"

# Tipo del callback de progreso: recibe un mensaje de hito y no devuelve nada.
# Es un `Callable[[str], None]` genérico (DD-2), sin acoplar el core a FastAPI ni
# a WebSocket: el CLI podría pasar un `print`/`rich` y el wizard un encolador.
ProgressCallback = Callable[[str], None]


def _emit(progress: ProgressCallback | None, message: str) -> None:
    """Emite un hito de progreso si hay callback definido (no-op si es None).

    Aditivo por diseño (DD-2): cuando `progress is None` —el caso del CLI
    actual— no ocurre nada y el comportamiento del pipeline no cambia.
    """
    if progress is not None:
        progress(message)


class Puriq:
    def __init__(self, project: Path):
        self.project = Path(project)

    # --- fase de datos -----------------------------------------------------
    def collect(
        self,
        resources_dir: Path,
        enrich: bool = False,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Escanea recursos del usuario y (opcional) enriquece con datos abiertos.

        Orden del pipeline (ver DD-1 del diseño): scan -> enrich (opcional) ->
        geocode -> comprobación de coords accionable -> validate -> persistir.
        Geocode se ejecuta *antes* de validar para garantizar que los Places
        borrador (sin `coords` tras el scan) queden completos; así el
        `tourism-data.json` persistido siempre cumple el esquema (con coords).

        Args:
            resources_dir: carpeta con los recursos crudos (site.json + CSVs).
            enrich: si es True, enriquece con datos abiertos (OSM/Wikidata).
            progress: callback opcional de progreso (DD-2). Si se define, se
                emiten hitos antes/después de cada fase; con `progress=None`
                (el caso del CLI) el comportamiento y la firma efectiva no
                cambian.
        """
        _emit(progress, "Escaneando recursos del proyecto...")
        data = scan_resources.run(resources_dir)
        if enrich:
            _emit(progress, "Importando datos abiertos (OSM/Wikidata)...")
            data = import_open_data.merge(data)
        # Geocode completa las `coords` faltantes antes de la validación estricta.
        _emit(progress, "Geocodificando direcciones faltantes...")
        data = geocode.fill_missing_coords(data)
        # Comprobación accionable: si algún Place sigue sin `coords`, lanza un
        # error que nombra cada Place afectado en vez de un ValidationError crudo.
        _emit(progress, "Validando el contrato...")
        schemas.check_places_have_coords(data)
        schemas.validate(data, "tourism-data")
        (self.project / DATA).write_text(schemas.dumps(data))
        _emit(progress, "Contrato generado (tourism-data.json).")

    # --- fase de build -----------------------------------------------------
    def build(
        self,
        use_llm: bool = True,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Reconstruye el sitio a partir del contrato persistido.

        Orden del pipeline (ver DD-1 del diseño): carga tolerante del
        `tourism-data.json` -> geocode -> comprobación de coords accionable ->
        validate estricto -> generate_content.enrich (si use_llm) ->
        build_site.assemble.

        El `tourism-data.json` se carga de forma **tolerante** (parseo JSON sin
        validación estricta previa), de modo que un documento editado a mano
        —con Places que solo tienen `address` y todavía sin `coords`— pueda
        pasar por `geocode` antes de validarse. Esto soporta el flujo de
        editar-y-reconstruir. En cambio, `site.config.json` y `theme.tokens.json`
        sí se cargan con validación estricta (`schemas.load`), porque no
        dependen de geocode. La validación estructural estricta del contenido
        ocurre *antes* del enriquecimiento con el LLM.

        Args:
            use_llm: si es True, enriquece el contenido con el LLM antes de
                ensamblar el sitio.
            progress: callback opcional de progreso (DD-2). Si se define, se
                emiten hitos antes/después de cada fase; con `progress=None`
                (el caso del CLI) el comportamiento y la firma efectiva no
                cambian.

        Returns:
            La ruta `project/dist` con el sitio estático construido.
        """
        # Carga tolerante: parsea sin validar para permitir coords faltantes.
        _emit(progress, "Cargando el contrato del proyecto...")
        data = schemas.load_raw(self.project / DATA)
        config = schemas.load(self.project / CONFIG, "site-config")
        theme = schemas.load(self.project / THEME, "theme-tokens")

        # Geocode completa las `coords` faltantes antes de la validación estricta.
        _emit(progress, "Geocodificando direcciones faltantes...")
        data = geocode.fill_missing_coords(data)
        # Comprobación accionable: si algún Place sigue sin `coords`, lanza un
        # error que nombra cada Place afectado en vez de un ValidationError crudo.
        _emit(progress, "Validando el contrato...")
        schemas.check_places_have_coords(data)
        # Validación estructural estricta del contenido antes de enriquecer.
        schemas.validate(data, "tourism-data")

        if use_llm:
            _emit(progress, "Generando contenido con IA...")
            # Traducciones OFF: la plantilla Astro actual NO renderiza el
            # companion `i18n`, así que generarlas sería coste de LLM
            # desperdiciado. Cuando el render de i18n aterrice en build_site,
            # cambiar esto a `translate=True`.
            data = generate_content.enrich(
                data, theme.get("voice", {}), translate=False
            )
            # Persistir el contrato enriquecido de vuelta a tourism-data.json,
            # SOLO en la rama use_llm=True. Sin esto, cada build re-llamaría al
            # LLM (re-pago) y el texto generado no sería revisable/editable.
            # Se persiste AQUÍ (justo tras enrich, antes de assemble): la escritura
            # es barata y garantiza que el contrato guardado coincide exactamente
            # con lo que se construye. Se escribe la vista conforme al esquema
            # (`contract_view`, sin `i18n`) con escritura atómica y validación
            # ESTRICTA: es segura porque geocode ya completó las coords y
            # check_places_have_coords se ejecutó más arriba. NO se persiste en
            # la rama use_llm=False, para no alterar el comportamiento existente
            # (Property 19 asume que el draft en disco sigue siendo inválido).
            _emit(progress, "Guardando contenido generado...")
            _persist.validate_then_write(
                generate_content.contract_view(data),
                "tourism-data",
                self.project / DATA,
            )

        _emit(progress, "Construyendo el sitio estático...")
        dist = build_site.assemble(self.project, data, config, theme)
        _emit(progress, f"Sitio construido en {dist}.")
        return dist

    # --- fase de publicacion ----------------------------------------------
    def preview(self, port: int = 4322) -> None:
        build_site.serve(self.project, port=port)

    def deploy(self, target: str = "aws-amplify") -> str:
        return deploy_tool.run(self.project, target=target)

    # --- gestión de contenido (content-management) -------------------------
    # Punto único de orquestación que comparten CLI y MCP (Req 11.2, 11.3): cada
    # método carga el artefacto necesario, delega en la tool correspondiente y
    # (cuando hay mutación del contrato) persiste de forma atómica y validada. No
    # se duplica lógica de negocio: las tools contienen la lógica pura.
    #
    # Nota de persistencia (draft): `edit`, `delete` y `bulk_update` producen un
    # `tourism-data` que puede contener Places con solo `address` y todavía sin
    # `coords` (mismo caso DD-1 del wizard). Persistir con validación ESTRICTA de
    # "tourism-data" los rechazaría. Por eso se persisten como **draft** con
    # `_persist.save_tourism_draft` (coords opcional por Place; el resto del
    # esquema se sigue exigiendo). `geocode` completará las coords en el próximo
    # build, donde `build()` sí valida de forma estricta. La persistencia de
    # artículos NO se relaja: `manage_articles` valida estrictamente contra
    # "article" al escribir.

    # -- artículos del blog (Content_Store /content) --
    def create_article(
        self,
        *,
        title: str,
        body: str | None = None,
        date: str | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
        summary: str | None = None,
    ) -> dict:
        """Crea un Article en `content/`. Devuelve `{"id", "path"}` (Req 2)."""
        return manage_articles.create_article(
            self.project / "content",
            title=title,
            body=body,
            date=date,
            tags=tags,
            category=category,
            summary=summary,
        )

    def list_articles(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        tag: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        """Lista/filtra los artículos de `content/` (Req 3)."""
        return manage_articles.list_articles(
            self.project / "content",
            date_from=date_from,
            date_to=date_to,
            tag=tag,
            category=category,
        )

    def edit_article(self, *, id: str, **fields) -> dict:
        """Edita (merge de campos) un Article existente. Devuelve `{"id"}` (Req 4)."""
        return manage_articles.edit_article(
            self.project / "content", id=id, **fields
        )

    def delete_article(self, *, id: str) -> dict:
        """Elimina el Article con ese `id`. Devuelve `{"id"}` (Req 5)."""
        return manage_articles.delete_article(self.project / "content", id=id)

    # -- Places / Events (tourism-data.json) --
    def query(self, kind: str, **filters) -> list[dict]:
        """Consulta (solo lectura) Places o Events del contrato (Req 6).

        Carga el `tourism-data.json` de forma tolerante (`load_raw`) y delega en
        `query_content.query`. No persiste nada.
        """
        data = schemas.load_raw(self.project / DATA)
        return query_content.query(data, kind=kind, **filters)

    def edit(self, id: str, fields: dict) -> str:
        """Edita (merge de campos) un Place o Event por `id` y persiste (Req 7).

        Carga el contrato tolerante, delega en `edit_content.edit` y persiste el
        resultado como draft (coords opcional). Devuelve el `id` editado.
        """
        data = schemas.load_raw(self.project / DATA)
        result = edit_content.edit(data, id=id, fields=fields)
        _persist.save_tourism_draft(result, self.project / DATA)
        return id

    def delete(self, id: str) -> dict:
        """Elimina un Place o Event por `id` y persiste (Req 8).

        Delega en `delete_content.delete` (que maneja integridad referencial),
        persiste el `data` resultante como draft y devuelve
        `{"id", "affectedEvents"}`.
        """
        data = schemas.load_raw(self.project / DATA)
        result = delete_content.delete(data, id=id)
        _persist.save_tourism_draft(result["data"], self.project / DATA)
        return {"id": result["id"], "affectedEvents": result["affectedEvents"]}

    def bulk_update(self, csv_path: Path, kind: str) -> dict:
        """Fusiona un CSV de Places/Events en el contrato y persiste (Req 9).

        Delega en `bulk_update.bulk_update`, persiste `result["data"]` como draft
        y devuelve el resumen `{"added", "updated", "skipped"}`.
        """
        data = schemas.load_raw(self.project / DATA)
        result = bulk_update_tool.bulk_update(data, Path(csv_path), kind=kind)
        _persist.save_tourism_draft(result["data"], self.project / DATA)
        return {
            "added": result["added"],
            "updated": result["updated"],
            "skipped": result["skipped"],
        }

    def analyze_seo(self) -> dict:
        """Analiza el contenido/salida local del proyecto (solo lectura, Req 10)."""
        return analyze_seo_tool.analyze_seo(self.project)
