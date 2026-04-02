import requests


def get_posts_from_subreddit(subreddit: str, limit: int = 25) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    headers = {"User-Agent": "Clarity/1.0"}
    params = {"limit": limit}

    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        print(f"[feed] Error {response.status_code} for r/{subreddit}")
        return []

    posts = []
    for item in response.json()["data"]["children"]:
        p = item["data"]
        if p.get("ups", 0) < 10:
            continue
        text = p.get("title", "") + ". " + p.get("selftext", "")[:200]
        if not text.strip():
            continue
        # Extract image if available
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
            "likes": p.get("ups", 0),
            "comments": p.get("num_comments", 0),
            "time": p.get("created_utc", 0),
            "url": "https://reddit.com" + p.get("permalink", ""),
            "source": "reddit",
            "image_url": image_url,
        })
    return posts


def validate_subreddit(name: str) -> bool:
    """Returns True if subreddit exists and is public."""
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
    # Test: fetch 5 posts from r/technology/hot
    posts = get_posts_from_subreddit("technology", limit=5)
    if posts:
        for i, post in enumerate(posts):
            print(f"\n--- Post {i+1} ---")
            print(f"Author: {post['author']} ({post['handle']})")
            print(f"Text: {post['text'][:150]}")
            print(f"Likes: {post['likes']}")
            print(f"URL: {post['url']}")
    else:
        print("No posts returned.") 