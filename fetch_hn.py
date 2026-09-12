import json
import re
import html
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import trafilatura


HN_API = "https://hacker-news.firebaseio.com/v0"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

OUTPUT_DIR = Path("output")

TARGET_STORIES = 10
MIN_FULLTEXT = 3
CANDIDATES_PER_FEED = 60
MAX_PER_DOMAIN = 2
MAX_ARTICLE_ATTEMPTS = 40

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0 Safari/537.36"
)

HN_FEEDS = {
    "Best": "beststories",
    "Top": "topstories",
    "New": "newstories",
    "Show HN": "showstories",
    "Ask HN": "askstories",
}


def get_json(url, timeout=20):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def download_html(url, timeout=25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "").lower()

            if (
                "text/html" not in content_type
                and "application/xhtml+xml" not in content_type
            ):
                return None

            raw = response.read()

            charset = response.headers.get_content_charset() or "utf-8"

            try:
                return raw.decode(charset, errors="replace")
            except LookupError:
                return raw.decode("utf-8", errors="replace")

    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        ValueError,
    ):
        return None


def fetch_feed(endpoint):
    url = f"{HN_API}/{endpoint}.json"
    data = get_json(url)

    if not isinstance(data, list):
        return []

    return data[:CANDIDATES_PER_FEED]


def fetch_story(story_id):
    try:
        data = get_json(f"{HN_API}/item/{story_id}.json")
    except Exception:
        return None

    if not data:
        return None

    if data.get("type") != "story":
        return None

    if data.get("dead") or data.get("deleted"):
        return None

    return data


def collect_candidates():
    feed_story_ids = {}

    for feed_name, endpoint in HN_FEEDS.items():
        print(f"Fetching HN feed: {feed_name}")
        ids = fetch_feed(endpoint)
        feed_story_ids[feed_name] = ids
        time.sleep(0.2)

    story_map = {}

    for feed_name, ids in feed_story_ids.items():
        for position, story_id in enumerate(ids, start=1):
            if story_id not in story_map:
                story_map[story_id] = {
                    "id": story_id,
                    "feeds": {},
                }

            story_map[story_id]["feeds"][feed_name] = position

    candidates = []

    for story_id, info in story_map.items():
        story = fetch_story(story_id)

        if not story:
            continue

        story["feeds"] = info["feeds"]

        positions = list(info["feeds"].values())

        story["_best_native_rank"] = min(positions)
        story["_feed_count"] = len(info["feeds"])

        candidates.append(story)

        time.sleep(0.08)

    candidates.sort(
        key=lambda s: (
            s["_best_native_rank"],
            -s["_feed_count"],
            -s.get("score", 0),
        )
    )

    return candidates


def get_domain(url):
    if not url:
        return "news.ycombinator.com"

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        domain = re.sub(r"^www\.", "", domain)

        return domain or "news.ycombinator.com"
    except Exception:
        return "unknown"


def clean_hn_text(text):
    if not text:
        return ""

    text = html.unescape(text)

    text = re.sub(
        r"<p>",
        "\n\n",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"<a\s+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
        r"\2 (\1)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(r"<[^>]+>", "", text)

    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def text_to_html(text):
    if not text:
        return ""

    paragraphs = re.split(r"\n\s*\n", text.strip())

    blocks = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()

        if not paragraph:
            continue

        paragraph = html.escape(paragraph)
        paragraph = paragraph.replace("\n", "<br>")

        blocks.append(f"<p>{paragraph}</p>")

    return "\n".join(blocks)


def extract_full_article(url):
    if not url:
        return None

    source_html = download_html(url)

    if not source_html:
        return None

    try:
        extracted = trafilatura.extract(
            source_html,
            url=url,
            include_comments=False,
            include_tables=True,
            include_links=False,
            include_images=False,
            favor_precision=True,
            deduplicate=True,
        )
    except Exception:
        return None

    if not extracted:
        return None

    extracted = extracted.strip()

    if len(extracted) < 1000:
        return None

    return extracted


def select_stories(candidates):
    selected = []

    domain_counts = {}
    fulltext_count = 0
    article_attempts = 0

    used_ids = set()

    for story in candidates:
        if len(selected) >= TARGET_STORIES:
            break

        url = story.get("url")
        domain = get_domain(url)

        if domain_counts.get(domain, 0) >= MAX_PER_DOMAIN:
            continue

        story_copy = dict(story)

        fulltext = None

        if url and article_attempts < MAX_ARTICLE_ATTEMPTS:
            article_attempts += 1

            print(
                f"Trying article {article_attempts}: "
                f"{story.get('title', '')[:70]}"
            )

            fulltext = extract_full_article(url)

        if fulltext:
            story_copy["fulltext"] = fulltext
            story_copy["content_type"] = "fulltext"
            fulltext_count += 1

        else:
            hn_text = clean_hn_text(story.get("text", ""))

            if hn_text:
                story_copy["fulltext"] = hn_text
                story_copy["content_type"] = "hntext"
            else:
                story_copy["fulltext"] = ""
                story_copy["content_type"] = "reference"

        selected.append(story_copy)
        used_ids.add(story["id"])

        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    if fulltext_count < MIN_FULLTEXT:
        for story in candidates:
            if fulltext_count >= MIN_FULLTEXT:
                break

            if article_attempts >= MAX_ARTICLE_ATTEMPTS:
                break

            if story["id"] in used_ids:
                continue

            url = story.get("url")

            if not url:
                continue

            domain = get_domain(url)

            if domain_counts.get(domain, 0) >= MAX_PER_DOMAIN:
                continue

            article_attempts += 1

            print(
                f"Extra full-text attempt {article_attempts}: "
                f"{story.get('title', '')[:70]}"
            )

            fulltext = extract_full_article(url)

            if not fulltext:
                continue

            story_copy = dict(story)
            story_copy["fulltext"] = fulltext
            story_copy["content_type"] = "fulltext"

            selected.append(story_copy)
            used_ids.add(story["id"])

            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            fulltext_count += 1

    if fulltext_count < MIN_FULLTEXT:
        print(
            f"WARNING: only {fulltext_count} full-text articles found. "
            "Some sources may block automated extraction."
        )

    return selected


def build_feed_line(feeds):
    if not feeds:
        return ""

    order = [
        "Best",
        "Top",
        "New",
        "Show HN",
        "Ask HN",
    ]

    parts = []

    for name in order:
        if name in feeds:
            parts.append(f"{name} #{feeds[name]}")

    return " · ".join(parts)


def build_story_html(story, index):
    title = html.escape(story.get("title", "Untitled"))

    url = story.get("url")

    hn_url = (
        f"https://news.ycombinator.com/item?id={story['id']}"
    )

    domain = html.escape(get_domain(url))

    score = story.get("score", 0)

    comments = story.get("descendants", 0)

    author = html.escape(story.get("by", ""))

    feed_line = html.escape(
        build_feed_line(story.get("feeds", {}))
    )

    content_type = story.get(
        "content_type",
        "reference",
    )

    if content_type == "fulltext":
        badge = "FULL TEXT"
        section_title = "Article"
    elif content_type == "hntext":
        badge = "HN TEXT"
        section_title = "Discussion text"
    else:
        badge = "REFERENCE"
        section_title = ""

    content = story.get("fulltext", "")

    content_html = text_to_html(content)

    link_lines = []

    if url:
        safe_url = html.escape(
            url,
            quote=True,
        )

        link_lines.append(
            f'<a href="{safe_url}">'
            "Original article"
            "</a>"
        )

    link_lines.append(
        f'<a href="{html.escape(hn_url, quote=True)}">'
        "HN discussion"
        "</a>"
    )

    links_html = " · ".join(link_lines)

    body_html = ""

    if content_html:
        body_html = f"""
        <div class="article-body">
            {content_html}
        </div>
        """

    return f"""
    <section class="story">

        <div class="story-number">
            {index:02d}
        </div>

        <h2>{title}</h2>

        <div class="badge">
            {badge}
        </div>

        <div class="meta">
            <strong>{score}</strong> points
            ·
            <strong>{comments}</strong> comments
            ·
            {author}
        </div>

        <div class="source">
            {domain}
        </div>

        <div class="feeds">
            {feed_line}
        </div>

        <div class="links">
            {links_html}
        </div>

        {
            f'<h3>{section_title}</h3>'
            if section_title and content_html
            else ''
        }

        {body_html}

    </section>
    """


def build_html(stories, generated_at):
    full_count = sum(
        1
        for story in stories
        if story.get("content_type") == "fulltext"
    )

    issue_period = (
        "AM"
        if generated_at.hour < 12
        else "PM"
    )

    issue_name = (
        f"{generated_at:%Y-%m-%d}-{issue_period}"
    )

    story_blocks = "\n".join(
        build_story_html(story, i)
        for i, story in enumerate(
            stories,
            start=1,
        )
    )

    return f"""<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>
HN Intelligence {issue_name}
</title>

<style>

html {{
    margin: 0;
    padding: 0;
}}

body {{
    margin: 0;
    padding: 0.45em 0.55em;

    font-family:
        serif;

    line-height: 1.42;

    text-align: left;

    word-wrap: break-word;

    overflow-wrap: break-word;
}}

header {{
    margin: 0 0 1.2em 0;
    padding: 0;
}}

h1 {{
    margin: 0 0 0.2em 0;

    padding: 0;

    font-size: 1.45em;

    line-height: 1.15;
}}

.issue {{
    margin: 0.15em 0 0.4em 0;

    font-size: 1.05em;

    font-weight: bold;
}}

.summary {{
    margin-top: 0.7em;

    padding-top: 0.5em;

    border-top: 1px solid #777;

    line-height: 1.45;
}}

.summary div {{
    margin: 0.12em 0;
}}

.story {{
    margin: 0;

    padding: 0.9em 0 1.05em 0;

    border-top: 1px solid #888;

    page-break-before: auto;

    break-before: auto;

    page-break-inside: auto;

    break-inside: auto;
}}

.story-number {{
    margin: 0 0 0.22em 0;

    font-size: 0.9em;

    font-weight: bold;
}}

h2 {{
    margin: 0 0 0.35em 0;

    padding: 0;

    font-size: 1.25em;

    line-height: 1.2;
}}

.badge {{
    display: inline-block;

    margin: 0.1em 0 0.55em 0;

    padding: 0.08em 0.32em;

    border: 1px solid #777;

    font-size: 0.72em;

    font-weight: bold;
}}

.meta,
.source,
.feeds,
.links {{
    margin: 0.14em 0;

    font-size: 0.86em;

    line-height: 1.35;
}}

.source {{
    font-weight: bold;
}}

.links {{
    margin-bottom: 0.7em;
}}

a {{
    color: inherit;

    text-decoration: underline;
}}

h3 {{
    margin: 0.65em 0 0.35em 0;

    padding: 0;

    font-size: 1em;

    line-height: 1.2;
}}

.article-body {{
    margin: 0;

    padding: 0;
}}

.article-body p {{
    margin: 0 0 0.72em 0;

    padding: 0;

    line-height: 1.45;

    orphans: 2;

    widows: 2;
}}

strong {{
    font-weight: bold;
}}

@media screen {{
    body {{
        max-width: 48em;

        margin-left: auto;

        margin-right: auto;
    }}
}}

</style>

</head>

<body>

<header>

    <h1>
        HN Intelligence
    </h1>

    <div class="issue">
        {issue_name}
    </div>

    <div class="summary">

        <div>
            Generated:
            {generated_at:%Y-%m-%d %H:%M}
            Asia/Taipei
        </div>

        <div>
            Sources:
            Best · Top · New · Show HN · Ask HN
        </div>

        <div>
            Stories:
            <strong>{len(stories)}</strong>
            ·
            Full:
            <strong>{full_count}</strong>
        </div>

    </div>

</header>

{story_blocks}

</body>

</html>
"""


def get_unique_output_path(generated_at):
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    period = (
        "AM"
        if generated_at.hour < 12
        else "PM"
    )

    base_name = (
        f"{generated_at:%Y-%m-%d}-{period}"
    )

    path = (
        OUTPUT_DIR
        / f"{base_name}.html"
    )

    if not path.exists():
        return path

    counter = 2

    while True:
        candidate = (
            OUTPUT_DIR
            / f"{base_name}-{counter}.html"
        )

        if not candidate.exists():
            return candidate

        counter += 1


def main():
    generated_at = datetime.now(
        TAIPEI_TZ
    )

    print(
        "Generated at:",
        generated_at.isoformat(),
    )

    print(
        "Collecting Hacker News candidates..."
    )

    candidates = collect_candidates()

    print(
        f"Candidates collected: {len(candidates)}"
    )

    print(
        "Selecting stories and extracting articles..."
    )

    stories = select_stories(
        candidates
    )

    if len(stories) < TARGET_STORIES:
        raise RuntimeError(
            f"Only {len(stories)} stories selected; "
            f"expected at least {TARGET_STORIES}."
        )

    output_html = build_html(
        stories,
        generated_at,
    )

    output_path = get_unique_output_path(
        generated_at
    )

    with open(
        output_path,
        "x",
        encoding="utf-8",
    ) as f:
        f.write(output_html)

    full_count = sum(
        1
        for story in stories
        if story.get("content_type")
        == "fulltext"
    )

    print(
        f"Written: {output_path}"
    )

    print(
        f"Stories: {len(stories)}"
    )

    print(
        f"Full-text articles: {full_count}"
    )


if __name__ == "__main__":
    main()
