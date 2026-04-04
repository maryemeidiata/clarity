import cohere
import json
import os
from dotenv import load_dotenv
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_vader = SentimentIntensityAnalyzer()

load_dotenv()
client = cohere.ClientV2(os.getenv("COHERE_API_KEY"))


def score_posts(preference, posts, behaviour_context=""):
    if not posts:
        return []

    posts_text = ""
    for i, post in enumerate(posts):
        posts_text += f"\n[POST {i}] by {post['author']} in {post['handle']}: {post['text'][:250]}\n"

    behaviour_line = (
        f"\nUser behaviour history (secondary signal, weight 0.2): {behaviour_context}"
        if behaviour_context else ""
    )

    prompt = f"""You are scoring social media posts for a personalised feed re-ranking system.

User preference: "{preference}"{behaviour_line}

SCORING SCALE — use the FULL range. Do NOT cluster around 50:
90-100 : Perfect match. Directly on-topic AND high quality/depth.
70-89  : Strong match. Clearly relevant, good content.
45-69  : Partial match. Loosely related or only tangentially relevant.
20-44  : Weak match. Off-topic or low quality for this user.
0-19   : No match, or actively bad (toxic, rage-bait, spam).

CRITICAL RULES:
- You MUST produce a SPREAD of scores. If posts vary in relevance, scores must reflect that.
- A generic on-topic post scores 55-65. A deep, specific, high-quality match scores 80+.
- An off-topic post from a broad subreddit scores below 40 even if the subreddit name is related.
- is_toxic: true only for genuinely hateful, harassing or harmful content.
- is_sponsored: true for obvious ads or paid promotions.
- is_ragebait: true for deliberate outrage-farming, inflammatory clickbait.

Posts to score:
{posts_text}

Reply with ONLY a JSON array. No markdown, no backticks, no extra text.
Each item: post_index (int), relevance (int 0-100), is_toxic (bool), is_sponsored (bool), is_ragebait (bool), reason (string, max 12 words explaining the score).

Example:
[{{"post_index": 0, "relevance": 87, "is_toxic": false, "is_sponsored": false, "is_ragebait": false, "reason": "Directly matches ML interest with strong research depth"}}]

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

    # Ensure every post has all required fields
    for post in posts:
        if "relevance" not in post:
            post["relevance"] = 50
            post["is_toxic"] = False
            post["is_sponsored"] = False
            post["is_ragebait"] = False
            post["reason"] = "Not scored"

    # VADER sentiment scoring — runs locally, no API call
    for post in posts:
        score = _vader.polarity_scores(post.get("text", ""))["compound"]
        post["sentiment_score"] = round(score, 3)
        if score >= 0.05:
            post["tone"] = "positive"
        elif score <= -0.05:
            post["tone"] = "negative"
        else:
            post["tone"] = "neutral"

    return posts


def generate_filter_chips(preference: str) -> list[str]:
    if not preference or len(preference.strip()) < 5:
        return []

    prompt = f"""A user wants to browse social media about: "{preference}"

Suggest 5-6 short filter labels (2-3 words each) that would help them narrow down the content.
These should be specific sub-categories or angles within their interest.

Reply with ONLY a JSON array of strings. No markdown, no backticks.
Example: ["Funny moments", "News updates", "Deep analysis", "Fan theories", "Behind the scenes"]"""

    try:
        response = client.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.message.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[scorer] Filter chips failed: {e}")
        return []


if __name__ == "__main__":
    from feed import get_posts_from_subreddit

    posts = get_posts_from_subreddit("food", limit=5)
    print(f"Pulled {len(posts)} posts. Scoring...\n")

    scored = score_posts("I love baking and food culture. No sad content.", posts, "")

    for post in scored:
        print(f"[{post['relevance']}%] {post['author']}: {post['text'][:80]}")
        print(f"  -> {post['reason']}")
        print()

    print("\nFilter chips:")
    chips = generate_filter_chips("baking and food culture")
    print(chips)