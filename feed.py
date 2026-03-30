import requests

def get_posts(query="cats", limit=30):
    url = f"https://www.reddit.com/search.json"
    params = {
        "q": query,
        "limit": limit,
        "sort": "relevance",
        "t": "week"
    }
    headers = {
        "User-Agent": "Clarity/1.0"
    }
    
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code != 200:
        print(f"API error: {response.status_code}")
        print(response.text[:300])
        return []
    
    data = response.json()
    
    posts = []
    for item in data["data"]["children"]:
        p = item["data"]
        post = {
            "author": p.get("author", "Unknown"),
            "handle": "r/" + p.get("subreddit", ""),
            "text": p.get("title", "") + ". " + p.get("selftext", "")[:200],
            "likes": p.get("ups", 0),
            "comments": p.get("num_comments", 0),
            "time": p.get("created_utc", 0),
        }
        if post["text"].strip():
            posts.append(post)
    
    return posts


if __name__ == "__main__":
    posts = get_posts("cats", limit=5)
    if posts:
        for i, post in enumerate(posts):
            print(f"\n--- Post {i+1} ---")
            print(f"Author: {post['author']} ({post['handle']})")
            print(f"Text: {post['text'][:150]}")
            print(f"Likes: {post['likes']}")
    else:
        print("No posts returned. Check the error above.")
