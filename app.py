from flask import Flask, render_template, request, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed
from feed import get_posts_from_subreddit, validate_subreddit, deduplicate_posts, search_reddit
from scorer import score_posts, generate_filter_chips
from cache import get_cached, set_cached, clear_cache
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


PERSONAS = {
    "learner": {
        "label": "Learner",
        "icon": "book-open",
        "description": "Learn something specific",
        "preference": "I want to learn and understand things in depth. Prioritise educational, well-explained, evidence-based content on my chosen topic. Favour accuracy and depth over entertainment.",
        "quality_baseline": "Favour posts with substance: real explanations, cited sources, or genuine expertise. Penalise shallow takes, listicles with no depth, and engagement-bait titles.",
        "sort": "top",
        "time_filter": "month",
    },
    "explorer": {
        "label": "Explorer",
        "icon": "compass",
        "description": "Discover new perspectives",
        "preference": "I want to discover things I wouldn't normally find. Prioritise novel, diverse, and unexpected content. Broaden my perspective beyond what I already know.",
        "quality_baseline": "Favour posts that offer a genuine perspective or angle not commonly seen. Penalise generic takes, reposted content, and posts that exist only for upvotes.",
        "sort": "top",
        "time_filter": "week",
    },
    "recharger": {
        "label": "Recharger",
        "icon": "battery-charging",
        "description": "Unwind intentionally",
        "preference": "I want low-effort, pleasant content for downtime. Nothing stressful, heavy, or that requires concentration. Content should feel calm, light, and restorative.",
        "quality_baseline": "Favour content that is genuinely warm, funny, or calming. Penalise anything emotionally charged, controversial, alarming, or that induces anxiety.",
        "sort": "hot",
        "time_filter": "day",
    },
    "tracker": {
        "label": "Tracker",
        "icon": "radio",
        "description": "Stay current and connected",
        "preference": "I want to stay up to date on what is happening. Prioritise recent, relevant content from my communities and topics. Include both news and community conversation.",
        "quality_baseline": "Favour posts with verifiable information, credible sources, or meaningful community discussion. Penalise rumour, speculation presented as fact, and low-effort news reposts.",
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


def extract_subreddits(preference: str) -> tuple[list[str], set[str]]:
    """
    Discover subreddits for the given preference using Reddit search + LLM in parallel.

    Returns:
        Tuple of (final validated subreddit list, set of reddit-search confirmed names).
        The second value is passed to the fetch layer so it can set min_upvotes=0
        for directly-matched communities — without calling search_for_subreddits again.
    """
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
        reddit_set = set(reddit_candidates)
        return (valid if valid else llm_candidates[:3]), reddit_set
    except Exception as e:
        print(f"[app] extract_subreddits failed: {e}")
        return [], set()



def _build_transparency_report(
    scored_posts: list,
    behaviour_context: str,
    subreddits: list[str],
    search_term: str,
    persona_key: str,
    sort_method: str,
    time_filter: str,
) -> dict:
    """
    Build the full transparency report from deterministic data — no LLM call.

    The 'understood' sentence is constructed from real pipeline variables:
    post count, match rate, search term, persona sort window. Every word
    is factually true and tells the user something they didn't already know.

    Args:
        scored_posts: All posts after scoring (visible + hidden).
        behaviour_context: Serialised past interaction summary.
        subreddits: Subreddit names that were fetched.
        search_term: Raw user search input (e.g. "ESADE").
        persona_key: Active persona key (e.g. "learner").
        sort_method: Reddit sort used — "top", "hot", "new".
        time_filter: Reddit time window — "day", "week", "month".

    Returns:
        Complete transparency dict ready for the template.
    """
    if not scored_posts:
        return {}

    visible = [p for p in scored_posts if not p.get("hidden")]
    hidden = [p for p in scored_posts if p.get("hidden")]

    # Score distribution buckets
    buckets: dict[str, int] = {"high": 0, "mid": 0, "low": 0, "unscored": 0}
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

    # Per-subreddit average relevance
    subreddit_scores: dict[str, list[int]] = {}
    for p in visible:
        handle = p.get("handle", "unknown")
        subreddit_scores.setdefault(handle, []).append(p.get("relevance", 50))
    subreddit_avg = {k: round(sum(v) / len(v)) for k, v in subreddit_scores.items()}

    # Filter breakdown
    filter_breakdown: dict[str, int] = {"toxic": 0, "sponsored": 0, "ragebait": 0}
    for p in hidden:
        if p.get("is_toxic"):
            filter_breakdown["toxic"] += 1
        if p.get("is_sponsored"):
            filter_breakdown["sponsored"] += 1
        if p.get("is_ragebait"):
            filter_breakdown["ragebait"] += 1

    avg_score = (
        round(sum(p.get("relevance", 0) for p in visible) / len(visible))
        if visible else 0
    )

    # Build the 'understood' sentence from real pipeline facts
    total_posts = len(scored_posts)
    scoreable = [
        p for p in visible
        if p.get("reason", "") not in ("Not scored", "Could not score this post.", "Scoring unavailable")
        and p.get("relevance", 50) != 50
    ]
    strong_count = buckets["high"]
    strong_pct = round(strong_count / len(scoreable) * 100) if scoreable else 0

    # Human-readable time window
    time_labels = {
        "hour": "the past hour",
        "day": "the past 24 hours",
        "week": "the past week",
        "month": "the past month",
        "year": "the past year",
    }
    sort_labels = {
        "top": "top-voted",
        "hot": "trending",
        "new": "newest",
        "rising": "rising",
    }
    time_str = time_labels.get(time_filter, f"the past {time_filter}")
    sort_str = sort_labels.get(sort_method, sort_method)
    topic_str = f'"{search_term}"' if search_term else "your preference"
    behaviour_note = " Past interactions shaped 20% of the ranking." if behaviour_context else ""

    understood = (
        f"Pulled {total_posts} {sort_str} posts from {len(subreddits)} "
        f"{'community' if len(subreddits) == 1 else 'communities'} over {time_str}. "
        f"{strong_pct}% matched {topic_str} well.{behaviour_note}"
    )

    return {
        "understood": understood,
        "buckets": buckets,
        "subreddit_avg": subreddit_avg,
        "filter_breakdown": filter_breakdown,
        "avg_score": avg_score,
        "behaviour_used": bool(behaviour_context),
        "interaction_count": len(behaviour_context.split(";")) if behaviour_context else 0,
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
    sort_method = "top"
    time_filter = "week"
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
        if persona.get("quality_baseline"):
            parts.append(f"Quality standard: {persona['quality_baseline']}")
        if selected_tones:
            tone_labels = [TONES[t]["label"] for t in selected_tones if t in TONES]
            parts.append(f"Desired tone: {', '.join(tone_labels)}.")
        if active_chips:
            parts.append(f"Focus specifically on: {', '.join(active_chips)}.")

        full_preference = " ".join(parts) if parts else "Show me interesting content"

        behaviour_context = get_interaction_context()

        # --- Cache lookup happens BEFORE extract_subreddits ---
        # This skips the 5-8s LLM+Reddit subreddit discovery call on hits.
        # Cache is keyed by raw search term only (see cache.py), persisted
        # to SQLite so it survives Flask restarts.
        cached = None if force_refresh else get_cached(preference.strip(), persona_key)

        if cached:
            scored_posts, subreddits_used = cached
            scored_posts = [p.copy() for p in scored_posts]
            original_posts = [p.copy() for p in scored_posts]
        else:
            # Cache miss — run full pipeline
            subreddits_used, reddit_matched_subs = extract_subreddits(full_preference)

            if not subreddits_used:
                print("[app] No valid subreddits found — will rely on search fallback")

            all_posts: list[dict] = []

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

            if not all_posts and preference.strip():
                print("[app] No posts from subreddits — using search-only fallback")
                all_posts = search_reddit(preference.strip(), 15)

            unique_posts = deduplicate_posts(all_posts)

            # Cap at 30 posts before scoring. Cohere latency scales with
            # prompt token count — sending 70 posts takes 3-4x longer than
            # sending 30, with diminishing quality return. Sort by engagement
            # rate first so the best candidates are kept.
            unique_posts.sort(key=lambda x: x.get("engagement_rate", 0), reverse=True)
            unique_posts = unique_posts[:30]

            original_posts = [p.copy() for p in unique_posts]

            # score_posts and generate_filter_chips run concurrently
            with ThreadPoolExecutor(max_workers=2) as executor:
                score_future = executor.submit(
                    score_posts, full_preference, unique_posts, behaviour_context
                )
                chips_future = executor.submit(generate_filter_chips, preference)

                scored_posts = score_future.result()
                filter_chips = chips_future.result()

            # Persist to SQLite cache — survives restarts
            set_cached(preference.strip(), persona_key, subreddits_used, [p.copy() for p in scored_posts])

        # chips on cache hit — only remaining LLM call
        if cached:
            filter_chips = generate_filter_chips(preference)

        transparency = _build_transparency_report(
            scored_posts,
            behaviour_context,
            subreddits_used,
            search_term=preference.strip(),
            persona_key=persona_key,
            sort_method=sort_method,
            time_filter=time_filter,
        )

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