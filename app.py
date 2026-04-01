from flask import Flask, render_template, request
from feed import get_posts_from_subreddit, validate_subreddit
from scorer import score_posts
import cohere
import os
from dotenv import load_dotenv
from db import init_db, log_interaction, get_interaction_context

load_dotenv()
co = cohere.ClientV2(os.getenv("COHERE_API_KEY"))

app = Flask(__name__)
init_db()

MOODS = {
    "relax": "Prioritize calming, wholesome, feel-good content. Avoid anything stressful, political, or negative.",
    "learn": "Prioritize educational, informative, and thought-provoking content. Favor depth over entertainment.",
    "laugh": "Prioritize funny, lighthearted, and entertaining content. Memes and humor welcome.",
    "explore": "Show me a diverse mix of interesting content. Surprise me with things I wouldn't normally see."
}

PRESETS = {
    "animals": "I love cute animals, pets, wildlife, and heartwarming animal stories",
    "foodie": "I want recipes, baking tips, cooking techniques, and food culture",
    "science": "I want science breakthroughs, space, technology, and research news",
    "creative": "I want art, design, photography, DIY projects, and creative inspiration",
    "fitness": "I want workout tips, healthy living, nutrition advice, and wellness content"
}

def extract_subreddits(preference: str) -> list[str]:
    prompt = f"""A user described what they want to see in their social media feed:
"{preference}"

Return 2-3 relevant subreddit names (just the name, no r/ prefix).
Reply ONLY with comma-separated names. Nothing else.
Example: MachineLearning, productivity, learnprogramming"""

    try:
        response = co.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.message.content[0].text.strip().split(",")
        candidates = [n.strip() for n in raw if n.strip()]
        return [n for n in candidates if validate_subreddit(n)]
    except Exception as e:
        print(f"[app] extract_subreddits failed: {e}")
        return ["technology"]  # safe fallback

@app.route("/", methods=["GET", "POST"])
def home():
    preference = ""
    scored_posts = []
    original_posts = []
    quality_score = 0
    mood = ""
    filtered_count = 0
    filters = {"toxic": True, "sponsored": True, "ragebait": True}
    
    if request.method == "POST":
        preference = request.form.get("preference", "")
        mood = request.form.get("mood", "")
        filters["toxic"] = request.form.get("filter_toxic") == "on"
        filters["sponsored"] = request.form.get("filter_sponsored") == "on"
        filters["ragebait"] = request.form.get("filter_ragebait") == "on"
        
        full_preference = preference
        if mood and mood in MOODS:
            full_preference += ". " + MOODS[mood]
        
        behaviour_context = get_interaction_context()
        subreddits = extract_subreddits(full_preference)

        all_posts = []
        for subreddit in subreddits:
            posts = get_posts_from_subreddit(subreddit, limit=15)
            all_posts.extend(posts)
        
        seen = set()
        unique_posts = []
        for p in all_posts:
            key = p["text"][:80]
            if key not in seen:
                seen.add(key)
                unique_posts.append(p)
        
        original_posts = [p.copy() for p in unique_posts]
        scored_posts = score_posts(full_preference, unique_posts, behaviour_context)
        
        for p in scored_posts:
            if filters["toxic"] and p.get("is_toxic"):
                p["hidden"] = True
                filtered_count += 1
            elif filters["sponsored"] and p.get("is_sponsored"):
                p["hidden"] = True
                filtered_count += 1
            elif filters["ragebait"] and p.get("is_ragebait"):
                p["hidden"] = True
                filtered_count += 1
            else:
                p["hidden"] = False
        
        scored_posts.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        
        visible = [p for p in scored_posts if not p["hidden"]]
        if visible:
            quality_score = round(sum(p["relevance"] for p in visible) / len(visible))
    
    return render_template(
        "index.html",
        preference=preference,
        scored_posts=scored_posts,
        original_posts=original_posts,
        quality_score=quality_score,
        filters=filters,
        mood=mood,
        filtered_count=filtered_count,
        moods=MOODS,
        presets=PRESETS
    )

if __name__ == "__main__":
    app.run(debug=True)