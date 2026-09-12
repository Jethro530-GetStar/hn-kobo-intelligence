from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from html import escape
import re


OUTPUT_DIR = Path("output")
OUTPUT_FILE = Path("opds.xml")

BASE_URL = (
    "https://jethro530-getstar.github.io/"
    "hn-kobo-intelligence"
)

CATALOG_TITLE = "HN Intelligence"

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def parse_issue_filename(path):
    """
    支援：
    2026-09-12-AM.html
    2026-09-12-PM.html
    2026-09-12-AM-2.html
    2026-09-12-PM-2.html
    """

    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})-(AM|PM)"
        r"(?:-(\d+))?\.html$",
        path.name,
    )

    if not match:
        return None

    year = int(match.group(1))
    month = int(match.group(2))
    day = int(match.group(3))
    period = match.group(4)
    revision = int(match.group(5) or 1)

    # 這裡只是給 OPDS 一個排序用的代表時間
    # AM = 台灣時間 06:00
    # PM = 台灣時間 18:00
    hour = 6 if period == "AM" else 18

    dt = datetime(
        year,
        month,
        day,
        hour,
        0,
        tzinfo=TAIPEI_TZ,
    )

    return {
        "date": dt,
        "period": period,
        "revision": revision,
    }


def get_issue_files():
    if not OUTPUT_DIR.exists():
        return []

    issues = []

    for path in OUTPUT_DIR.glob("*.html"):
        info = parse_issue_filename(path)

        if not info:
            continue

        issues.append(
            {
                "path": path,
                **info,
            }
        )

    # 新的排前面
    # 同一期如果有 AM-2、AM-3
    # revision 較大的排前面
    issues.sort(
        key=lambda item: (
            item["date"],
            item["revision"],
        ),
        reverse=True,
    )

    return issues


def iso_time(dt):
    """
    OPDS / Atom 建議使用 UTC ISO 8601 時間。

    例如：
    台灣 2026-09-12 18:00 +08:00
    會輸出：
    2026-09-12T10:00:00Z
    """

    return (
        dt.astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def build_display_name(issue):
    path = issue["path"]

    filename = path.stem

    # 例如：
    # 2026-09-12-AM
    # 2026-09-12-AM-2

    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})-(AM|PM)"
        r"(?:-(\d+))?$",
        filename,
    )

    if not match:
        return filename

    year = match.group(1)
    month = match.group(2)
    day = match.group(3)
    period = match.group(4)
    revision = match.group(5)

    display_name = (
        f"{year}-{month}-{day} {period}"
    )

    if revision:
        display_name += f" #{revision}"

    return display_name


def build_entry(issue):
    path = issue["path"]

    filename = path.name

    url = (
        f"{BASE_URL}/output/{filename}"
    )

    display_name = build_display_name(
        issue
    )

    updated = iso_time(
        issue["date"]
    )

    entry_id = (
        f"tag:jethro530-getstar.github.io,"
        f"{issue['date']:%Y-%m-%d}:"
        f"hn-kobo-intelligence/{filename}"
    )

    return f"""
  <entry>

    <title>{escape(display_name)}</title>

    <id>{escape(entry_id)}</id>

    <updated>{updated}</updated>

    <summary type="text">
      Hacker News intelligence digest:
      {escape(display_name)}
    </summary>

    <link
      rel="http://opds-spec.org/acquisition"
      href="{escape(url)}"
      type="text/html"
    />

  </entry>
"""


def build_catalog(issues):
    now = datetime.now(
        timezone.utc
    )

    entries = "\n".join(
        build_entry(issue)
        for issue in issues
    )

    self_url = (
        f"{BASE_URL}/opds.xml"
    )

    return f"""<?xml version="1.0" encoding="UTF-8"?>

<feed
  xmlns="http://www.w3.org/2005/Atom"
  xmlns:opds="http://opds-spec.org/2010/catalog"
>

  <id>tag:jethro530-getstar.github.io,2026:hn-kobo-intelligence</id>

  <title>{escape(CATALOG_TITLE)}</title>

  <updated>{iso_time(now)}</updated>

  <author>
    <name>HN Intelligence</name>
  </author>

  <link
    rel="self"
    href="{escape(self_url)}"
    type="application/atom+xml;profile=opds-catalog;kind=acquisition"
  />

  <link
    rel="start"
    href="{escape(self_url)}"
    type="application/atom+xml;profile=opds-catalog;kind=acquisition"
  />

{entries}

</feed>
"""


def main():
    issues = get_issue_files()

    if not issues:
        raise RuntimeError(
            "No HTML issues found in output/"
        )

    xml = build_catalog(
        issues
    )

    OUTPUT_FILE.write_text(
        xml,
        encoding="utf-8",
    )

    print(
        f"Generated: {OUTPUT_FILE}"
    )

    print(
        f"Issues: {len(issues)}"
    )

    print()

    for issue in issues:
        print(
            "-",
            build_display_name(issue),
            "->",
            issue["path"],
        )


if __name__ == "__main__":
    main()
