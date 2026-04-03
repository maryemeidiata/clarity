from flask import Flask, render_template, request
from feed import get_posts_from_subreddit, validate_subreddit
from scorer import score_posts
import cohere
import os
from dotenv import load_dotenv
from db import init_db, log_interaction, get_interaction_context
import json

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

PERSONAS = {
    "learner": {
        "label": "The Learner",
        "emoji": "🧠",
        "description": "Deep dives, research, explainers",
        "preference": "I want educational, in-depth content. Long reads, research findings, how-things-work explainers. No clickbait or shallow takes."
    },
    "optimist": {
        "label": "The Optimist",
        "emoji": "🌱",
        "description": "Progress, kindness, good news",
        "preference": "I want positive, uplifting content. Stories of progress, kindness, creativity, and human achievement. Nothing toxic or depressing."
    },
    "analyst": {
        "label": "The Analyst",
        "emoji": "📊",
        "description": "Data, strategy, critical thinking",
        "preference": "I want data-driven, evidence-based content. Strategic analysis, systems thinking, well-reasoned arguments. Skip the hot takes."
    },
    "explorer": {
        "label": "The Explorer",
        "emoji": "🔭",
        "description": "Niche, surprising, diverse",
        "preference": "I want surprising, niche, and diverse content. Things I wouldn't normally encounter. Broaden my perspective."
    },
    "minimalist": {
        "label": "The Minimalist",
        "emoji": "⚡",
        "description": "Signal-dense, no noise",
        "preference": "I want concise, high-signal content only. No filler, no drama, no noise. Maximum substance per word."
    },
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
        return []  # safe fallback

def generate_transparency_report(preference: str, scored_posts: list, behaviour_context: str) -> dict:
    if not scored_posts:
        return {}

    visible = [p for p in scored_posts if not p.get("hidden")]
    hidden = [p for p in scored_posts if p.get("hidden")]
    avg_score = round(sum(p.get("relevance", 0) for p in visible) / len(visible)) if visible else 0
    top_posts = sorted(visible, key=lambda x: x.get("relevance", 0), reverse=True)[:3]
    top_handles = list(dict.fromkeys(p["handle"] for p in top_posts))

    prompt = f"""A user set this preference for their social media feed:
"{preference}"

The algorithm scored {len(scored_posts)} posts. Top scores went to posts from: {', '.join(top_handles)}.
Average relevance of visible posts: {avg_score}/100.
{len(hidden)} posts were filtered out as toxic, sponsored, or rage-bait.
{f'User behaviour context: {behaviour_context}' if behaviour_context else 'No behaviour history yet.'}

Write a transparency report for the user in 3 short sections:
1. "How I understood your preference" - one sentence
2. "What I prioritised" - two to three bullet points, each max 10 words
3. "What I filtered out" - one sentence

Reply in JSON with keys: understood, prioritised (array of strings), filtered_out.
No markdown, no backticks."""

    try:
        response = co.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.message.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[transparency] Failed: {e}")
        return {
            "understood": f"You want: {preference[:80]}",
            "prioritised": ["Posts matching your stated topics", "Clean, non-toxic content"],
            "filtered_out": f"{len(hidden)} posts removed by your filters."
        }

@app.route("/", methods=["GET", "POST"])
def home():
    transparency = {}
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
        transparency = generate_transparency_report(full_preference, scored_posts, behaviour_context)

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
        presets=PRESETS,
        personas=PERSONAS,
        transparency=transparency,
    )

@app.route("/interact", methods=["POST"])
def interact():
    from flask import jsonify
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error"}), 400
    log_interaction(
        session_id=data.get("session_id", "anonymous"),
        post_id=data.get("post_id", ""),
        post_text=data.get("post_text", ""),
        signal=data.get("signal", ""),
        weight=float(data.get("weight", 0))
    )
    return jsonify({"status": "ok"})

@app.route("/refine", methods=["POST"])
def refine():
    from flask import jsonify
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data"}), 400

    current_preference = data.get("current_preference", "")
    user_message = data.get("message", "")

    prompt = f"""A user has this current feed preference:
"{current_preference}"

They want to change it. Their feedback:
"{user_message}"

Rewrite the preference to incorporate their feedback. Keep what they liked, adjust what they didn't.
Reply with ONLY the new preference string. No explanation, no quotes, no preamble."""

    try:
        response = co.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}]
        )
        new_preference = response.message.content[0].text.strip()
        return jsonify({"new_preference": new_preference})
    except Exception as e:
        print(f"[refine] Failed: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)