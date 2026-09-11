import json
import urllib.request
from datetime import datetime, timezone

HN_API = "https://hacker-news.firebaseio.com/v0"
OUTPUT_FILE = "hn-test.html"
STORY_COUNT = 10


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HN-Kobo-Intelligence/0.1"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_top_stories():
    story_ids = get_json(f"{HN_API}/topstories.json")

    stories = []

    for story_id in story_ids[:STORY_COUNT]:
        story = get_json(f"{HN_API}/item/{story_id}.json")

        if not story:
            continue

        stories.append({
            "id": story.get("id"),
            "title": story.get("title", "(No title)"),
            "url": story.get(
                "url",
                f"https://news.ycombinator.com/item?id={story_id}"
            ),
            "score": story.get("score", 0),
            "comments": story.get("descendants", 0),
        })

    return stories


def build_html(stories):
    generated = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )

    items = []

    for index, story in enumerate(stories, start=1):
        hn_url = (
            f"https://news.ycombinator.com/item?id={story['id']}"
        )

        items.append(
            f"""
            <article>
                <h2>{index}. {story['title']}</h2>

                <p>
                    {story['score']} points ·
                    {story['comments']} comments
                </p>

                <p>
                    <a href="{story['url']}">Original Article</a>
                    ·
                    <a href="{hn_url}">HN Discussion</a>
                </p>
            </article>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">

    <title>HN Kobo Intelligence</title>

    <style>
        body {{
            max-width: 760px;
            margin: 40px auto;
            padding: 0 20px;
            font-family: sans-serif;
            line-height: 1.6;
        }}

        article {{
            border-bottom: 1px solid #ccc;
            padding: 20px 0;
        }}

        h1 {{
            margin-bottom: 5px;
        }}

        h2 {{
            font-size: 1.2rem;
        }}
    </style>
</head>

<body>

<h1>HN Kobo Intelligence</h1>

<p>Generated: {generated}</p>

<p>
Hacker News Top {len(stories)} Stories
</p>

{''.join(items)}

</body>
</html>
"""


def main():
    print("Fetching Hacker News...")

    stories = fetch_top_stories()

    html = build_html(stories)

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(html)

    print(
        f"Done. Generated {OUTPUT_FILE} "
        f"with {len(stories)} stories."
    )


if __name__ == "__main__":
    main()
