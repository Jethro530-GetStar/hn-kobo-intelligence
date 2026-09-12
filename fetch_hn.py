from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from html import escape, unescape
from html.parser import HTMLParser
import json
import re
import time

import trafilatura


# ============================================================
# 基本設定
# ============================================================

HN_API = "https://hacker-news.firebaseio.com/v0"

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

OUTPUT_DIR = Path("output")

TARGET_STORIES = 10
MIN_FULLTEXT = 3

CANDIDATES_PER_FEED = 60
MAX_PER_DOMAIN = 2
MAX_ARTICLE_ATTEMPTS = 40

REQUEST_TIMEOUT = 20

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/152.0 Safari/537.36"
)

HN_FEEDS = {
    "Best": "beststories",
    "Top": "topstories",
    "New": "newstories",
    "Show HN": "showstories",
    "Ask HN": "askstories",
}


# ============================================================
# HTML → 純文字
# ============================================================

class SimpleHTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data:
            self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {
            "p",
            "div",
            "br",
            "li",
            "pre",
            "blockquote",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {
            "p",
            "div",
            "li",
            "pre",
            "blockquote",
        }:
            self.parts.append("\n")


def html_to_text(value):
    if not value:
        return ""

    parser = SimpleHTMLTextExtractor()

    try:
        parser.feed(value)
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(
            r"<[^>]+>",
            " ",
            value,
        )

    text = unescape(text)

    text = re.sub(
        r"\r\n?",
        "\n",
        text,
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n[ \t]+",
        "\n",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


# ============================================================
# 網路工具
# ============================================================

def fetch_json(url):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:
        return json.loads(
            response.read().decode(
                "utf-8"
            )
        )


def fetch_html(url):
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,"
                "application/xhtml+xml,"
                "application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
        },
    )

    with urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:
        content_type = (
            response.headers.get(
                "Content-Type",
                "",
            )
            .lower()
        )

        if (
            "text/html" not in content_type
            and
            "application/xhtml+xml"
            not in content_type
            and
            content_type
        ):
            return None

        raw = response.read()

        encoding = (
            response.headers.get_content_charset()
            or "utf-8"
        )

        try:
            return raw.decode(
                encoding,
                errors="replace",
            )
        except Exception:
            return raw.decode(
                "utf-8",
                errors="replace",
            )


# ============================================================
# Hacker News API
# ============================================================

def fetch_feed(feed_name):
    endpoint = HN_FEEDS[feed_name]

    url = (
        f"{HN_API}/{endpoint}.json"
    )

    ids = fetch_json(url)

    if not isinstance(ids, list):
        return []

    return ids[
        :CANDIDATES_PER_FEED
    ]


def fetch_item(item_id):
    url = (
        f"{HN_API}/item/"
        f"{item_id}.json"
    )

    try:
        return fetch_json(url)
    except Exception as exc:
        print(
            f"[WARN] item {item_id}: "
            f"{exc}"
        )
        return None


# ============================================================
# 收集 HN 候選文章
# ============================================================

def collect_candidates():
    story_map = {}

    for feed_name in HN_FEEDS:
        print(
            f"Fetching feed: "
            f"{feed_name}"
        )

        try:
            ids = fetch_feed(
                feed_name
            )
        except Exception as exc:
            print(
                f"[WARN] feed "
                f"{feed_name}: {exc}"
            )
            continue

        for position, story_id in enumerate(
            ids,
            start=1,
        ):
            if story_id not in story_map:
                story_map[story_id] = {
                    "id": story_id,
                    "feed_positions": {},
                }

            story_map[
                story_id
            ][
                "feed_positions"
            ][
                feed_name
            ] = position

    print(
        "Unique candidate IDs:",
        len(story_map),
    )

    candidates = []

    for index, (
        story_id,
        metadata,
    ) in enumerate(
        story_map.items(),
        start=1,
    ):
        item = fetch_item(
            story_id
        )

        if not item:
            continue

        if item.get("dead"):
            continue

        if item.get("deleted"):
            continue

        if item.get("type") != "story":
            continue

        title = (
            item.get("title")
            or ""
        ).strip()

        if not title:
            continue

        item[
            "feed_positions"
        ] = metadata[
            "feed_positions"
        ]

        candidates.append(
            item
        )

        if index % 40 == 0:
            print(
                "Fetched items:",
                index,
            )

        time.sleep(0.02)

    return candidates


# ============================================================
# HN 原生排序
# ============================================================

def candidate_sort_key(item):
    positions = (
        item.get(
            "feed_positions",
            {},
        )
    )

    if positions:
        best_position = min(
            positions.values()
        )
    else:
        best_position = 999999

    feed_count = len(
        positions
    )

    score = (
        item.get("score")
        or 0
    )

    comments = (
        item.get("descendants")
        or 0
    )

    return (
        best_position,
        -feed_count,
        -score,
        -comments,
    )


# ============================================================
# URL / Domain
# ============================================================

def get_domain(url):
    if not url:
        return "news.ycombinator.com"

    try:
        host = (
            urlparse(url)
            .hostname
            or ""
        ).lower()

        if host.startswith(
            "www."
        ):
            host = host[4:]

        return (
            host
            or
            "unknown"
        )

    except Exception:
        return "unknown"


def hn_discussion_url(
    story_id
):
    return (
        "https://news.ycombinator.com/"
        f"item?id={story_id}"
    )


# ============================================================
# 全文擷取
# ============================================================

def extract_fulltext(url):
    if not url:
        return None

    try:
        downloaded = fetch_html(
            url
        )

        if not downloaded:
            return None

        text = trafilatura.extract(
            downloaded,
            url=url,
            include_comments=False,
            include_tables=True,
            include_links=False,
            include_images=False,
            favor_precision=True,
            deduplicate=True,
        )

        if not text:
            return None

        text = text.strip()

        if len(text) < 1000:
            return None

        return text

    except Exception as exc:
        print(
            "[WARN] extraction failed:",
            url,
            exc,
        )

        return None


# ============================================================
# 選文章
# ============================================================

def select_stories(candidates):
    ranked = sorted(
        candidates,
        key=candidate_sort_key,
    )

    selected = []

    domain_counts = {}

    fulltext_count = 0
    article_attempts = 0

    for item in ranked:
        url = item.get("url")

        is_external = bool(
            url
            and
            "news.ycombinator.com"
            not in url
        )

        domain = get_domain(
            url
        )

        current_domain_count = (
            domain_counts.get(
                domain,
                0,
            )
        )

        if (
            current_domain_count
            >= MAX_PER_DOMAIN
        ):
            continue

        fulltext = None

        if (
            is_external
            and
            article_attempts
            < MAX_ARTICLE_ATTEMPTS
        ):
            article_attempts += 1

            print(
                f"[{article_attempts}/"
                f"{MAX_ARTICLE_ATTEMPTS}] "
                f"Extracting: "
                f"{item.get('title', '')}"
            )

            fulltext = (
                extract_fulltext(
                    url
                )
            )

        hn_text = html_to_text(
            item.get("text")
            or ""
        )

        if fulltext:
            content_type = (
                "FULL TEXT"
            )
            content = fulltext
            fulltext_count += 1

        elif hn_text:
            content_type = (
                "HN TEXT"
            )
            content = hn_text

        else:
            content_type = (
                "REFERENCE"
            )
            content = ""

        selected_item = dict(
            item
        )

        selected_item[
            "domain"
        ] = domain

        selected_item[
            "content_type"
        ] = content_type

        selected_item[
            "content"
        ] = content

        selected.append(
            selected_item
        )

        domain_counts[
            domain
        ] = (
            current_domain_count
            + 1
        )

        # 正常條件：
        # 已有至少 10 篇
        # 且至少 3 篇成功擷取全文
        if (
            len(selected)
            >= TARGET_STORIES
            and
            fulltext_count
            >= MIN_FULLTEXT
        ):
            break

        # 若全文不足，可以超過 10 篇繼續找
        # 但全文嘗試上限為 MAX_ARTICLE_ATTEMPTS
        if (
            len(selected)
            >= TARGET_STORIES
            and
            fulltext_count
            < MIN_FULLTEXT
            and
            article_attempts
            >= MAX_ARTICLE_ATTEMPTS
        ):
            break

    print()
    print(
        "Selected stories:",
        len(selected),
    )

    print(
        "Full text:",
        fulltext_count,
    )

    print(
        "Article attempts:",
        article_attempts,
    )

    if len(selected) < TARGET_STORIES:
        print(
            "[WARN] fewer than "
            f"{TARGET_STORIES} "
            "stories selected."
        )

    if fulltext_count < MIN_FULLTEXT:
        print(
            "[WARN] fewer than "
            f"{MIN_FULLTEXT} "
            "full-text articles."
        )

    return selected


# ============================================================
# 顯示 HN 排名
# ============================================================

def render_feed_positions(item):
    positions = (
        item.get(
            "feed_positions",
            {},
        )
    )

    if not positions:
        return "HN"

    ordered = sorted(
        positions.items(),
        key=lambda pair: pair[1],
    )

    return " · ".join(
        f"{name} #{position}"
        for name, position
        in ordered
    )


# ============================================================
# 純文字 → HTML 段落
# ============================================================

def text_to_html(text):
    if not text:
        return ""

    text = text.strip()

    blocks = re.split(
        r"\n\s*\n+",
        text,
    )

    html_parts = []

    for block in blocks:
        block = block.strip()

        if not block:
            continue

        lines = [
            line.strip()
            for line
            in block.splitlines()
            if line.strip()
        ]

        if not lines:
            continue

        safe_text = "<br>".join(
            escape(line)
            for line in lines
        )

        html_parts.append(
            '<div class="para">'
            f"{safe_text}"
            "</div>"
        )

    return "\n".join(
        html_parts
    )


# ============================================================
# KOReader-first HTML
# ============================================================

def build_html(
    stories,
    generated_at,
    issue_label,
):
    full_count = sum(
        1
        for story in stories
        if story.get(
            "content_type"
        ) == "FULL TEXT"
    )

    sources = sorted(
        {
            story.get(
                "domain",
                "unknown",
            )
            for story
            in stories
        }
    )

    source_text = ", ".join(
        sources
    )

    story_blocks = []

    for index, story in enumerate(
        stories,
        start=1,
    ):
        title = escape(
            story.get(
                "title",
                "Untitled",
            )
        )

        author = escape(
            str(
                story.get(
                    "by",
                    "unknown",
                )
            )
        )

        score = (
            story.get(
                "score"
            )
            or 0
        )

        comments = (
            story.get(
                "descendants"
            )
            or 0
        )

        story_id = story.get(
            "id"
        )

        url = story.get(
            "url"
        )

        discussion_url = (
            hn_discussion_url(
                story_id
            )
        )

        domain = escape(
            story.get(
                "domain",
                "unknown",
            )
        )

        feed_positions = escape(
            render_feed_positions(
                story
            )
        )

        content_type = (
            story.get(
                "content_type"
            )
            or "REFERENCE"
        )

        badge_class = (
            content_type
            .lower()
            .replace(
                " ",
                "-",
            )
        )

        content = (
            story.get(
                "content"
            )
            or ""
        )

        content_html = (
            text_to_html(
                content
            )
        )

        links = []

        if url:
            links.append(
                '<a href="'
                f'{escape(url, quote=True)}'
                '">Original article</a>'
            )

        links.append(
            '<a href="'
            f'{escape(discussion_url, quote=True)}'
            '">HN discussion</a>'
        )

        links_html = (
            " · ".join(
                links
            )
        )

        if content_html:
            body_html = f"""
<div class="body-label">Article</div>
<div class="article-body">
{content_html}
</div>
"""
        else:
            body_html = """
<div class="reference-note">
Full article text was not extracted.
Use the links above to open the original article
or Hacker News discussion.
</div>
"""

        # 注意：
        # 不使用 article / section / h1 / h2 / h3
        # 避免 KOReader / CREngine 對語意標籤套用
        # 額外分頁規則。
        story_block = f"""
<div class="story">
  <div class="story-title">
    <span class="number">{index}.</span>
    {title}
  </div>

  <div class="badge {badge_class}">
    {escape(content_type)}
  </div>

  <div class="meta">
    {score} points ·
    {comments} comments ·
    {author}
  </div>

  <div class="meta">
    {domain}
  </div>

  <div class="meta ranks">
    {feed_positions}
  </div>

  <div class="links">
    {links_html}
  </div>

  {body_html}
</div>
"""

        story_blocks.append(
            story_block
        )

    stories_html = "\n".join(
        story_blocks
    )

    # --------------------------------------------------------
    # 關鍵：
    # 1. 不使用 header/main/article/section/h1/h2
    # 2. 所有 break-before / page-break-before 強制 auto
    # 3. 不使用 break-inside: avoid
    # 4. body 第一個實體元素直接就是 issue-head
    # 5. 無封面頁、無 title page
    # --------------------------------------------------------

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HN Intelligence {escape(issue_label)}</title>

<style>
html {{
    margin: 0 !important;
    padding: 0 !important;
    border: 0 !important;
}}

body {{
    margin: 0 !important;
    padding: 0 !important;
    border: 0 !important;

    font-family:
        Georgia,
        "Noto Serif",
        serif;

    font-size: 1em;
    line-height: 1.42;

    orphans: 1 !important;
    widows: 1 !important;
}}

/*
KOReader / CREngine 可能對標題或區塊套用
內建分頁規則。

這裡全面取消文件自己的前後換頁要求。
*/
* {{
    break-before: auto !important;
    page-break-before: auto !important;

    break-after: auto !important;
    page-break-after: auto !important;
}}

/*
尤其確保第一個元素絕不要求換頁。
*/
body > :first-child {{
    margin-top: 0 !important;
    padding-top: 0 !important;

    break-before: auto !important;
    page-break-before: auto !important;
}}

/*
不使用 break-inside: avoid。
讓 CREngine 自己自然分頁，
避免大型區塊被整個推到下一頁。
*/
.issue-head,
.story,
.story-title,
.article-body,
.para {{
    break-inside: auto !important;
    page-break-inside: auto !important;
}}

.issue-head {{
    margin:
        0
        0.55em
        0.55em
        0.55em;

    padding:
        0.18em
        0
        0.48em
        0;

    border-bottom:
        1px solid #777;
}}

.issue-title {{
    margin: 0;
    padding: 0;

    font-size: 1.42em;
    line-height: 1.15;
    font-weight: bold;
}}

.issue-sub {{
    margin-top: 0.18em;

    font-size: 0.82em;
    line-height: 1.28;
}}

.story {{
    margin:
        0
        0.55em
        0
        0.55em;

    padding:
        0.62em
        0
        0.78em
        0;

    border-top:
        1px solid #aaa;
}}

.story:first-of-type {{
    border-top: 0;
    padding-top: 0.28em;
}}

.story-title {{
    margin: 0;
    padding: 0;

    font-size: 1.24em;
    line-height: 1.25;
    font-weight: bold;
}}

.number {{
    font-weight: normal;
}}

.badge {{
    display: inline-block;

    margin:
        0.35em
        0
        0.15em
        0;

    padding:
        0.08em
        0.30em;

    border:
        1px solid #777;

    font-size: 0.72em;
    font-weight: bold;
    line-height: 1.2;
}}

.meta {{
    margin-top: 0.12em;

    font-size: 0.78em;
    line-height: 1.28;
}}

.ranks {{
    font-style: italic;
}}

.links {{
    margin:
        0.28em
        0
        0.48em
        0;

    font-size: 0.82em;
    line-height: 1.3;
}}

.links a {{
    text-decoration: underline;
}}

.body-label {{
    margin:
        0.48em
        0
        0.30em
        0;

    font-size: 0.86em;
    font-weight: bold;
}}

.article-body {{
    margin: 0;
    padding: 0;
}}

.para {{
    margin:
        0
        0
        0.72em
        0;

    padding: 0;

    line-height: 1.45;
}}

.reference-note {{
    margin:
        0.45em
        0
        0.35em
        0;

    padding:
        0.35em
        0;

    font-size: 0.88em;
    font-style: italic;
    line-height: 1.38;
}}

@media screen and (min-width: 48em) {{
    body {{
        max-width: 48em;
        margin-left: auto !important;
        margin-right: auto !important;
    }}
}}
</style>
</head>
<body><div class="issue-head"><div class="issue-title">HN Intelligence</div><div class="issue-sub">{escape(issue_label)} · Generated {escape(generated_at.strftime("%Y-%m-%d %H:%M %Z"))}</div><div class="issue-sub">Stories {len(stories)} · Full {full_count}</div><div class="issue-sub">Sources: {escape(source_text)}</div></div>
{stories_html}
</body>
</html>
"""

    return html


# ============================================================
# AM / PM 歷史檔名
# ============================================================

def determine_period(now):
    if now.hour < 12:
        return "AM"

    return "PM"


def make_unique_output_path(
    now,
    period,
):
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    date_string = (
        now.strftime(
            "%Y-%m-%d"
        )
    )

    base_name = (
        f"{date_string}-"
        f"{period}"
    )

    first_path = (
        OUTPUT_DIR
        /
        f"{base_name}.html"
    )

    if not first_path.exists():
        return first_path

    revision = 2

    while True:
        candidate = (
            OUTPUT_DIR
            /
            (
                f"{base_name}-"
                f"{revision}.html"
            )
        )

        if not candidate.exists():
            return candidate

        revision += 1


# ============================================================
# 主程式
# ============================================================

def main():
    now = datetime.now(
        TAIPEI_TZ
    )

    period = determine_period(
        now
    )

    issue_label = (
        f"{now:%Y-%m-%d} "
        f"{period}"
    )

    print(
        "================================"
    )
    print(
        "HN Kobo Intelligence"
    )
    print(
        "================================"
    )
    print(
        "Taipei time:",
        now.isoformat(),
    )
    print(
        "Issue:",
        issue_label,
    )
    print()

    candidates = (
        collect_candidates()
    )

    print()
    print(
        "Valid candidates:",
        len(candidates),
    )

    stories = select_stories(
        candidates
    )

    if not stories:
        raise RuntimeError(
            "No Hacker News stories "
            "were selected."
        )

    html = build_html(
        stories=stories,
        generated_at=now,
        issue_label=issue_label,
    )

    output_path = (
        make_unique_output_path(
            now,
            period,
        )
    )

    # x = exclusive create
    # 即使程式邏輯出錯，也不覆蓋歷史期數。
    with output_path.open(
        "x",
        encoding="utf-8",
        newline="\n",
    ) as file:
        file.write(
            html
        )

    full_count = sum(
        1
        for story in stories
        if story.get(
            "content_type"
        ) == "FULL TEXT"
    )

    print()
    print(
        "================================"
    )
    print(
        "Generated:",
        output_path,
    )
    print(
        "Stories:",
        len(stories),
    )
    print(
        "Full text:",
        full_count,
    )
    print(
        "================================"
    )


if __name__ == "__main__":
    main()
