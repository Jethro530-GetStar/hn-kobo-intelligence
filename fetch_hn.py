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

try:
    import trafilatura
except ImportError:
    raise SystemExit(
        "找不到 trafilatura。\n"
        "GitHub Actions 請先執行：pip install trafilatura"
    )


# =========================================================
# 基本設定
# =========================================================

HN_API = "https://hacker-news.firebaseio.com/v0"

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

OUTPUT_DIR = Path("output")

# 每一期至少幾則情報
TARGET_STORIES = 10

# 至少幾篇必須成功取得全文
MIN_FULLTEXT = 3

# 每個 HN 原生分類先抓多少候選
CANDIDATES_PER_FEED = 60

# 同一網域最多出現幾篇
MAX_PER_DOMAIN = 2

# 為了找足夠全文，最多檢查多少篇候選
MAX_ARTICLE_ATTEMPTS = 40

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/152 Safari/537.36 "
    "HN-Kobo-Intelligence/1.0"
)


# =========================================================
# Hacker News 原生資訊來源
# =========================================================

HN_FEEDS = {
    "Best": "beststories",
    "Top": "topstories",
    "New": "newstories",
    "Show HN": "showstories",
    "Ask HN": "askstories",
}


# =========================================================
# HTTP
# =========================================================

def get_json(url, timeout=20):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout
    ) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def download_html(url, timeout=25):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout
    ) as response:

        content_type = response.headers.get(
            "Content-Type",
            ""
        ).lower()

        if (
            "text/html" not in content_type
            and
            "application/xhtml+xml" not in content_type
        ):
            return None

        raw = response.read()

        charset = response.headers.get_content_charset()

        if not charset:
            charset = "utf-8"

        try:
            return raw.decode(
                charset,
                errors="replace"
            )
        except LookupError:
            return raw.decode(
                "utf-8",
                errors="replace"
            )


# =========================================================
# HN 資料
# =========================================================

def fetch_feed(feed_name):
    endpoint = HN_FEEDS[feed_name]

    print(f"取得 HN {feed_name}...")

    ids = get_json(
        f"{HN_API}/{endpoint}.json"
    )

    return ids[:CANDIDATES_PER_FEED]


def fetch_story(story_id):
    try:
        story = get_json(
            f"{HN_API}/item/{story_id}.json"
        )

        if not story:
            return None

        if story.get("type") != "story":
            return None

        if story.get("deleted"):
            return None

        if story.get("dead"):
            return None

        return story

    except Exception as exc:
        print(
            f"讀取 Story {story_id} 失敗：{exc}"
        )
        return None


# =========================================================
# 候選合併
# =========================================================

def collect_candidates():
    """
    不使用主題式個人化。

    只使用 Hacker News 自己提供的：
    Best / Top / New / Show / Ask

    並保留 Story 在各 feed 的排名。
    """

    candidates = {}

    for feed_name in HN_FEEDS:

        try:
            ids = fetch_feed(feed_name)

        except Exception as exc:
            print(
                f"{feed_name} 取得失敗：{exc}"
            )
            continue

        for position, story_id in enumerate(
            ids,
            start=1
        ):

            if story_id not in candidates:
                candidates[story_id] = {
                    "id": story_id,
                    "feeds": {},
                }

            candidates[story_id]["feeds"][
                feed_name
            ] = position

    print(
        f"合併去重後候選："
        f"{len(candidates)} 則"
    )

    # 這裡不是主題評分。
    # 單純使用 HN 自己各排行榜的位置。
    def ranking(item):

        feed_positions = item[
            "feeds"
        ].values()

        best_position = min(
            feed_positions
        )

        feed_count = len(
            item["feeds"]
        )

        return (
            best_position,
            -feed_count,
        )

    return sorted(
        candidates.values(),
        key=ranking,
    )


# =========================================================
# 網域
# =========================================================

def get_domain(story):

    url = story.get("url")

    if not url:
        return "news.ycombinator.com"

    try:
        domain = urlparse(
            url
        ).netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return (
            domain
            or "unknown"
        )

    except Exception:
        return "unknown"


# =========================================================
# 文字清理
# =========================================================

def clean_hn_text(value):

    if not value:
        return ""

    value = html.unescape(
        value
    )

    value = re.sub(
        r"<p>",
        "\n\n",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"<[^>]+>",
        "",
        value,
    )

    value = re.sub(
        r"\n{3,}",
        "\n\n",
        value,
    )

    return value.strip()


def text_to_html(text):

    if not text:
        return ""

    paragraphs = re.split(
        r"\n\s*\n",
        text.strip(),
    )

    result = []

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        escaped = html.escape(
            paragraph
        )

        escaped = escaped.replace(
            "\n",
            "<br>"
        )

        result.append(
            f"<p>{escaped}</p>"
        )

    return "\n".join(
        result
    )


# =========================================================
# 抓外部文章全文
# =========================================================

def extract_full_article(url):

    if not url:
        return None

    try:

        raw_html = download_html(
            url
        )

        if not raw_html:
            return None

        article_text = trafilatura.extract(
            raw_html,
            include_comments=False,
            include_tables=True,
            include_links=False,
            include_images=False,
            favor_precision=True,
            deduplicate=True,
        )

        if not article_text:
            return None

        article_text = article_text.strip()

        # 太短的內容不視為「全文」
        if len(article_text) < 1000:
            return None

        return article_text

    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
    ) as exc:

        print(
            f"文章下載失敗 {url}：{exc}"
        )

        return None

    except Exception as exc:

        print(
            f"文章解析失敗 {url}：{exc}"
        )

        return None


# =========================================================
# 選本期文章
# =========================================================

def select_stories(candidate_meta):

    selected = []

    selected_ids = set()

    domain_counts = {}

    fulltext_count = 0

    article_attempts = 0

    # -----------------------------------------------------
    # 第一輪：
    # 優先建立 10 個不同情報項目，
    # 並同步嘗試取得全文
    # -----------------------------------------------------

    for meta in candidate_meta:

        if (
            len(selected) >= TARGET_STORIES
            and
            fulltext_count >= MIN_FULLTEXT
        ):
            break

        story = fetch_story(
            meta["id"]
        )

        if not story:
            continue

        story_id = story["id"]

        if story_id in selected_ids:
            continue

        domain = get_domain(
            story
        )

        # 同網站避免過度集中
        if (
            domain_counts.get(
                domain,
                0
            )
            >= MAX_PER_DOMAIN
        ):
            continue

        story["hn_feeds"] = meta[
            "feeds"
        ]

        story["domain"] = domain

        story[
            "full_article"
        ] = None

        url = story.get("url")

        # Ask HN 通常沒有外部網址
        if (
            url
            and
            article_attempts
            < MAX_ARTICLE_ATTEMPTS
        ):

            article_attempts += 1

            print(
                f"嘗試抓全文："
                f"{story.get('title', '')}"
            )

            full_article = (
                extract_full_article(
                    url
                )
            )

            if full_article:

                story[
                    "full_article"
                ] = full_article

                fulltext_count += 1

                print(
                    "  ✓ 全文成功 "
                    f"({len(full_article)} 字元)"
                )

            else:
                print(
                    "  × 未取得有效全文"
                )

            # 稍微禮貌一點，不高速打網站
            time.sleep(0.4)

        selected.append(
            story
        )

        selected_ids.add(
            story_id
        )

        domain_counts[
            domain
        ] = (
            domain_counts.get(
                domain,
                0
            )
            + 1
        )

    # -----------------------------------------------------
    # 如果已經 10 篇，但全文仍不足 3 篇，
    # 繼續尋找可取得全文的候選，
    # 並加入本期。
    #
    # 所以最終可能 > 10 篇。
    # -----------------------------------------------------

    if fulltext_count < MIN_FULLTEXT:

        print(
            f"目前只有 "
            f"{fulltext_count} 篇全文，"
            "繼續尋找..."
        )

        for meta in candidate_meta:

            if fulltext_count >= MIN_FULLTEXT:
                break

            if article_attempts >= MAX_ARTICLE_ATTEMPTS:
                break

            story_id = meta[
                "id"
            ]

            if story_id in selected_ids:
                continue

            story = fetch_story(
                story_id
            )

            if not story:
                continue

            url = story.get(
                "url"
            )

            if not url:
                continue

            domain = get_domain(
                story
            )

            if (
                domain_counts.get(
                    domain,
                    0
                )
                >= MAX_PER_DOMAIN
            ):
                continue

            article_attempts += 1

            print(
                f"補抓全文："
                f"{story.get('title', '')}"
            )

            full_article = (
                extract_full_article(
                    url
                )
            )

            if not full_article:
                time.sleep(0.4)
                continue

            story[
                "hn_feeds"
            ] = meta["feeds"]

            story[
                "domain"
            ] = domain

            story[
                "full_article"
            ] = full_article

            selected.append(
                story
            )

            selected_ids.add(
                story_id
            )

            domain_counts[
                domain
            ] = (
                domain_counts.get(
                    domain,
                    0
                )
                + 1
            )

            fulltext_count += 1

            print(
                "  ✓ 補抓成功"
            )

            time.sleep(0.4)

    print()
    print(
        f"本期共收錄："
        f"{len(selected)} 則"
    )

    print(
        f"其中全文："
        f"{fulltext_count} 篇"
    )

    if fulltext_count < MIN_FULLTEXT:

        print(
            "警告：受網站阻擋、付費牆或"
            "動態網頁影響，本次未達 "
            f"{MIN_FULLTEXT} 篇全文。"
        )

    return selected


# =========================================================
# HTML
# =========================================================

def build_story_html(
    story,
    number,
):

    title = html.escape(
        story.get(
            "title",
            "(No title)",
        )
    )

    score = story.get(
        "score",
        0
    )

    comments = story.get(
        "descendants",
        0
    )

    author = html.escape(
        story.get(
            "by",
            "unknown",
        )
    )

    domain = html.escape(
        story.get(
            "domain",
            "",
        )
    )

    story_id = story[
        "id"
    ]

    hn_url = (
        "https://news.ycombinator.com/"
        f"item?id={story_id}"
    )

    original_url = (
        story.get("url")
        or hn_url
    )

    original_url_escaped = (
        html.escape(
            original_url,
            quote=True,
        )
    )

    hn_url_escaped = html.escape(
        hn_url,
        quote=True,
    )

    feeds = []

    for feed_name, position in sorted(
        story.get(
            "hn_feeds",
            {}
        ).items()
    ):

        feeds.append(
            f"{html.escape(feed_name)} "
            f"#{position}"
        )

    feeds_text = " · ".join(
        feeds
    )

    # -----------------------------------------------------
    # 正文
    # -----------------------------------------------------

    full_article = story.get(
        "full_article"
    )

    if full_article:

        content_section = f"""
        <section class="article-content">

            <h3>文章全文</h3>

            <div class="fulltext">
                {text_to_html(full_article)}
            </div>

        </section>
        """

        fulltext_badge = (
            '<span class="badge full">'
            "FULL TEXT"
            "</span>"
        )

    else:

        hn_text = clean_hn_text(
            story.get(
                "text",
                ""
            )
        )

        if hn_text:

            content_section = f"""
            <section class="article-content">

                <h3>HN 原始內容</h3>

                {text_to_html(hn_text)}

            </section>
            """

        else:

            content_section = """
            <section class="article-content">

                <p class="notice">
                此來源未能自動擷取完整正文。
                可由下方 Original Article
                開啟原始網站。
                </p>

            </section>
            """

        fulltext_badge = (
            '<span class="badge reference">'
            "REFERENCE"
            "</span>"
        )

    return f"""
    <article class="story">

        <div class="story-number">
            {number:02d}
        </div>

        <h2>{title}</h2>

        <div class="badges">
            {fulltext_badge}
        </div>

        <div class="meta">

            <strong>{score}</strong> points
            ·
            <strong>{comments}</strong> comments
            ·
            by {author}

            <br>

            Source:
            <strong>{domain}</strong>

            <br>

            HN:
            {feeds_text}

        </div>

        {content_section}

        <div class="links">

            <a href="{original_url_escaped}">
                Original Article
            </a>

            ·

            <a href="{hn_url_escaped}">
                HN Discussion
            </a>

        </div>

    </article>
    """


def build_html(
    stories,
    generated_time,
    issue_name,
):

    fulltext_count = sum(
        1
        for story in stories
        if story.get(
            "full_article"
        )
    )

    story_blocks = []

    for index, story in enumerate(
        stories,
        start=1,
    ):

        story_blocks.append(
            build_story_html(
                story,
                index,
            )
        )

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>
HN Intelligence {html.escape(issue_name)}
</title>

<style>

body {{
    max-width: 760px;
    margin: 0 auto;
    padding: 32px 24px 80px 24px;

    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Arial,
        sans-serif;

    line-height: 1.7;

    background: #fff;
    color: #111;
}}

header {{
    padding-bottom: 32px;
    border-bottom: 2px solid #111;
    margin-bottom: 40px;
}}

h1 {{
    font-size: 2.2rem;
    line-height: 1.2;
    margin-bottom: 12px;
}}

.summary {{
    font-size: 1.05rem;
}}

.story {{
    padding-bottom: 56px;
    margin-bottom: 56px;
    border-bottom: 1px solid #999;
}}

.story-number {{
    font-size: 0.95rem;
    font-weight: bold;
    margin-bottom: 8px;
}}

h2 {{
    font-size: 1.55rem;
    line-height: 1.35;
    margin-top: 0;
}}

h3 {{
    font-size: 1.15rem;
    margin-top: 32px;
}}

.meta {{
    margin: 16px 0;
    font-size: 0.95rem;
    line-height: 1.6;
}}

.badges {{
    margin: 10px 0;
}}

.badge {{
    display: inline-block;
    border: 1px solid #111;
    padding: 3px 8px;
    font-size: 0.75rem;
    font-weight: bold;
}}

.article-content {{
    margin-top: 28px;
}}

.fulltext p {{
    margin: 1.1em 0;
    text-align: left;
}}

.notice {{
    border-left: 4px solid #555;
    padding-left: 14px;
}}

.links {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px dotted #999;
}}

a {{
    color: #000;
    text-decoration: underline;
}}

@media (max-width: 600px) {{

    body {{
        padding: 20px 16px 60px 16px;
    }}

    h1 {{
        font-size: 1.8rem;
    }}

    h2 {{
        font-size: 1.35rem;
    }}

}}

</style>

</head>

<body>

<header>

<h1>
HN Kobo Intelligence
</h1>

<div class="summary">

<strong>
{html.escape(issue_name)}
</strong>

<br>

Generated:
{html.escape(generated_time)}

<br><br>

HN Sources:
Best · Top · New · Show HN · Ask HN

<br>

Stories:
<strong>{len(stories)}</strong>

<br>

Full Articles:
<strong>{fulltext_count}</strong>

</div>

</header>

{''.join(story_blocks)}

</body>

</html>
"""


# =========================================================
# 永不覆蓋歷史檔案
# =========================================================

def get_unique_output_path(
    now,
):

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    period = (
        "AM"
        if now.hour < 12
        else "PM"
    )

    base_name = (
        f"{now:%Y-%m-%d}-{period}"
    )

    path = (
        OUTPUT_DIR
        / f"{base_name}.html"
    )

    if not path.exists():
        return path

    # 如果同一期手動重跑，
    # 不覆寫，而是 AM-2、AM-3...
    counter = 2

    while True:

        candidate = (
            OUTPUT_DIR
            / f"{base_name}-{counter}.html"
        )

        if not candidate.exists():
            return candidate

        counter += 1


# =========================================================
# Main
# =========================================================

def main():

    print(
        "================================"
    )

    print(
        "HN Kobo Intelligence"
    )

    print(
        "================================"
    )

    now = datetime.now(
        TAIPEI_TZ
    )

    print(
        "台灣時間：",
        now.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    )

    candidate_meta = (
        collect_candidates()
    )

    stories = select_stories(
        candidate_meta
    )

    if len(stories) < TARGET_STORIES:

        raise RuntimeError(
            "取得的有效情報不足 "
            f"{TARGET_STORIES} 則。"
        )

    output_path = (
        get_unique_output_path(
            now
        )
    )

    issue_name = (
        output_path.stem
    )

    generated_time = (
        now.strftime(
            "%Y-%m-%d %H:%M "
            "Asia/Taipei"
        )
    )

    document = build_html(
        stories,
        generated_time,
        issue_name,
    )

    # 使用 x 模式：
    # 就算程式邏輯出錯，
    # 也禁止覆寫現有檔案。
    with open(
        output_path,
        "x",
        encoding="utf-8",
    ) as file:

        file.write(
            document
        )

    print()
    print(
        "================================"
    )

    print(
        "完成：",
        output_path
    )

    print(
        "歷史檔案未覆寫。"
    )

    print(
        "================================"
    )


if __name__ == "__main__":
    main()
