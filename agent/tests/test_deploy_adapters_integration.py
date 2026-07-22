"""Pruebas de integracion de los adaptadores de deploy (Tarea 11.4).

Verifican los tres adaptadores implementados en `puriq.tools.deploy` mediante
1-3 ejemplos, mockeando por completo las fronteras externas (`boto3`, `botocore`
y `httpx`), de modo que ninguna prueba toca AWS ni la red:

  - `aws-amplify`  -> publica via boto3 (Amplify manual deploy) + subida httpx y
                      devuelve la URL publica del branch (Req 7.4).
  - `s3-cloudfront`-> sube `dist/` a S3 e invalida CloudFront via boto3 y
                      devuelve la URL publica (Req 7.5).
  - `static-export`-> deja `dist/` intacto y devuelve la ruta local (Req 7.6).

`deploy.run` exige que exista `project/dist` antes de delegar en el adaptador
(precondicion de build, Req 7.1/7.3): cada prueba crea ese directorio en un
`tmp_path`. Las variables de entorno que leen los adaptadores se inyectan con
`monkeypatch.setenv` (que `config.get_env` prioriza sobre `agent/.env`).

Los modulos `boto3`/`botocore`/`httpx` se importan de forma diferida dentro de
los adaptadores, asi que se inyectan como dobles en `sys.modules` justo antes de
invocar el deploy; boto3 puede no estar instalado en el entorno de pruebas.

_Requirements: 7.1, 7.4, 7.5, 7.6_
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest

# El paquete `puriq` vive en agent/; aseguramos que este en sys.path.
_AGENT_DIR = Path(__file__).resolve().parent.parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from puriq.tools import deploy  # noqa: E402


# --- Dobles de las fronteras externas --------------------------------------


class _FakeBotoCoreError(Exception):
    """Sustituto de `botocore.exceptions.BotoCoreError` para las clausulas except."""


class _FakeClientError(Exception):
    """Sustituto de `botocore.exceptions.ClientError` para las clausulas except."""


def _fake_botocore() -> types.ModuleType:
    """Construye un `botocore` falso con el submodulo `exceptions` que usa deploy.

    `deploy` hace `from botocore.exceptions import BotoCoreError, ClientError`;
    ambos deben ser clases de excepcion reales para que los `except` funcionen.
    """
    botocore = types.ModuleType("botocore")
    exceptions = types.ModuleType("botocore.exceptions")
    exceptions.BotoCoreError = _FakeBotoCoreError
    exceptions.ClientError = _FakeClientError
    botocore.exceptions = exceptions
    return botocore, exceptions


def _fake_boto3(clients: dict[str, mock.Mock]) -> types.ModuleType:
    """`boto3` falso cuyo `client(service, region_name=...)` devuelve `clients[service]`."""
    fake = types.ModuleType("boto3")

    def _client(service: str, region_name: str | None = None):
        return clients[service]

    fake.client = mock.Mock(side_effect=_client)
    return fake


def _install_aws_modules(clients: dict[str, mock.Mock]) -> dict[str, types.ModuleType]:
    """Devuelve el dict `sys.modules` a inyectar para las fronteras AWS."""
    botocore, exceptions = _fake_botocore()
    return {
        "boto3": _fake_boto3(clients),
        "botocore": botocore,
        "botocore.exceptions": exceptions,
    }


def _make_dist(tmp_path: Path) -> Path:
    """Crea `tmp_path/dist` con un par de archivos para satisfacer el build previo."""
    project = tmp_path
    dist = project / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Puriq</title>", encoding="utf-8")
    (dist / "assets" / "app.css").write_text("body{color:#000}", encoding="utf-8")
    return project


# --- Ejemplo 1: static-export devuelve la ruta local (Req 7.6) --------------


def test_static_export_returns_local_dist_path(tmp_path: Path):
    """`static-export` no sube nada y devuelve la ruta absoluta local de `dist/`."""
    project = _make_dist(tmp_path)

    result = deploy.run(project, target="static-export")

    expected = str((project / "dist").resolve())
    assert result == expected
    # La ruta devuelta existe y sigue conteniendo el build (no se movio nada).
    assert Path(result).is_dir()
    assert (Path(result) / "index.html").exists()


# --- Ejemplo 2: s3-cloudfront sube a S3 + invalida CloudFront (Req 7.5) -----


def test_s3_cloudfront_uploads_and_invalidates_and_returns_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`s3-cloudfront` sube cada archivo de `dist/` a S3, invalida la distribucion
    y devuelve la URL publica derivada del `DomainName` de CloudFront."""
    project = _make_dist(tmp_path)

    monkeypatch.setenv("PURIQ_S3_BUCKET", "puriq-bucket")
    monkeypatch.setenv("PURIQ_CLOUDFRONT_DISTRIBUTION_ID", "E123ABC")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    # Sin PURIQ_SITE_DOMAIN: la URL debe derivarse del DomainName de CloudFront.
    monkeypatch.delenv("PURIQ_SITE_DOMAIN", raising=False)

    s3 = mock.Mock()
    cloudfront = mock.Mock()
    cloudfront.get_distribution.return_value = {
        "Distribution": {"DomainName": "d111abc.cloudfront.net"}
    }
    modules = _install_aws_modules({"s3": s3, "cloudfront": cloudfront})

    with mock.patch.dict(sys.modules, modules):
        url = deploy.run(project, target="s3-cloudfront")

    # URL publica derivada del DomainName de la distribucion.
    assert url == "https://d111abc.cloudfront.net"

    # Se subieron los 2 archivos de dist/ con su clave relativa (posix).
    assert s3.put_object.call_count == 2
    uploaded_keys = {c.kwargs["Key"] for c in s3.put_object.call_args_list}
    assert uploaded_keys == {"index.html", "assets/app.css"}
    for call in s3.put_object.call_args_list:
        assert call.kwargs["Bucket"] == "puriq-bucket"
        assert call.kwargs["Body"]  # cuerpo no vacio
        assert call.kwargs["ContentType"]  # Content-Type presente
    # El index.html se sube como text/html (Content-Type por extension).
    html_call = next(
        c for c in s3.put_object.call_args_list if c.kwargs["Key"] == "index.html"
    )
    assert html_call.kwargs["ContentType"] == "text/html"

    # Se invalido toda la distribucion CloudFront.
    cloudfront.create_invalidation.assert_called_once()
    inv = cloudfront.create_invalidation.call_args
    assert inv.kwargs["DistributionId"] == "E123ABC"
    assert inv.kwargs["InvalidationBatch"]["Paths"]["Items"] == ["/*"]


def test_s3_cloudfront_prefers_configured_site_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Con `PURIQ_SITE_DOMAIN` definido, la URL usa ese dominio y no consulta el
    DomainName de la distribucion (Req 7.5)."""
    project = _make_dist(tmp_path)

    monkeypatch.setenv("PURIQ_S3_BUCKET", "puriq-bucket")
    monkeypatch.setenv("PURIQ_CLOUDFRONT_DISTRIBUTION_ID", "E123ABC")
    monkeypatch.setenv("PURIQ_SITE_DOMAIN", "turismo.gob.bo")

    s3 = mock.Mock()
    cloudfront = mock.Mock()
    modules = _install_aws_modules({"s3": s3, "cloudfront": cloudfront})

    with mock.patch.dict(sys.modules, modules):
        url = deploy.run(project, target="s3-cloudfront")

    assert url == "https://turismo.gob.bo"
    # No hizo falta derivar el dominio de la distribucion.
    cloudfront.get_distribution.assert_not_called()
    # Aun asi invalido la cache tras subir.
    cloudfront.create_invalidation.assert_called_once()
    assert s3.put_object.call_count == 2


# --- Ejemplo 3: aws-amplify publica via boto3 + httpx (Req 7.4) -------------


def test_aws_amplify_publishes_via_boto3_and_returns_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`aws-amplify` crea un deployment, sube el zip via httpx, arranca el
    deployment y devuelve la URL publica del branch."""
    project = _make_dist(tmp_path)

    monkeypatch.setenv("PURIQ_AMPLIFY_APP_ID", "d2xyzappid")
    monkeypatch.setenv("PURIQ_AMPLIFY_BRANCH", "main")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    amplify = mock.Mock()
    amplify.create_deployment.return_value = {
        "zipUploadUrl": "https://s3.amazonaws.com/upload-here",
        "jobId": "job-1",
    }
    amplify.get_app.return_value = {"app": {"defaultDomain": "amplifyapp.com"}}
    modules = _install_aws_modules({"amplify": amplify})

    # httpx falso: put() devuelve una respuesta con raise_for_status() no-op.
    fake_httpx = types.ModuleType("httpx")
    upload_response = mock.Mock()
    upload_response.raise_for_status = mock.Mock()
    fake_httpx.put = mock.Mock(return_value=upload_response)
    fake_httpx.HTTPError = Exception
    modules["httpx"] = fake_httpx

    with mock.patch.dict(sys.modules, modules):
        url = deploy.run(project, target="aws-amplify")

    # URL publica del branch: https://{branch}.{defaultDomain}
    assert url == "https://main.amplifyapp.com"

    # Flujo de deploy manual de Amplify via boto3.
    amplify.create_deployment.assert_called_once_with(
        appId="d2xyzappid", branchName="main"
    )
    amplify.start_deployment.assert_called_once_with(
        appId="d2xyzappid", branchName="main", jobId="job-1"
    )

    # El zip del sitio se subio a la URL prefirmada devuelta por Amplify.
    fake_httpx.put.assert_called_once()
    put_call = fake_httpx.put.call_args
    assert put_call.args[0] == "https://s3.amazonaws.com/upload-here"
    assert put_call.kwargs["content"]  # bytes del zip, no vacio
    assert put_call.kwargs["headers"]["Content-Type"] == "application/zip"
    upload_response.raise_for_status.assert_called_once()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
