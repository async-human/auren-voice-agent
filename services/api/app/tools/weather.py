from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes used by Open-Meteo.
WEATHER_CODES: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "rain showers",
    82: "violent rain showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "thunderstorms with heavy hail",
}


class WeatherArgs(BaseModel):
    location: str = Field(
        description="City, or 'city, country' for a place name that is ambiguous.",
        min_length=1,
        max_length=120,
    )
    units: Literal["metric", "imperial"] = Field(
        default="metric",
        description="metric for Celsius, imperial for Fahrenheit.",
    )


async def _geocode(context: ToolContext, location: str) -> dict[str, Any]:
    response = await context.http.get(
        GEOCODING_URL,
        params={"name": location, "count": 1, "language": "en", "format": "json"},
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    if not results:
        raise ToolError(f"I could not find a place called {location}.")
    return results[0]


async def get_weather(context: ToolContext, args: WeatherArgs) -> ToolResult:
    place = await _geocode(context, args.location)
    imperial = args.units == "imperial"

    response = await context.http.get(
        FORECAST_URL,
        params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 1,
            "timezone": "auto",
            "temperature_unit": "fahrenheit" if imperial else "celsius",
            "wind_speed_unit": "mph" if imperial else "kmh",
        },
    )
    response.raise_for_status()
    payload = response.json()

    current = payload.get("current", {})
    daily = payload.get("daily", {})
    degree = "F" if imperial else "C"
    condition = WEATHER_CODES.get(int(current.get("weather_code", -1)), "unsettled weather")

    name_parts = [place.get("name"), place.get("country")]
    place_name = ", ".join(part for part in name_parts if part)

    temperature = round(float(current.get("temperature_2m", 0)))
    feels_like = round(float(current.get("apparent_temperature", temperature)))
    high = _first(daily.get("temperature_2m_max"))
    low = _first(daily.get("temperature_2m_min"))
    rain_chance = _first(daily.get("precipitation_probability_max"))

    summary = f"It is {temperature} degrees {degree} and {condition} in {place_name}"
    if feels_like != temperature:
        summary += f", feeling like {feels_like}"
    if high is not None and low is not None:
        summary += f". Today ranges from {round(low)} to {round(high)} degrees"
    if rain_chance:
        summary += f", with a {round(rain_chance)} percent chance of precipitation"
    summary += "."

    return ToolResult(
        summary=summary,
        data={
            "location": place_name,
            "condition": condition,
            "temperature": temperature,
            "feels_like": feels_like,
            "high": high,
            "low": low,
            "precipitation_probability": rain_chance,
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "units": args.units,
        },
    )


def _first(values: Any) -> float | None:
    if isinstance(values, list) and values and values[0] is not None:
        return float(values[0])
    return None


SPEC = ToolSpec(
    name="get_weather",
    description="Get the current weather and today's forecast for a place.",
    args_model=WeatherArgs,
    handler=get_weather,
)
