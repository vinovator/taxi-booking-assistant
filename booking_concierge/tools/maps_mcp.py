"""Driving distances from Google Maps' MCP server, for places in the UK.

calculate_distance is booking_agent's tool. For each trip it:
1. checks the pickup and the drop-off are in the UK, with postcodes.io (free, no key, built on official ONS and
   Ordnance Survey data);
2. asks Google Maps' MCP server for the driving distance between them (its compute_routes tool), in miles.

The Google Maps key comes from GOOGLE_MAPS_API_KEY (booking_concierge/.env, or Streamlit's secrets once deployed).
Without one, every trip is estimated at 10 miles, so the app still runs.
"""
import os
import re
from contextlib import asynccontextmanager

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MAPS_MCP_URL = "https://mapstools.googleapis.com/mcp"  # Google's "Maps Experience agent tools" MCP server
POSTCODES_IO = "https://api.postcodes.io"
METRES_PER_MILE = 1609.344
ESTIMATE_MILES = 10.0  # every trip's distance when there's no Google Maps key

# postcodes.io also covers the Channel Islands and the Isle of Man, which aren't in the UK.
UK_COUNTRIES = {"England", "Scotland", "Wales", "Northern Ireland"}

# A UK postcode, with or without its space, for example SW1A 1AA or sw1a1aa.
POSTCODE = re.compile(r"[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}", re.IGNORECASE)


@asynccontextmanager
async def _google_maps(key: str):
    """A session with Google Maps' MCP server.

    MCP over HTTP is three layers, each opened inside the one before:
    1. an HTTP client, which sends the key with every request;
    2. MCP's "streamable HTTP" transport, which carries MCP messages over that client;
    3. the MCP session, which calls the server's tools.
    """
    async with (
        httpx.AsyncClient(headers={"X-Goog-Api-Key": key}, timeout=30) as http,
        streamable_http_client(MAPS_MCP_URL, http_client=http) as (read, write, _),
        ClientSession(read, write) as maps,
    ):
        await maps.initialize()  # MCP's handshake
        yield maps


async def _call_tool(maps: ClientSession, name: str, arguments: dict) -> dict:
    """Call one of Google Maps' tools, and return its answer.

    Each of Google's tools declares an output schema. So the MCP library checks every answer against it, and the
    answer is always there, as JSON, in structuredContent.
    """
    result = await maps.call_tool(name, arguments)
    if result.isError:
        raise RuntimeError(" ".join(part.text for part in result.content if part.type == "text"))
    return result.structuredContent


async def _postcodes_io(path: str, **params):
    """The result of a postcodes.io lookup, or None if it found nothing.

    It has its own HTTP client, so the Google Maps key is never sent to postcodes.io.
    """
    async with httpx.AsyncClient(base_url=POSTCODES_IO, timeout=10) as postcodes:
        response = await postcodes.get(path, params=params)
    if response.status_code == 404:  # no such postcode
        return None
    response.raise_for_status()
    return response.json()["result"]


async def _waypoint(place: str, maps: ClientSession) -> dict | str:
    """Where a place is, ready for compute_routes, or a message for the customer saying why it can't be used.

    - A postcode must exist, and be in England, Scotland, Wales or Northern Ireland.
    - Anything else is found on Google Maps. Google doesn't say which country a place is in, so postcodes.io must
      find a UK postcode within 2 km of it.

    Like booking_tools' helpers, it returns its message instead of raising an exception. That matters here: an
    exception raised inside the MCP session comes out wrapped in exception groups (see _reason).
    """
    place = place.strip()
    if POSTCODE.fullmatch(place):
        postcode = place.upper()
        found = await _postcodes_io(f"/postcodes/{postcode}")
        if not found or found["country"] not in UK_COUNTRIES:
            return f"{postcode} isn't a UK postcode."
        return {"latLng": {"latitude": found["latitude"], "longitude": found["longitude"]}}

    search = {"textQuery": f"{place}, UK", "regionCode": "GB", "pageSize": 1}
    places = (await _call_tool(maps, "search_places", search)).get("places")
    if not places:
        return f"Google Maps can't find {place}."
    location = places[0]["location"]
    near = {"lat": location["latitude"], "lon": location["longitude"], "radius": 2000, "limit": 1}  # within 2 km
    if not await _postcodes_io("/postcodes", **near):
        return f"{place} isn't in the UK."
    return {"placeId": places[0]["id"]}


def _reason(error: Exception) -> str:
    """What went wrong, in a few words.

    The MCP library runs its connection in task groups, and they wrap errors in exception groups, sometimes one
    inside another. The first error inside is the one that happened: Google refusing the key, say, or a timeout.
    """
    while hasattr(error, "exceptions"):
        error = error.exceptions[0]
    return str(error)[:200] or type(error).__name__  # a timeout, for one, has no message


async def calculate_distance(source: str, destination: str) -> dict:
    """
    Check both places are in the UK, then get the driving distance between them from Google Maps.

    Args:
        source (str): The pickup place: an address, a place name or a UK postcode
        destination (str): The drop-off place: an address, a place name or a UK postcode

    Returns:
        dict: {"miles": ..., "source": ...}: the driving distance and where it comes from, or {"message": ...}
        if a place isn't in the UK or there's no driving route.
    """
    key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not key:
        return {"miles": ESTIMATE_MILES, "source": "an estimate, because Google Maps isn't set up"}

    try:
        async with _google_maps(key) as maps:
            start = await _waypoint(source, maps)
            end = await _waypoint(destination, maps)
            problems = [waypoint for waypoint in (start, end) if isinstance(waypoint, str)]
            if problems:
                return {"message": " ".join(problems)}
            route = {"origin": start, "destination": end, "travelMode": "DRIVE"}
            routes = (await _call_tool(maps, "compute_routes", route)).get("routes")
    except Exception as error:  # for example, no internet, a wrong key, or the daily limit reached
        return {"message": f"Distances aren't available right now: {_reason(error)}"}

    if not routes:
        return {"message": f"Google Maps found no driving route from {source} to {destination}."}
    metres = routes[0].get("distanceMeters", 0)  # left out when it's 0
    if not metres:
        return {"message": "The pickup and drop-off are the same place."}
    return {"miles": round(metres / METRES_PER_MILE, 1), "source": "Google Maps"}
