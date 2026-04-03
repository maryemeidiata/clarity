import requests
import time

def get_posts_from_subreddit(subreddit: str, limit: int = 25, sort: str = "top", time_filter: str = "week") -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    headers = {"User-Agent": "Clarity/1.0"}
    params = {"limit": limit, "t": time_filter}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
    except requests.exceptions.RequestException as e:
        print(f"[feed] Request failed for r/{subreddit}: {e}")
        return []

    if response.status_code != 200:
        print(f"[feed] Error {response.status_code} for r/{subreddit}")
        return []

    posts = []
    now = time.time()
    for item in response.json().get("data", {}).get("children", []):
        p = item["data"]
        ups = p.get("ups", 0)
        if ups < 50:
            continue

        text = p.get("title", "") + ". " + p.get("selftext", "")[:300]
        if not text.strip() or len(text.strip()) < 20:
            continue

        created = p.get("created_utc", now)
        hours_old = max((now - created) / 3600, 1)
        engagement_rate = round(ups / hours_old, 1)

        image_url = None
        post_hint = p.get("post_hint", "")
        if post_hint == "image":
            image_url = p.get("url", None)
        elif p.get("preview"):
            try:
                image_url = p["preview"]["images"][0]["source"]["url"].replace("&amp;", "&")
            except (KeyError, IndexError):
                image_url = None

        posts.append({
            "id": p["id"],
            "author": p.get("author", "unknown"),
            "handle": "r/" + p.get("subreddit", subreddit),
            "text": text,
            "likes": ups,
            "comments": p.get("num_comments", 0),
            "time": created,
            "url": "https://reddit.com" + p.get("permalink", ""),
            "source": "reddit",
            "image_url": image_url,
            "engagement_rate": engagement_rate,
        })

    posts.sort(key=lambda x: x["engagement_rate"], reverse=True)
    return posts


def deduplicate_posts(posts: list[dict], threshold: float = 0.7) -> list[dict]:
    unique = []
    seen_titles = []

    for post in posts:
        title = post["text"][:80].lower().strip()
        is_dup = False
        for seen in seen_titles:
            overlap = len(set(title.split()) & set(seen.split()))
            max_len = max(len(title.split()), len(seen.split()), 1)
            if overlap / max_len > threshold:
                is_dup = True
                break
        if not is_dup:
            unique.append(post)
            seen_titles.append(title)

    return unique


def validate_subreddit(name: str) -> bool:
    try:
        r = requests.get(
            f"https://www.reddit.com/r/{name}/about.json",
            headers={"User-Agent": "Clarity/1.0"},
            timeout=3
        )
        data = r.json()
        return (
            r.status_code == 200
            and data.get("data", {}).get("subreddit_type") != "private"
        )
    except Exception:
        return False


if __name__ == "__main__":
    posts = get_posts_from_subreddit("technology", limit=5)
    posts = deduplicate_posts(posts)
    if posts:
        for i, post in enumerate(posts):
            print(f"\n--- Post {i+1} ---")
            print(f"Author: {post['author']} ({post['handle']})")
            print(f"Text: {post['text'][:150]}")
            print(f"Likes: {post['likes']} | Engagement: {post['engagement_rate']}/hr")
    else:
        print("No posts returned.")
