from pathlib import Path
from datetime import datetime, timezone
from html import escape
import re


OUTPUT_DIR = Path("output")
OUTPUT_FILE = Path("opds.xml")

BASE_URL = (
    "https://jethro530-getstar.github.io/"
    "hn-kobo-intelligence"
)

CATALOG_TITLE = "HN Intelligence"


def parse_issue_filename(path):
    """
    支援：
    2026-09-12-AM.html
    2026-09-12-PM.html
    2026-09-12-AM-2.html
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

    hour = 6 if period == "AM" else 18

    dt = datetime(
        year,
        month,
        day,
        hour,
        0,
        tzinfo=timezone.utc,
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

    issues.sort(
        key=lambda item: (
            item["date"],
            item["revision"],
        ),
        reverse=True,
    )

    return issues


def iso_time(dt):
    return dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def build_entry(issue):
    path = issue["path"]

    filename = path.name

    url = (
        f"{BASE_URL}/output/{filename}"
    )

    display_name = (
        filename
        .replace(".html", "")
        .replace("-", " ")
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

  <id>
    tag:jethro530-getstar.github.io,2026:
    hn-kobo-intelligence
  </id>

  <title>{CATALOG_TITLE}</title>

  <updated>{iso_time(now)}</updated>

  <author>
    <name>HN Intelligence</name>
  </author>

  <link
    rel="self"
    href="{self_url}"
    type="application/atom+xml;profile=opds-catalog;kind=acquisition"
  />

  <link
    rel="start"
    href="{self_url}"
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
        f"Generated {OUTPUT_FILE}"
    )

    print(
        f"Issues: {len(issues)}"
    )


if __name__ == "__main__":
    main()
