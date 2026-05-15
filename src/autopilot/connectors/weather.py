"""OpenWeatherMap — Weather & Environment Connector.

Provides real-time weather data for location-aware operational signals.
Useful for correlating service issues with weather events (data center outages,
network disruptions during storms, etc).

Env: OPENWEATHER_API_KEY
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from autopilot.connectors.base import Connector
from autopilot.models import Capability, ConnectorManifest, ConnectorToolSpec, Evidence, Signal


WEATHER_API = "https://api.openweathermap.org/data/2.5"


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
            "configured": configured,
            "action_ready": configured,
            "missing": [] if configured else ["OPENWEATHER_API_KEY"],
            "mode": "api_key",
            "detail": "OpenWeatherMap ready." if configured else "Missing: OPENWEATHER_API_KEY",
            "action": action,
        }

    async def read(self, ref: str) -> dict[str, Any]:
        if not self._configured():
            return {"error": "OpenWeatherMap not configured — set OPENWEATHER_API_KEY"}
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
                }
        except Exception as e:
            return {"ref": ref, "error": str(e)}

    async def search(self, query: str) -> list[Evidence]:
        data = await self.read(query)
        if "error" in data:
            return [Evidence(source="weather", title="Weather lookup failed", summary=str(data["error"]), confidence=0.0)]
        return [Evidence(
            source="weather",
            title=f"Weather: {data.get('location', query)}",
            summary=(
                f"{data.get('condition', '?')} | "
                f"{data.get('temperature_c', '?')}°C (feels {data.get('feels_like_c', '?')}°C) | "
                f"Humidity: {data.get('humidity_pct', '?')}% | "
                f"Wind: {data.get('wind_speed_ms', '?')} m/s | "
                f"Clouds: {data.get('clouds_pct', '?')}%"
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

