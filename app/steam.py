"""Steam Web API boundary. No Steam data is fetched outside explicit user actions."""

import re
from collections.abc import Iterator, Mapping
from urllib.parse import urlparse

import httpx
from django.conf import settings

BASE_URL = "https://api.steampowered.com"


class SteamError(Exception):
    pass


def _get(
    path: str,
    params: Mapping[str, str | int] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> object:
    if not settings.STEAM_WEB_API_KEY:
        raise SteamError(
            "Steam import is unavailable until a Steam Web API key is configured."
        )
    try:
        with httpx.Client(timeout=30, transport=transport) as client:
            response = client.get(
                BASE_URL + path,
                params=params,
                headers={"x-webapi-key": settings.STEAM_WEB_API_KEY},
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SteamError("Steam could not be reached. Please try again later.") from exc


def resolve_profile(value: str, *, transport: httpx.BaseTransport | None = None) -> str:
    value = value.strip().rstrip("/")
    if value.isdecimal() and 0 < int(value) < 2**64:
        return value
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) and not value.isdecimal():
        vanity = value
    else:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc.lower() not in {
            "steamcommunity.com",
            "www.steamcommunity.com",
        }:
            raise SteamError(
                "Enter a Steam ID, custom URL name, or "
                "https://steamcommunity.com profile URL."
            )
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or not parts[1] or parsed.query or parsed.fragment:
            raise SteamError("Enter a Steam profile URL.")
        if parts[0] == "profiles":
            return resolve_profile(parts[1])
        if parts[0] != "id":
            raise SteamError("Enter a Steam profile URL.")
        vanity = parts[1]
    data = _get(
        "/ISteamUser/ResolveVanityURL/v1/", {"vanityurl": vanity}, transport=transport
    )
    if not isinstance(data, dict):
        raise SteamError("Steam returned an invalid profile response.")
    response = data.get("response", {})
    if response.get("success") != 1 or not str(response.get("steamid", "")).isdecimal():
        raise SteamError("Steam could not find that profile.")
    return str(response["steamid"])


def owned_games(
    steam_id: str, *, transport: httpx.BaseTransport | None = None
) -> dict[str, str]:
    data = _get(
        "/IPlayerService/GetOwnedGames/v1/",
        {
            "steamid": steam_id,
            "include_appinfo": 1,
        },
        transport=transport,
    )
    if not isinstance(data, dict):
        raise SteamError("Steam returned an invalid game library. Nothing was changed.")
    response = data.get("response")
    if not isinstance(response, dict) or type(response.get("game_count")) is not int:
        raise SteamError(
            "Steam did not return a visible game library. "
            "Check the profile's game details privacy setting."
        )
    if response["game_count"] == 0 and "games" not in response:
        response["games"] = []
    if not isinstance(response.get("games"), list):
        raise SteamError("Steam returned an invalid game library. Nothing was changed.")
    games = {}
    for item in response["games"]:
        if (
            not isinstance(item, dict)
            or type(item.get("appid")) is not int
            or item["appid"] <= 0
        ):
            raise SteamError(
                "Steam returned an invalid game library. Nothing was changed."
            )
        appid = str(item["appid"])
        games[appid] = str(item.get("name") or f"Steam app {appid}")[:200]
    if len(games) != response["game_count"]:
        raise SteamError(
            "Steam returned an incomplete game library. Nothing was changed."
        )
    return games


def catalogue_pages(
    *,
    modified_since: int | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[dict[str, str]]:
    last_appid = 0
    while True:
        params = {"max_results": 10000}
        if modified_since is not None:
            params["if_modified_since"] = modified_since
        if last_appid:
            params["last_appid"] = last_appid
        data = _get("/IStoreService/GetAppList/v1/", params, transport=transport)
        if not isinstance(data, dict):
            raise SteamError("Steam returned an invalid game catalogue.")
        response = data.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("apps"), list):
            raise SteamError("Steam returned an invalid game catalogue.")
        apps = response["apps"]
        page = {}
        for item in apps:
            if (
                not isinstance(item, dict)
                or type(item.get("appid")) is not int
                or item["appid"] <= last_appid
            ):
                raise SteamError("Steam returned an invalid game catalogue.")
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                page[str(item["appid"])] = name.strip()[:200]
        yield page
        if not response.get("have_more_results"):
            return
        next_appid = response.get("last_appid")
        if type(next_appid) is not int or next_appid <= last_appid:
            raise SteamError("Steam returned an incomplete game catalogue.")
        last_appid = next_appid
