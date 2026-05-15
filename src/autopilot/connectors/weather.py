"""Weather and environment connector.

Provides weather context for correlating operational issues with storms,
regional outages, or other environmental conditions.

OPENWEATHER_API_KEY is optional. When it is not configured, the connector uses
Open-Meteo's no-key geocoding and forecast APIs so weather remains demoable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Evidence, Signal


WEATHER_API = "https://api.openweathermap.org/data/2.5"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"


class WeatherConnector(Connector):
    manifest = ConnectorManifest(
        name="weather",
        description="Real-time weather data for correlating operational issues with environmental conditions.",
        category="Observability",
        auth_mode="api_key",
        capabilities=[Capability.READ, Capability.SEARCH],
        scopes=["weather.read"],
        objects=["weather", "forecasts", "alerts"],
        safe_actions=[],
        tools=[
            ConnectorToolSpec(
                name="weather_current",
                description="Get current weather for a location (useful for correlating outages with storms).",
                capability=Capability.READ,
                input_schema={"location": "City name or 'lat,lon'"},
                output="Weather JSON",
                mcp_tool=True,
            ),
            ConnectorToolSpec(
                name="weather_search",
                description="Search weather conditions and alerts for a region.",
                capability=Capability.SEARCH,
                input_schema={"query": "Location or weather query"},
                output="Evidence[]",
                mcp_tool=True,
            ),
        ],
        reliability_score=0.92,
    )

    def _configured(self) -> bool:
        return bool(os.getenv("OPENWEATHER_API_KEY"))

    def readiness(self, action: str | None = None) -> dict:
        configured = self._configured()
        return {
            "configured": True,
            "action_ready": True,
            "missing": [] if configured else ["OPENWEATHER_API_KEY"],
            "mode": "openweathermap" if configured else "open_meteo_fallback",
            "detail": "OpenWeatherMap ready." if configured else "Open-Meteo fallback active; set OPENWEATHER_API_KEY for OpenWeatherMap.",
            "action": action,
        }

    async def read(self, ref: str) -> dict[str, Any]:
        if not self._configured():
            return await self._read_open_meteo(ref)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                params: dict[str, Any] = {"appid": os.getenv("OPENWEATHER_API_KEY"), "units": "metric"}
                if "," in ref and all(p.replace(".", "").replace("-", "").isdigit() for p in ref.split(",")):
                    lat, lon = ref.split(",")
                    params.update({"lat": lat.strip(), "lon": lon.strip()})
                else:
                    params["q"] = ref
                resp = await client.get(f"{WEATHER_API}/weather", params=params)
                resp.raise_for_status()
                data = resp.json()
                weather = data.get("weather", [{}])[0]
                main = data.get("main", {})
                wind = data.get("wind", {})
                return {
                    "location": data.get("name", ref),
                    "condition": weather.get("description", "unknown"),
                    "temperature_c": main.get("temp"),
                    "feels_like_c": main.get("feels_like"),
                    "humidity_pct": main.get("humidity"),
                    "wind_speed_ms": wind.get("speed"),
                    "visibility_m": data.get("visibility"),
                    "clouds_pct": data.get("clouds", {}).get("all"),
                    "provider": "openweathermap",
                }
        except Exception as e:
            return {"ref": ref, "error": str(e), "provider": "openweathermap"}

    async def _read_open_meteo(self, ref: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if "," in ref and all(p.strip().replace(".", "").replace("-", "").isdigit() for p in ref.split(",", 1)):
                    lat, lon = [part.strip() for part in ref.split(",", 1)]
                    location_name = ref
                else:
                    geo = await client.get(
                        OPEN_METEO_GEOCODE,
                        params={"name": ref, "count": 1, "language": "en", "format": "json"},
                    )
                    geo.raise_for_status()
                    match = (geo.json().get("results") or [{}])[0]
                    lat = str(match.get("latitude"))
                    lon = str(match.get("longitude"))
                    location_name = ", ".join(str(part) for part in [match.get("name"), match.get("country_code")] if part)
                    if lat == "None" or lon == "None":
                        return {"ref": ref, "error": "Location not found by Open-Meteo geocoder", "provider": "open_meteo"}
                forecast = await client.get(
                    OPEN_METEO_FORECAST,
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,cloud_cover,weather_code",
                    },
                )
                forecast.raise_for_status()
                current = forecast.json().get("current", {})
                return {
                    "location": location_name or ref,
                    "condition": f"weather_code={current.get('weather_code', 'unknown')}",
                    "temperature_c": current.get("temperature_2m"),
                    "feels_like_c": current.get("temperature_2m"),
                    "humidity_pct": current.get("relative_humidity_2m"),
                    "wind_speed_ms": current.get("wind_speed_10m"),
                    "visibility_m": None,
                    "clouds_pct": current.get("cloud_cover"),
                    "provider": "open_meteo",
                }
        except Exception as e:
            return {"ref": ref, "error": str(e), "provider": "open_meteo"}

    async def search(self, query: str) -> list[Evidence]:
        data = await self.read(query)
        if "error" in data:
            return [Evidence(source="weather", title="Weather lookup failed", summary=str(data["error"]), confidence=0.0)]
        return [Evidence(
            source="weather",
            title=f"Weather: {data.get('location', query)}",
            summary=(
                f"{data.get('condition', '?')} | "
                f"{data.get('temperature_c', '?')} C (feels {data.get('feels_like_c', '?')} C) | "
                f"Humidity: {data.get('humidity_pct', '?')}% | "
                f"Wind: {data.get('wind_speed_ms', '?')} m/s | "
                f"Clouds: {data.get('clouds_pct', '?')}% | "
                f"Provider: {data.get('provider', '?')}"
            ),
            confidence=0.85,
        )]

    async def normalize_event(self, payload: dict[str, Any]) -> Signal:
        alert = payload.get("alert", payload)
        return Signal(
            source=self.manifest.name,
            type="weather.alert",
            summary=f"Weather alert: {alert.get('event', alert.get('description', 'Severe weather'))}",
            entities=[str(alert.get("sender_name", "")), str(alert.get("event", ""))],
            urgency="high" if alert.get("severity") in {"Extreme", "Severe"} else "medium",
            payload=payload,
        )

    def as_tools(self):
        from autopilot.agents.base import Tool
        return [
            Tool(
                name="weather_search",
                description="Get current weather for a location. Use when correlating outages with storms or environmental events.",
                parameters={"query": "City name or 'lat,lon' coordinates"},
                fn=self.search,
            )
        ]
