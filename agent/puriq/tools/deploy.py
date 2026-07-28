"""deploy: publica el sitio construido. Patron de adaptadores por destino.

Cada destino soportado se aisla detras de un adaptador que implementa el
protocolo `DeployAdapter` (DD del diseño: "Patrón de adaptadores"). `run`
resuelve el adaptador correspondiente al `target`, valida las precondiciones
(destino soportado y `dist/` existente) y delega la publicacion en el adaptador.

Destinos soportados (registro `ADAPTERS`):
  aws-amplify     -> AWS Amplify Hosting (recomendado, AWS-native)
  s3-cloudfront   -> sube dist/ a S3 + invalida CloudFront
  static-export   -> deja dist/ listo para que TI lo suba a su servidor
  vercel          -> alternativa no-AWS (stub documentado)
  netlify         -> alternativa no-AWS (stub documentado)

Variables de entorno usadas por los adaptadores (leidas con `puriq.config`,
definidas en `agent/.env`; ver `agent/.env.example`):

  Comun AWS:
    AWS_REGION                       Region de los clientes boto3.

  s3-cloudfront (S3CloudFrontAdapter):
    PURIQ_S3_BUCKET                  (requerido) bucket S3 destino.
    PURIQ_CLOUDFRONT_DISTRIBUTION_ID (requerido) id de la distribucion a invalidar.
    PURIQ_SITE_DOMAIN                (opcional) dominio publico; si falta, la URL
                                     se deriva del DomainName de la distribucion.

  aws-amplify (AmplifyAdapter):
    PURIQ_AMPLIFY_APP_ID             (requerido) id de la app de Amplify.
    PURIQ_AMPLIFY_BRANCH             (opcional, default "main") branch a publicar.

Seguridad: ningun mensaje de error expone valores de secretos. Todo error de
adaptador se pasa por `puriq.config.redact` antes de propagarse (Req 7.7).
"""
from __future__ import annotations

import io
import mimetypes
import zipfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from puriq import config


@runtime_checkable
class DeployAdapter(Protocol):
    """Contrato de un adaptador de publicacion para un destino de deploy.

    Cada destino soportado (AWS Amplify, S3+CloudFront, export estatico, etc.)
    provee una implementacion de este protocolo.
    """

    def publish(self, dist: Path) -> str:
        """Publica el contenido de `dist/` y devuelve la URL/ruta publica.

        Args:
            dist: ruta al directorio `dist/` ya construido por `puriq build`.

        Returns:
            La URL publica del sitio (destinos remotos) o la ruta local del
            directorio (destino `static-export`).
        """
        ...


class DeployError(RuntimeError):
    """Error de publicacion de un adaptador.

    Su mensaje ya viene enmascarado con `config.redact`, de modo que nunca
    contiene valores de secretos (Req 7.7).
    """


def _guess_content_type(path: Path) -> str:
    """Adivina el `Content-Type` de un archivo por su extension (mimetypes).

    Args:
        path: ruta del archivo.

    Returns:
        El tipo MIME adivinado; `application/octet-stream` si no se reconoce.
    """
    content_type, _ = mimetypes.guess_type(str(path))
    return content_type or "application/octet-stream"


# --- Adaptadores por destino ---------------------------------------------------


class StaticExportAdapter:
    """Export estatico: no sube a ningun lado (Req 7.6).

    Deja `dist/` intacto y listo para copia manual al servidor del usuario, y
    devuelve la ruta local absoluta del directorio.
    """

    target = "static-export"

    def publish(self, dist: Path) -> str:
        """Devuelve la ruta local absoluta de `dist/` sin subir nada.

        Args:
            dist: directorio `dist/` construido por `puriq build`.

        Returns:
            La ruta absoluta (como string) del directorio `dist/`, lista para
            que el usuario la copie a su servidor.
        """
        return str(Path(dist).resolve())


class S3CloudFrontAdapter:
    """Publica en S3 e invalida CloudFront vía boto3 (Req 7.5).

    Sube recursivamente el contenido de `dist/` a un bucket S3 (con el
    `Content-Type` correcto por archivo), crea una invalidacion de la
    distribucion CloudFront asociada y devuelve la URL publica del sitio.
    """

    target = "s3-cloudfront"

    def publish(self, dist: Path) -> str:
        """Sube `dist/` a S3, invalida CloudFront y devuelve la URL publica.

        Args:
            dist: directorio `dist/` construido por `puriq build`.

        Returns:
            La URL publica del sitio (`PURIQ_SITE_DOMAIN` si esta definida, o el
            `DomainName` de la distribucion CloudFront).

        Raises:
            DeployError: si faltan credenciales/variables o el proveedor rechaza
                la operacion; el mensaje identifica la causa sin exponer secretos.
        """
        # import diferido: permite importar este modulo sin boto3/AWS.
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            bucket = config.get_env("PURIQ_S3_BUCKET", required=True)
            distribution_id = config.get_env(
                "PURIQ_CLOUDFRONT_DISTRIBUTION_ID", required=True
            )
            region = config.get_env("AWS_REGION")
            site_domain = config.get_env("PURIQ_SITE_DOMAIN")

            s3 = boto3.client("s3", region_name=region)
            cloudfront = boto3.client("cloudfront", region_name=region)

            dist = Path(dist)
            # Subida recursiva: cada archivo con su Content-Type y su clave
            # relativa al directorio dist/.
            for file_path in sorted(dist.rglob("*")):
                if not file_path.is_file():
                    continue
                key = file_path.relative_to(dist).as_posix()
                with file_path.open("rb") as fh:
                    s3.put_object(
                        Bucket=bucket,
                        Key=key,
                        Body=fh.read(),
                        ContentType=_guess_content_type(file_path),
                    )

            # Invalidar toda la distribucion para servir el contenido nuevo.
            cloudfront.create_invalidation(
                DistributionId=distribution_id,
                InvalidationBatch={
                    "Paths": {"Quantity": 1, "Items": ["/*"]},
                    "CallerReference": f"puriq-{_caller_reference()}",
                },
            )

            if site_domain:
                return _as_https_url(site_domain)

            # Derivar la URL del DomainName de la distribucion CloudFront.
            info = cloudfront.get_distribution(Id=distribution_id)
            domain_name = info["Distribution"]["DomainName"]
            return _as_https_url(domain_name)
        except config.MissingEnvVarError as exc:
            # Ya nombra la variable faltante; no contiene secretos.
            raise DeployError(config.redact(str(exc))) from None
        except (BotoCoreError, ClientError) as exc:
            raise DeployError(
                config.redact(
                    "El proveedor S3/CloudFront rechazo la publicacion o faltan "
                    f"credenciales: {exc}"
                )
            ) from None


class AmplifyAdapter:
    """Publica en AWS Amplify Hosting vía boto3 (Req 7.4).

    Usa el flujo de deploy manual de Amplify Hosting: crea un deployment
    (obtiene una URL de subida), empaqueta `dist/` como zip, lo sube y arranca
    el deployment. Devuelve la URL publica del branch.
    """

    target = "aws-amplify"

    def publish(self, dist: Path) -> str:
        """Publica `dist/` en Amplify Hosting y devuelve la URL publica.

        Args:
            dist: directorio `dist/` construido por `puriq build`.

        Returns:
            La URL publica del branch en Amplify
            (`https://{branch}.{appId}.amplifyapp.com`).

        Raises:
            DeployError: si faltan credenciales/variables o el proveedor rechaza
                la operacion; el mensaje identifica la causa sin exponer secretos.
        """
        # import diferido: permite importar este modulo sin boto3/AWS.
        import boto3
        import httpx
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            app_id = config.get_env("PURIQ_AMPLIFY_APP_ID", required=True)
            branch = config.get_env("PURIQ_AMPLIFY_BRANCH") or "main"
            region = config.get_env("AWS_REGION")

            amplify = boto3.client("amplify", region_name=region)

            # Amplify manual deploy: create_deployment devuelve una URL S3
            # prefirmada donde subir el zip del sitio.
            deployment = amplify.create_deployment(appId=app_id, branchName=branch)
            zip_upload_url = deployment["zipUploadUrl"]
            job_id = deployment["jobId"]

            archive = _zip_directory(Path(dist))
            upload = httpx.put(
                zip_upload_url,
                content=archive,
                headers={"Content-Type": "application/zip"},
            )
            upload.raise_for_status()

            amplify.start_deployment(appId=app_id, branchName=branch, jobId=job_id)

            # URL publica del branch: usa el defaultDomain de la app si esta
            # disponible; si no, la forma estandar {appId}.amplifyapp.com.
            try:
                app = amplify.get_app(appId=app_id)
                default_domain = app["app"].get("defaultDomain") or (
                    f"{app_id}.amplifyapp.com"
                )
            except (BotoCoreError, ClientError):
                default_domain = f"{app_id}.amplifyapp.com"
            return _as_https_url(f"{branch}.{default_domain}")
        except config.MissingEnvVarError as exc:
            raise DeployError(config.redact(str(exc))) from None
        except httpx.HTTPError as exc:
            raise DeployError(
                config.redact(f"Fallo al subir el paquete a Amplify: {exc}")
            ) from None
        except (BotoCoreError, ClientError) as exc:
            raise DeployError(
                config.redact(
                    "El proveedor Amplify rechazo la publicacion o faltan "
                    f"credenciales: {exc}"
                )
            ) from None


class _CliStubAdapter:
    """Base para adaptadores todavia no implementados.

    Decision de diseño: `vercel` y `netlify` quedan como stubs documentados. No
    son destinos AWS-native (invariante 7 del diseño prioriza AWS), y su
    publicacion se realiza normalmente vía su CLI propietaria (`vercel deploy`,
    `netlify deploy`), fuera del alcance de esta tarea. Se dejan registrados para
    no romper el enrutamiento y para poder implementarlos luego sin cambiar la
    interfaz.
    """

    target = ""
    cli_hint = ""

    def publish(self, dist: Path) -> str:
        """No implementado: publicar manualmente con la CLI del proveedor."""
        raise DeployError(
            f"El destino '{self.target}' no esta implementado en este MVP "
            f"(foco AWS). Publica `dist/` con {self.cli_hint} o usa "
            f"'aws-amplify'/'s3-cloudfront'/'static-export'."
        )


class VercelAdapter(_CliStubAdapter):
    """Stub documentado para Vercel (alternativa no-AWS)."""

    target = "vercel"
    cli_hint = "`vercel deploy --prebuilt`"


class NetlifyAdapter(_CliStubAdapter):
    """Stub documentado para Netlify (alternativa no-AWS)."""

    target = "netlify"
    cli_hint = "`netlify deploy --prod --dir dist`"


def _zip_directory(directory: Path) -> bytes:
    """Empaqueta recursivamente `directory` en un zip en memoria.

    Las rutas dentro del zip son relativas a `directory` (sin el prefijo del
    directorio), como espera el deploy manual de Amplify.

    Args:
        directory: directorio a comprimir.

    Returns:
        Los bytes del archivo zip resultante.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(directory.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(directory).as_posix())
    return buffer.getvalue()


def _as_https_url(domain_or_url: str) -> str:
    """Normaliza un dominio o URL a una URL https sin barra final.

    Args:
        domain_or_url: dominio (p. ej. `d123.cloudfront.net`) o URL completa.

    Returns:
        La URL con esquema `https://` y sin `/` final.
    """
    value = domain_or_url.strip().rstrip("/")
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def _caller_reference() -> str:
    """Referencia unica para invalidaciones de CloudFront (timestamp).

    Returns:
        Una cadena unica basada en el reloj monotono/tiempo actual.
    """
    import time

    return str(int(time.time() * 1000))


# Registro de adaptadores: destino -> fabrica del adaptador. Es la unica fuente
# de verdad de los destinos soportados.
_ADAPTER_REGISTRY: dict[str, type[DeployAdapter]] = {
    "aws-amplify": AmplifyAdapter,
    "s3-cloudfront": S3CloudFrontAdapter,
    "static-export": StaticExportAdapter,
    "vercel": VercelAdapter,
    "netlify": NetlifyAdapter,
}

# Tupla de nombres de destino validos, derivada del registro. Se conserva como
# `ADAPTERS` (tupla) por compatibilidad con el resto del codigo y las pruebas.
ADAPTERS: tuple[str, ...] = tuple(_ADAPTER_REGISTRY)


def get_adapter(target: str) -> DeployAdapter:
    """Devuelve el adaptador registrado para `target`.

    Args:
        target: nombre del destino de deploy.

    Returns:
        Una instancia del `DeployAdapter` correspondiente al destino.

    Raises:
        ValueError: si `target` no esta entre los destinos soportados; el
            mensaje lista los destinos validos (Req 7.2).
    """
    try:
        adapter_cls = _ADAPTER_REGISTRY[target]
    except KeyError:
        validos = ", ".join(ADAPTERS)
        raise ValueError(
            f"Destino no soportado: {target!r}. Destinos validos: {validos}."
        ) from None
    return adapter_cls()


def run(project: Path, target: str = "aws-amplify") -> str:
    """Publica `project/dist` mediante el adaptador del destino indicado.

    Valida que el destino este soportado y que exista el directorio `dist/`
    (producido por `puriq build`) antes de delegar en el adaptador.

    Args:
        project: raiz del proyecto que contiene el directorio `dist/`.
        target: destino de deploy; debe pertenecer a `ADAPTERS`.

    Returns:
        La URL/ruta publica devuelta por el adaptador.

    Raises:
        ValueError: si `target` no esta soportado; el error lista los destinos
            validos (Req 7.2).
        FileNotFoundError: si no existe `project/dist`; el error indica ejecutar
            `puriq build` primero (Req 7.3).
        DeployError: si el adaptador falla al publicar; el mensaje identifica la
            causa sin exponer secretos (Req 7.7).
    """
    # Req 7.2: validar destino soportado antes de tocar el disco.
    adapter = get_adapter(target)

    # Req 7.3: exigir un build previo.
    dist = Path(project) / "dist"
    if not dist.exists():
        raise FileNotFoundError(
            "No existe el directorio 'dist/'. Ejecuta `puriq build` primero."
        )

    # Req 7.1: delegar la publicacion en el adaptador del destino.
    return adapter.publish(dist)
