"""Pruebas unitarias de seleccion de proveedor de geocoding (DD-4, Tarea 5.5).

Verifican la fabrica `geocode.get_provider` (DD-4):
  - Amazon Location configurado/disponible -> `AmazonLocationProvider` (Req 4.5).
  - Sin configuracion -> `NominatimProvider` (fallback OSM, Req 4.6).

Se mockea el acceso a la configuracion (`get_env`) y las fronteras de red/servicio
(`boto3` para Amazon Location, `httpx` para Nominatim): ninguna prueba toca la red.

_Requirements: 4.5, 4.6_
"""
from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from puriq.tools import geocode


# --- helpers ---------------------------------------------------------------
def _fake_get_env(values: dict[str, str | None]):
    """Devuelve un sustituto de `get_env` que consulta `values` por nombre."""

    def _inner(name: str, *, required: bool = False, secret: bool = False):
        return values.get(name)

    return _inner


# --- seleccion de proveedor (Req 4.5 / 4.6) --------------------------------
def test_get_provider_returns_amazon_when_place_index_configured():
    """Con `PURIQ_LOCATION_PLACE_INDEX` definido -> AmazonLocationProvider (Req 4.5)."""
    env = {
        geocode.PLACE_INDEX_ENV: "puriq-place-index",
        "AWS_REGION": "us-east-1",
    }
    with mock.patch.object(geocode, "get_env", side_effect=_fake_get_env(env)):
        provider = geocode.get_provider()

    assert isinstance(provider, geocode.AmazonLocationProvider)
    assert provider.place_index == "puriq-place-index"
    assert provider.region == "us-east-1"
    # Cumple el protocolo del adaptador de geocoding.
    assert isinstance(provider, geocode.GeocodeProvider)


def test_get_provider_amazon_without_region_configured():
    """Amazon Location configurado sin AWS_REGION -> region None (opcional, Req 4.5)."""
    env = {geocode.PLACE_INDEX_ENV: "idx", "AWS_REGION": None}
    with mock.patch.object(geocode, "get_env", side_effect=_fake_get_env(env)):
        provider = geocode.get_provider()

    assert isinstance(provider, geocode.AmazonLocationProvider)
    assert provider.place_index == "idx"
    assert provider.region is None


def test_get_provider_falls_back_to_nominatim_when_unconfigured():
    """Sin place index configurado -> NominatimProvider (fallback OSM, Req 4.6)."""
    env = {geocode.PLACE_INDEX_ENV: None, "AWS_REGION": "us-east-1"}
    with mock.patch.object(geocode, "get_env", side_effect=_fake_get_env(env)):
        provider = geocode.get_provider()

    assert isinstance(provider, geocode.NominatimProvider)
    assert provider.base_url == geocode.NOMINATIM_URL
    assert isinstance(provider, geocode.GeocodeProvider)


def test_get_provider_falls_back_when_place_index_empty():
    """Place index vacio (get_env normaliza a None) -> Nominatim (Req 4.6)."""
    env = {geocode.PLACE_INDEX_ENV: None}
    with mock.patch.object(geocode, "get_env", side_effect=_fake_get_env(env)):
        provider = geocode.get_provider()

    assert isinstance(provider, geocode.NominatimProvider)


# --- comportamiento minimo del proveedor Amazon (boto3 mockeado) -----------
def test_amazon_provider_geocodes_via_mocked_boto3():
    """AmazonLocationProvider usa el cliente `location` de boto3 (sin red real).

    Verifica que se selecciona el proveedor Amazon y que traduce el punto
    ``[lng, lat]`` de Amazon Location a ``{"lat", "lng"}`` (Req 4.5).
    """
    fake_client = mock.Mock()
    fake_client.search_place_index_for_text.return_value = {
        "Results": [{"Place": {"Geometry": {"Point": [20.5, 10.25]}}}]
    }
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = mock.Mock(return_value=fake_client)

    env = {geocode.PLACE_INDEX_ENV: "idx", "AWS_REGION": "us-west-2"}
    with mock.patch.object(geocode, "get_env", side_effect=_fake_get_env(env)):
        provider = geocode.get_provider()
    assert isinstance(provider, geocode.AmazonLocationProvider)

    with mock.patch.dict(sys.modules, {"boto3": fake_boto3}):
        result = provider.geocode("Plaza de Armas")

    fake_boto3.client.assert_called_once_with("location", region_name="us-west-2")
    fake_client.search_place_index_for_text.assert_called_once_with(
        IndexName="idx", Text="Plaza de Armas", MaxResults=1
    )
    # Amazon devuelve [lng, lat]; el proveedor lo normaliza.
    assert result == {"lat": 10.25, "lng": 20.5}


# --- comportamiento minimo del proveedor Nominatim (httpx mockeado) --------
def test_nominatim_provider_geocodes_via_mocked_httpx():
    """NominatimProvider consulta Nominatim via httpx (sin red real, Req 4.6)."""
    fake_response = mock.Mock()
    fake_response.raise_for_status = mock.Mock()
    fake_response.json.return_value = [{"lat": "12.34", "lon": "-56.78"}]

    env = {geocode.PLACE_INDEX_ENV: None}
    with mock.patch.object(geocode, "get_env", side_effect=_fake_get_env(env)):
        provider = geocode.get_provider()
    assert isinstance(provider, geocode.NominatimProvider)

    fake_httpx = types.ModuleType("httpx")
    fake_httpx.get = mock.Mock(return_value=fake_response)

    with mock.patch.dict(sys.modules, {"httpx": fake_httpx}):
        result = provider.geocode("Salar de Uyuni")

    assert fake_httpx.get.call_count == 1
    call = fake_httpx.get.call_args
    assert call.args[0] == geocode.NOMINATIM_URL
    assert call.kwargs["params"]["q"] == "Salar de Uyuni"
    assert "User-Agent" in call.kwargs["headers"]
    assert result == {"lat": 12.34, "lng": -56.78}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
