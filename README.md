# Clarity

**Your feed, your rules.**

Clarity is a feed re-ranking engine that pulls real social media content from Reddit, scores it using AI against your stated preferences, and re-orders it so the content you care about rises to the top — and the noise sinks or disappears.

It is not a social media app. It is a transparency and control layer that sits on top of one.

## How It Works

1. You choose an intention (Learner, Explorer, Recharger, Tracker), set a content tone, and describe what you want to see
2. Clarity discovers relevant Reddit communities using both Reddit's search API and an LLM (Cohere) in parallel
3. Posts are fetched concurrently from multiple subreddits and deduplicated
4. Each post is scored by the LLM on relevance (0-100), toxicity, sponsored content, and rage-bait detection
5. VADER sentiment analysis runs locally on every post to classify emotional tone (positive / neutral / negative)
6. Posts are re-ranked by relevance score, with filtered content removed entirely
7. A full transparency report shows which communities were searched, how posts matched, and what was filtered

## Features

### Feed Intelligence
- Natural-language preference input with LLM interpretation
- Four persona modes (Learner, Explorer, Recharger, Tracker) — each with distinct sort strategies and quality baselines
- Five content tones (Funny, Inspiring, Optimistic, Analytical, Calming)
- AI-generated smart filter chips for sub-topic narrowing
- Conversational algorithm refinement ("less opinion, more data")

### Content Scoring
- Cohere LLM batch scoring with calibrated 0-100 scale
- VADER sentiment analysis (local, no API call) for tone classification
- Toxicity, sponsored content, and rage-bait detection
- Feed Tone Bar showing positive/neutral/negative distribution
- Tone warning when negative content exceeds 40%

### Transparency
- Deterministic transparency panel (no LLM — built from real pipeline data)
- Communities searched with per-subreddit relevance averages
- Score distribution visualization (strong match / partial / low relevance)
- Filter breakdown (toxic / sponsored / rage-bait counts)
- Behaviour signal indicator when past interactions influence ranking

### User Interaction
- Thumbs up / thumbs down signals stored in SQLite
- Past interactions shape 20% of future rankings
- Session timer with gentle wellbeing nudge
- View original post links to Reddit

### Analytics & Wrapped
- Session history with quality scores and tone data
- Clarity Wrapped — AI-generated content DNA profile, personality description, and fun fact
- Mental Health Index — per-topic emotional tone tracking
- Healthiest topic and biggest blind spot detection
- Mood timeline showing tone trends across sessions
- Engagement ratio (likes vs dismissals)
- Shielded counter (total toxic/spam/rage-bait posts blocked)

### Performance
- SQLite-persistent cache (survives restarts, 30-minute TTL)
- Parallel subreddit discovery (Reddit search + LLM concurrent)
- Parallel post fetching (ThreadPoolExecutor, up to 8 workers)
- Concurrent scoring + chip generation
- Post cap at 30 before scoring to control LLM latency
- Rate limit handling with automatic retry on Reddit 429s

### Design
- Animated welcome page with space particle background and eye+orbit logo
- Dive-into-eye transition animation
- Glassmorphism dark theme with purple accent palette
- Lucide SVG icons throughout (no emoji)
- Fully responsive (desktop + mobile)
- Loading skeleton with rotating status messages

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Flask (Python) |
| LLM | Cohere API (command-a-03-2025) — 5 distinct call types |
| Sentiment | VADER (vaderSentiment) — local, no API |
| Data Source | Reddit public JSON API (subreddit + search endpoints) |
| Database | SQLite (interactions, sessions, cache) |
| Frontend | HTML / CSS / JS (Jinja2 templates) |
| Icons | Lucide |
| Hosting | AWS EC2 |

## LLM Usage (Non-Straightforward)

Clarity uses the Cohere LLM in five distinct ways, none of which are simple prompt-in/text-out:

1. **Subreddit discovery** — LLM suggests relevant communities, run in parallel with Reddit's own search API, results merged and validated
2. **Batch post scoring** — All posts scored in a single call with structured JSON output, calibrated scoring scale, and reason generation
3. **Filter chip generation** — LLM generates contextual sub-category suggestions based on the user's topic
4. **Algorithm refinement** — Conversational rewriting of the user's preference based on natural-language feedback
5. **Content DNA profiling** — LLM analyzes accumulated session preferences to generate a personality profile

Additionally, VADER sentiment analysis runs as a local ML model on every post, creating a hybrid architecture (cloud LLM + local model).

## Setup

```bash
git clone <repo-url>
cd clarity
pip3 install -r requirements.txt
```

Create a `.env` file:
```
COHERE_API_KEY=your-key-here
```

Run:
```bash
python3 app.py
```

Open http://127.0.0.1:5000

## File Structure

```
clarity/
├── app.py                 # Flask app, routes, pipeline orchestration
├── feed.py                # Reddit data fetching, deduplication, validation
├── scorer.py              # LLM scoring, VADER sentiment, filter chips
├── db.py                  # SQLite schema, logging, analytics queries
├── cache.py               # SQLite-persistent cache with TTL
├── requirements.txt       # Python dependencies
├── templates/
│   ├── index.html         # Main app (welcome page + feed UI)
│   ├── analytics.html     # Stats dashboard
│   └── wrapped.html       # Clarity Wrapped + Content DNA
└── .env                   # API keys (not committed)
```

## Team

Built for Prototyping with Data and AI