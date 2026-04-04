dddfrom flask import Flask, render_template, request, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed
from feed import get_posts_from_subreddit, validate_subreddit, deduplicate_posts, search_reddit
from scorer import score_posts, generate_filter_chips
from cache import get_cached, set_cached
import cohere
import os
import json
import requests
from dotenv import load_dotenv
from db import init_db, log_interaction, get_interaction_context, log_session, get_analytics

load_dotenv()
co = cohere.ClientV2(os.getenv("COHERE_API_KEY"))

app = Flask(__name__)
init_db()

PRESETS = {
    "animals": "I love cute animals, pets, wildlife, and heartwarming animal stories",
    "foodie": "I want recipes, baking tips, cooking techniques, and food culture",
    "science": "I want science breakthroughs, space, technology, and research news",
    "creative": "I want art, design, photography, DIY projects, and creative inspiration",
    "fitness": "I want workout tips, healthy living, nutrition advice, and wellness content"
}

PERSONAS = {
    "learner": {
        "label": "Learner",
        "icon": "book-open",
        "description": "Learn something specific",
        "preference": "I want to learn and understand things in depth. Prioritise educational, well-explained, evidence-based content on my chosen topic. Favour accuracy and depth over entertainment.",
        "sort": "top",
        "time_filter": "month",
    },
    "explorer": {
        "label": "Explorer",
        "icon": "compass",
        "description": "Discover new perspectives",
        "preference": "I want to discover things I wouldn't normally find. Prioritise novel, diverse, and unexpected content. Broaden my perspective beyond what I already know.",
        "sort": "top",
        "time_filter": "week",
    },
    "recharger": {
        "label": "Recharger",
        "icon": "battery-charging",
        "description": "Unwind intentionally",
        "preference": "I want low-effort, pleasant content for downtime. Nothing stressful, heavy, or that requires concentration. Content should feel calm, light, and restorative.",
        "sort": "hot",
        "time_filter": "day",
    },
    "tracker": {
        "label": "Tracker",
        "icon": "radio",
        "description": "Stay current and connected",
        "preference": "I want to stay up to date on what is happening. Prioritise recent, relevant content from my communities and topics. Include both news and community conversation.",
        "sort": "new",
        "time_filter": "day",
    },
}

TONES = {
    "funny":      {"label": "Funny",      "icon": "smile"},
    "inspiring":  {"label": "Inspiring",  "icon": "star"},
    "optimistic": {"label": "Optimistic", "icon": "sun"},
    "analytical": {"label": "Analytical", "icon": "bar-chart-2"},
    "calming":    {"label": "Calming",    "icon": "wind"},
}


def search_for_subreddits(preference: str, limit: int = 3) -> list[str]:
    """Search Reddit's subreddit index for subreddits matching the keyword."""
    raw = preference.split(".")[0]
    raw = raw.replace("PRIMARY TOPIC:", "").strip()

    stop_words = {
        "i", "want", "to", "a", "an", "the", "and", "or", "for", "of",
        "in", "on", "about", "with", "my", "me", "show", "find", "get",
        "is", "are", "was", "be", "some", "more", "less", "all", "any"
    }
    words = [w.strip(".,!?") for w in raw.split() if w.lower().strip(".,!?") not in stop_words and len(w) > 2]
    keyword = " ".join(words[:3]) if words else raw

    try:
        response = requests.get(
            "https://www.reddit.com/subreddits/search.json",
            headers={"User-Agent": "Clarity/1.0"},
            params={"q": keyword, "limit": limit, "include_over_18": "off"},
            timeout=5
        )
        if response.status_code != 200:
            print(f"[app] Subreddit search error {response.status_code} for '{keyword}'")
            return []
        children = response.json().get("data", {}).get("children", [])
        return [c["data"]["display_name"] for c in children if c["data"].get("subreddit_type") != "private"]
    except Exception as e:
        print(f"[app] search_for_subreddits failed: {e}")
        return []


def extract_subreddits(preference: str) -> list[str]:
    prompt = f"""A user described what they want to see in their social media feed:
"{preference}"

Return exactly 3 subreddit names (no r/ prefix):
- 2 subreddits that closely match the preference topic
- 1 broader or adjacent subreddit that is related but not perfectly aligned

The contrast between specific and broader subreddits is important for re-ranking quality.
Reply ONLY with comma-separated names. Nothing else.
Example for "machine learning research": MachineLearning, deeplearning, technology"""

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            llm_future = executor.submit(
                lambda: co.chat(
                    model="command-a-03-2025",
                    messages=[{"role": "user", "content": prompt}]
                )
            )
            reddit_future = executor.submit(search_for_subreddits, preference)

            llm_response = llm_future.result()
            reddit_candidates = reddit_future.result()

        raw = llm_response.message.content[0].text.strip().split(",")
        llm_candidates = [n.strip() for n in raw if n.strip()]

        # Reddit search results take priority, LLM fills the rest
        all_candidates = list(dict.fromkeys(reddit_candidates + llm_candidates))

        # Validate LLM candidates only (Reddit search results already confirmed to exist)
        llm_only = [n for n in all_candidates if n not in reddit_candidates]
        with ThreadPoolExecutor(max_workers=max(len(llm_only), 1)) as executor:
            results = list(executor.map(validate_subreddit, llm_only))
        valid_llm = [n for n, ok in zip(llm_only, results) if ok]

        valid = list(dict.fromkeys(reddit_candidates + valid_llm))
        return valid if valid else llm_candidates[:3]
    except Exception as e:
        print(f"[app] extract_subreddits failed: {e}")
        return []


def generate_transparency_report(
    preference: str,
    scored_posts: list,
    behaviour_context: str,
    subreddits: list[str]
) -> dict:
    if not scored_posts:
        return {}

    visible = [p for p in scored_posts if not p.get("hidden")]
    hidden = [p for p in scored_posts if p.get("hidden")]

    buckets = {"high": 0, "mid": 0, "low": 0, "unscored": 0}
    for p in visible:
        r = p.get("relevance", 50)
        reason = p.get("reason", "")
        if reason in ("Not scored", "Could not score this post.", "Scoring unavailable"):
            buckets["unscored"] += 1
        elif r >= 70:
            buckets["high"] += 1
        elif r >= 45:
            buckets["mid"] += 1
        else:
            buckets["low"] += 1

    subreddit_scores = {}
    for p in visible:
        handle = p.get("handle", "unknown")
        if handle not in subreddit_scores:
            subreddit_scores[handle] = []
        subreddit_scores[handle].append(p.get("relevance", 50))
    subreddit_avg = {
        k: round(sum(v) / len(v)) for k, v in subreddit_scores.items()
    }

    filter_breakdown = {"toxic": 0, "sponsored": 0, "ragebait": 0}
    for p in hidden:
        if p.get("is_toxic"):
            filter_breakdown["toxic"] += 1
        if p.get("is_sponsored"):
            filter_breakdown["sponsored"] += 1
        if p.get("is_ragebait"):
            filter_breakdown["ragebait"] += 1

    avg_score = round(
        sum(p.get("relevance", 0) for p in visible) / len(visible)
    ) if visible else 0

    prompt = f"""A user set this preference for their social media feed:
"{preference}"

The algorithm searched: {', '.join(subreddits)}.
Scored {len(scored_posts)} posts total. Average relevance of visible posts: {avg_score}/100.
Score distribution: {buckets['high']} posts scored 70+, {buckets['mid']} scored 45-69, {buckets['low']} scored below 45.
{len(hidden)} posts filtered ({filter_breakdown['toxic']} toxic, {filter_breakdown['sponsored']} sponsored, {filter_breakdown['ragebait']} rage-bait).
{f'Behaviour history used: {behaviour_context}' if behaviour_context else 'No behaviour history — scored on stated preference only.'}

Write a transparency report in 3 short sections:
1. "How I understood your preference" — one sentence explaining what the algorithm focused on
2. "What I prioritised" — two to three bullet points, each max 10 words
3. "What I filtered out" — one sentence summarising what was removed and why

Reply in JSON with keys: understood (string), prioritised (array of strings), filtered_out (string).
No markdown, no backticks."""

    try:
        response = co.chat(
            model="command-a-03-2025",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.message.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        report = json.loads(raw)
        report["buckets"] = buckets
        report["subreddit_avg"] = subreddit_avg
        report["filter_breakdown"] = filter_breakdown
        report["avg_score"] = avg_score
        report["behaviour_used"] = bool(behaviour_context)
        report["interaction_count"] = len(behaviour_context.split(";")) if behaviour_context else 0
        report["subreddits"] = subreddits
        return report
    except Exception as e:
        print(f"[transparency] Failed: {e}")
        return {
            "understood": f"Focused on: {preference[:80]}",
            "prioritised": ["Posts matching your stated topic", "Clean, non-toxic content"],
            "filtered_out": f"{len(hidden)} posts removed by your filters.",
            "buckets": buckets,
            "subreddit_avg": subreddit_avg,
            "filter_breakdown": filter_breakdown,
            "avg_score": avg_score,
            "behaviour_used": bool(behaviour_context),
            "interaction_count": 0,
            "subreddits": subreddits,
        }


@app.route("/", methods=["GET", "POST"])
def home():
    transparency = {}
    preference = ""
    scored_posts = []
    original_posts = []
    persona_key = ""
    selected_tones = []
    filtered_count = 0
    filter_chips = []
    active_chips = []
    subreddits_used = []
    tone_breakdown = {}
    tone_warning = False
    filters = {"toxic": True, "sponsored": True, "ragebait": True}

    if request.method == "POST":
        preference = request.form.get("preference", "")
        persona_key = request.form.get("persona", "")
        selected_tones = request.form.getlist("tones")
        filters["toxic"] = request.form.get("filter_toxic") == "on"
        filters["sponsored"] = request.form.get("filter_sponsored") == "on"
        filters["ragebait"] = request.form.get("filter_ragebait") == "on"
        active_chips = request.form.getlist("active_chips")
        force_refresh = request.form.get("force_refresh") == "1"

        persona = PERSONAS.get(persona_key, {})
        sort_method = persona.get("sort", "top")
        time_filter = persona.get("time_filter", "week")

        # Build full_preference — search text is explicitly PRIMARY
        parts = []
        if preference.strip():
            parts.append(f"PRIMARY TOPIC: {preference.strip()}.")
        if persona:
            parts.append(f"User intent context: {persona['preference']}")
        if selected_tones:
            tone_labels = [TONES[t]["label"] for t in selected_tones if t in TONES]
            parts.append(f"Desired tone: {', '.join(tone_labels)}.")
        if active_chips:
            parts.append(f"Focus specifically on: {', '.join(active_chips)}.")

        full_preference = " ".join(parts) if parts else "Show me interesting content"

        behaviour_context = get_interaction_context()
        subreddits_used = extract_subreddits(full_preference)

        if not subreddits_used:
            print("[app] No valid subreddits found — will rely on search fallback")

        cached = None if force_refresh else get_cached(full_preference, subreddits_used)

        if cached:
            print(f"[cache] HIT for: {full_preference[:60]}")
            scored_posts = [p.copy() for p in cached]
            original_posts = [p.copy() for p in cached]
        else:
            print(f"[cache] MISS — fetching from Reddit")
            all_posts = []

            reddit_matched_subs = set(search_for_subreddits(full_preference))

            def fetch_subreddit(sub):
                min_ups = 0 if sub in reddit_matched_subs else -1
                return get_posts_from_subreddit(
                    sub, limit=20, sort=sort_method, time_filter=time_filter, min_upvotes=min_ups
                )

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {}
                for sub in subreddits_used:
                    futures[executor.submit(fetch_subreddit, sub)] = sub
                if preference.strip():
                    futures[executor.submit(search_reddit, preference.strip(), 10)] = "search"

                for future in as_completed(futures):
                    try:
                        all_posts.extend(future.result())
                    except Exception as e:
                        print(f"[fetch] Error: {e}")

            # Fallback: if subreddits returned nothing, use search only
            if not all_posts and preference.strip():
                print("[app] No posts from subreddits — using search-only fallback")
                all_posts = search_reddit(preference.strip(), 15)

            unique_posts = deduplicate_posts(all_posts)
            original_posts = [p.copy() for p in unique_posts]
            scored_posts = score_posts(full_preference, unique_posts, behaviour_context)
            # Store clean copy before filter mutation
            set_cached(full_preference, subreddits_used, [p.copy() for p in scored_posts])

        transparency = generate_transparency_report(
            full_preference, scored_posts, behaviour_context, subreddits_used
        )
        filter_chips = generate_filter_chips(preference)

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

        # Tone breakdown across visible posts
        tone_counts = {"positive": 0, "neutral": 0, "negative": 0}
        for p in visible:
            tone_counts[p.get("tone", "neutral")] += 1
        total_toned = len(visible) or 1
        tone_breakdown = {
            "positive": round(tone_counts["positive"] / total_toned * 100),
            "neutral": round(tone_counts["neutral"] / total_toned * 100),
            "negative": round(tone_counts["negative"] / total_toned * 100),
        }
        tone_warning = tone_breakdown["negative"] > 40
        avg_tone = round(
            sum(p.get("sentiment_score", 0) for p in visible) / total_toned, 3
        )

        try:
            log_session("web", preference, persona_key, 0,
                        len(scored_posts), filtered_count, avg_tone)
        except Exception:
            pass

    return render_template(
        "index.html",
        preference=preference,
        scored_posts=scored_posts,
        original_posts=original_posts,
        filters=filters,
        persona_key=persona_key,
        selected_tones=selected_tones,
        filtered_count=filtered_count,
        presets=PRESETS,
        personas=PERSONAS,
        tones=TONES,
        transparency=transparency,
        filter_chips=filter_chips,
        active_chips=active_chips,
        subreddits_used=subreddits_used,
        tone_breakdown=tone_breakdown if scored_posts else {},
        tone_warning=tone_warning if scored_posts else False,
    )



@app.route("/interact", methods=["POST"])
def interact():
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


@app.route("/generate_chips", methods=["POST"])
def generate_chips():
    data = request.get_json(silent=True)
    preference = data.get("preference", "") if data else ""
    if not preference.strip():
        return jsonify({"chips": []})
    chips = generate_filter_chips(preference)
    return jsonify({"chips": chips})


@app.route("/wrapped")
def wrapped_page():
    analytics = get_analytics()
    wrapped = {
        "total_sessions": analytics["total_sessions"],
        "total_filtered": analytics["total_filtered"],
        "thumbs_up": analytics["thumbs_up"],
        "thumbs_down": analytics["thumbs_down"],
        "topic_tone": analytics["topic_tone"],
        "mood_timeline": analytics["mood_timeline"],
        "healthiest_topic": analytics["healthiest_topic"],
        "blind_spot": analytics["blind_spot"],
    }
    conn = __import__('db').get_connection()
    rows = conn.execute(
        "SELECT preference FROM sessions WHERE preference IS NOT NULL AND preference != '' ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    conn.close()
    all_prefs = " ".join([r["preference"] for r in rows]) if rows else ""
    if all_prefs:
        try:
            response = co.chat(
                model="command-a-03-2025",
                messages=[{"role": "user", "content": f"""Analyze these user feed preferences and extract their content DNA profile.
Preferences: "{all_prefs[:500]}"

Reply with ONLY a JSON object. No markdown, no backticks.
{{"top_topics": ["topic1", "topic2", "topic3", "topic4", "topic5"], "percentages": [35, 25, 20, 12, 8], "personality": "A one-sentence description of this user's content personality", "fun_fact": "A fun observation about their browsing habits"}}"""}]
            )
            raw = response.message.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            wrapped["dna"] = json.loads(raw)
        except Exception as e:
            print(f"[wrapped] DNA failed: {e}")
            wrapped["dna"] = {"top_topics": ["General"], "percentages": [100], "personality": "Still building your profile!", "fun_fact": "Use Clarity more to unlock your content DNA."}
    else:
        wrapped["dna"] = {"top_topics": ["No data yet"], "percentages": [100], "personality": "Your content DNA is waiting to be discovered.", "fun_fact": "Start a session to begin building your profile."}
    return render_template("wrapped.html", wrapped=wrapped)


if __name__ == "__main__":
    app.run(debug=True)