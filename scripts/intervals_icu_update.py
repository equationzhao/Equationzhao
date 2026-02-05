#!/usr/bin/env python3
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://intervals.icu"
START_MARKER = "<!-- INTERVALS_ICU:START -->"
END_MARKER = "<!-- INTERVALS_ICU:END -->"


def get_env(name: str, default: str | None = None, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


def build_auth_header(username: str, api_key: str) -> str:
    token = f"{username}:{api_key}".encode("utf-8")
    return "Basic " + base64.b64encode(token).decode("ascii")


def fetch_json(path: str, params: dict[str, object], headers: dict[str, str]) -> object:
    query = urlencode(params, doseq=True)
    url = f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"
    request = Request(url, headers=headers)
    with urlopen(request) as response:
        return json.load(response)


def parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return dt.datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None


def format_distance(distance_m: float | None) -> str:
    if not distance_m:
        return "-"
    return f"{distance_m / 1000:.1f} km"


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "-"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def format_load(load: float | None) -> str:
    if load is None:
        return "-"
    return str(int(round(load)))


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def extract_load(activity: dict[str, object]) -> float:
    for key in ("icu_training_load", "power_load", "hr_load", "pace_load"):
        value = activity.get(key)
        if value is not None:
            return float(value)
    return 0.0


def render_svg(days: list[dt.date], loads: list[float]) -> str:
    width = max(320, len(days) * 10)
    height = 120
    padding = 10
    bar_gap = 2
    bar_width = (width - (2 * padding) - (len(days) - 1) * bar_gap) / len(days)
    max_load = max(loads) if loads else 0.0
    scale = (height - 2 * padding) / max_load if max_load > 0 else 0

    bars = []
    for index, load in enumerate(loads):
        bar_height = load * scale if scale else 0
        x = padding + index * (bar_width + bar_gap)
        y = height - padding - bar_height
        bars.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" '
            f'height="{bar_height:.2f}" rx="1" fill="#58a6ff" />'
        )

    bars_svg = "\n  ".join(bars)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Training load last {len(days)} days">'
        f'\n  <line x1="{padding}" y1="{height - padding}" x2="{width - padding}" '
        f'y2="{height - padding}" stroke="#8c959f" stroke-width="1" />'
        f'\n  {bars_svg}\n</svg>\n'
    )


def update_readme(readme_path: Path, content: str) -> None:
    text = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}", re.DOTALL
    )
    if not pattern.search(text):
        raise SystemExit("README markers not found.")
    replacement = f"{START_MARKER}\n{content}\n{END_MARKER}"
    updated = pattern.sub(replacement, text)
    if updated != text:
        readme_path.write_text(updated, encoding="utf-8")


def main() -> None:
    api_key = get_env("INTERVALS_API_KEY", required=True)
    athlete_id = get_env("INTERVALS_ATHLETE_ID", required=True)
    api_user = get_env("INTERVALS_API_USER", "API_KEY") or "API_KEY"
    days = int(get_env("INTERVALS_DAYS", "30") or "30")
    recent_limit = int(get_env("INTERVALS_RECENT_LIMIT", "5") or "5")
    summary_days = int(get_env("INTERVALS_SUMMARY_DAYS", "7") or "7")

    today = dt.date.today()
    oldest = today - dt.timedelta(days=days - 1)

    headers = {
        "Accept": "application/json",
        "Authorization": build_auth_header(api_user, api_key),
        "User-Agent": "intervals-icu-readme-bot",
    }
    params = {
        "oldest": oldest.isoformat(),
        "limit": 200,
    }

    raw = fetch_json(f"/api/v1/athlete/{athlete_id}/activities", params, headers)
    if not isinstance(raw, list):
        raise SystemExit("Unexpected activities response.")

    activities = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        when = parse_datetime(item.get("start_date_local") or item.get("start_date"))
        if not when:
            continue
        name_raw = item.get("name")
        name = str(name_raw).strip() if name_raw is not None else ""
        name = name if name else None
        activity_date = when.date()
        load_value = extract_load(item)
        activities.append(
            {
                "date": activity_date,
                "datetime": when,
                "name": name,
                "type": item.get("type") or "Unknown",
                "distance": item.get("distance"),
                "duration": item.get("moving_time") or item.get("elapsed_time"),
                "load": load_value,
                "load_display": format_load(load_value),
            }
        )

    activities.sort(key=lambda entry: entry["datetime"], reverse=True)
    recent_candidates = [activity for activity in activities if activity["name"]]
    recent = recent_candidates[:recent_limit]

    summary_start = today - dt.timedelta(days=summary_days - 1)
    summary_activities = [a for a in activities if a["date"] >= summary_start]
    total_distance = sum(float(a["distance"] or 0) for a in summary_activities)
    total_duration = sum(float(a["duration"] or 0) for a in summary_activities)
    total_load = sum(float(a["load"] or 0) for a in summary_activities)

    daily_load = {oldest + dt.timedelta(days=i): 0.0 for i in range(days)}
    for activity in activities:
        if activity["date"] in daily_load:
            daily_load[activity["date"]] += float(activity["load"] or 0)

    day_list = list(daily_load.keys())
    load_list = [daily_load[day] for day in day_list]

    repo_root = Path(__file__).resolve().parents[1]
    assets_dir = repo_root / "assets"
    assets_dir.mkdir(exist_ok=True)
    svg_path = assets_dir / "intervals-load.svg"
    svg_path.write_text(render_svg(day_list, load_list), encoding="utf-8")

    if summary_activities:
        summary_line = (
            f"**Last {summary_days} days:** {len(summary_activities)} activities · "
            f"{format_distance(total_distance)} · {format_duration(total_duration)} · "
            f"Load {format_load(total_load)}"
        )
    else:
        summary_line = f"**Last {summary_days} days:** No activities."

    lines = [summary_line, "", f"![Training load last {days} days](assets/intervals-load.svg)", ""]
    if recent:
        lines.extend(
            [
                "| Date | Activity | Type | Distance | Time | Load |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for activity in recent:
            lines.append(
                "| {date} | {name} | {type} | {distance} | {time} | {load} |".format(
                    date=activity["date"].isoformat(),
                    name=escape_md(str(activity["name"])),
                    type=escape_md(str(activity["type"])),
                    distance=format_distance(activity["distance"]),
                    time=format_duration(activity["duration"]),
                    load=activity["load_display"],
                )
            )
    else:
        lines.append("_No recent activities._")

    content = "\n".join(lines).strip()
    update_readme(repo_root / "README.md", content)


if __name__ == "__main__":
    main()
