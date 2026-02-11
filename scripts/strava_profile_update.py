#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


BASE_URL = "https://www.strava.com/api/v3"
START_MARKER = "<!-- STRAVA_PROFILE:START -->"
END_MARKER = "<!-- STRAVA_PROFILE:END -->"


def load_env_file() -> dict[str, str]:
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return {}
    env_vars = {}
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_vars[key.strip()] = value.strip()
    return env_vars


def get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name)
    if not value:
        env_vars = load_env_file()
        value = env_vars.get(name)
    value = value if value is not None else default
    if required and not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict[str, Any]:
    auth_url = f"{BASE_URL}/oauth/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    request = Request(
        auth_url,
        data=urlencode(data).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(request) as response:
        return json.load(response)


def fetch_json(path: str, access_token: str) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    request = Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urlopen(request) as response:
        return json.load(response)


def format_distance(distance_m: float | None) -> str:
    if not distance_m:
        return "-"
    return f"{distance_m / 1000:.1f} km"


def format_time(seconds: int | None) -> str:
    if not seconds:
        return "-"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_number(num: int | None) -> str:
    if num is None:
        return "-"
    return f"{num:,}"


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def render_zones(zones: dict[str, Any] | None, zone_type: str) -> str:
    if not zones or "zones" not in zones:
        return "-"
    zone_list = zones["zones"]
    if not zone_list or not isinstance(zone_list, list):
        return "-"
    return " | ".join([f"{z['min']}-{z['max']}" for z in zone_list[:6]])


def update_readme(readme_path: Path, content: str) -> None:
    text = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL
    )
    if not pattern.search(text):
        replacement = f"{START_MARKER}\n{content}\n{END_MARKER}"
        updated = text + f"\n\n## Strava Profile\n\n{replacement}\n"
    else:
        replacement = f"{START_MARKER}\n{content}\n{END_MARKER}"
        updated = pattern.sub(replacement, text)
    if updated != text:
        readme_path.write_text(updated, encoding="utf-8")


def main() -> None:
    client_id = get_env("STRAVA_CLIENT_ID", required=True)
    client_secret = get_env("STRAVA_CLIENT_SECRET", required=True)
    refresh_token = get_env("STRAVA_REFRESH_TOKEN", required=True)

    token_data = refresh_access_token(client_id, client_secret, refresh_token)
    access_token = token_data.get("access_token")

    if not access_token:
        raise SystemExit("Failed to get access token")

    athlete = fetch_json("/athlete", access_token)
    stats = fetch_json(f"/athletes/{athlete['id']}/stats", access_token)

    import pprint
    print("=== Full Athlete Response ===")
    pprint.pprint(athlete)
    print("==============================")

    lines = []

    name = athlete.get("firstname", "") + " " + athlete.get("lastname", "")
    location = athlete.get("city", "") + ", " + athlete.get("country", "")
    bio = athlete.get("bio", "")

    lines.append(f"### 🚴 {escape_md(name.strip())}")
    if bio:
        lines.append(f"> {escape_md(bio)}")
    if location:
        lines.append(f"📍 {escape_md(location)}")

    bikes = athlete.get("bikes", [])
    if bikes:
        lines.append("")
        lines.append("#### 🚲 Bikes")
        for bike in bikes:
            bike_name = bike.get("name", "Unknown")
            bike_distance = format_distance(bike.get("distance"))
            is_primary = " ⭐" if bike.get("primary") else ""
            lines.append(f"- {escape_md(bike_name)}{is_primary}: {bike_distance}")

    shoes = athlete.get("shoes", [])
    if shoes:
        lines.append("")
        lines.append("#### 👟 Shoes")
        for shoe in shoes:
            shoe_name = shoe.get("name", "Unknown")
            shoe_distance = format_distance(shoe.get("distance"))
            is_primary = " ⭐" if shoe.get("primary") else ""
            lines.append(f"- {escape_md(shoe_name)}{is_primary}: {shoe_distance}")

    lines.append("")

    all_ride_stats = stats.get("all_ride_totals", {})
    all_run_stats = stats.get("all_run_totals", {})

    lines.append("#### 📊 Lifetime Stats")
    lines.append("| | Ride | Run |")
    lines.append("| --- | --- | --- |")
    lines.append(
        f"| Distance | {format_distance(all_ride_stats.get('distance'))} | {format_distance(all_run_stats.get('distance'))} |"
    )
    lines.append(
        f"| Time | {format_time(all_ride_stats.get('moving_time'))} | {format_time(all_run_stats.get('moving_time'))} |"
    )
    lines.append(
        f"| Activities | {format_number(all_ride_stats.get('count'))} | {format_number(all_run_stats.get('count'))} |"
    )
    lines.append(
        f"| Elevation Gain | {format_distance(all_ride_stats.get('elevation_gain'))} | {format_distance(all_run_stats.get('elevation_gain'))} |"
    )
    lines.append("")

    biggest_ride = stats.get("biggest_ride_distance")
    biggest_climb = stats.get("biggest_climb_elevation_gain")

    lines.append("#### 🏆 Personal Records")
    if biggest_ride:
        lines.append(f"- 🚴 Longest Ride: {format_distance(biggest_ride)}")
    if biggest_climb:
        lines.append(f"- ⛰️ Biggest Climb: {format_distance(biggest_climb)}")

    content = "\n".join(lines).strip()
    update_readme(Path(__file__).parent.parent / "README.md", content)


if __name__ == "__main__":
    main()
