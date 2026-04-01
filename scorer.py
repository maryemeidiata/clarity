import cohere
import json
import os
from dotenv import load_dotenv

load_dotenv()
client = cohere.ClientV2(os.getenv("COHERE_API_KEY"))

def score_posts(preference, posts):
    if not posts:
        return []
    
    posts_text = ""
    for i, post in enumerate(posts):
        posts_text += f"\n[POST {i}] by {post['author']}: {post['text'][:200]}\n"
    
    behaviour_line = f"\nUser behaviour history (secondary signal, weight 0.2): {behaviour_context}" if behaviour_context else ""

    prompt = f"""A user wants their social media feed to show: "{preference}"{behaviour_line}

The stated preference is the PRIMARY ranking signal (weight 0.8).
Behaviour history, if present, is secondary — use it to refine, not override.

Here are the posts to evaluate:
{posts_text}

Score each post. Reply with ONLY a JSON array. No markdown, no backticks, no extra text.
Each item must have: post_index, relevance (0-100), is_toxic (true/false), is_sponsored (true/false), is_ragebait (true/false), reason (one short sentence, max 12 words).

Example format:
[{{"post_index": 0, "relevance": 85, "is_toxic": false, "is_sponsored": false, "is_ragebait": false, "reason": "Matches interest in animals"}}]

Score all {len(posts)} posts:"""

    try:
        response = client.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}]
        )
        
        raw = response.message.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        results = json.loads(raw)
        
        for result in results:
            idx = result.get("post_index", -1)
            if 0 <= idx < len(posts):
                posts[idx]["relevance"] = result.get("relevance", 50)
                posts[idx]["is_toxic"] = result.get("is_toxic", False)
                posts[idx]["is_sponsored"] = result.get("is_sponsored", False)
                posts[idx]["is_ragebait"] = result.get("is_ragebait", False)
                posts[idx]["reason"] = result.get("reason", "")
    except Exception as e:
        print(f"[scorer] Batch scoring failed: {e}")
        for post in posts:
            post.setdefault("relevance", 50)
            post.setdefault("is_toxic", False)
            post.setdefault("is_sponsored", False)
            post.setdefault("is_ragebait", False)
            post.setdefault("reason", "Could not score this post.")
    
    for post in posts:
        if "relevance" not in post:
            post["relevance"] = 50
            post["is_toxic"] = False
            post["is_sponsored"] = False
            post["is_ragebait"] = False
            post["reason"] = "Not scored"
    
    return posts


if __name__ == "__main__":
    from feed import get_posts_from_subreddit

    posts = get_posts_from_subreddit("food", limit=5)
    print(f"Pulled {len(posts)} posts. Scoring in one batch...\n")

    scored = score_posts("I love baking and food culture. No sad content.", posts, "")

    for post in scored:
        print(f"[{post['relevance']}%] {post['author']}: {post['text'][:80]}")
        print(f"  → {post['reason']}")
        print()