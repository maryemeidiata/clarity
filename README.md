# Clarity

**Your feed, your rules.**

Clarity is a web app that pulls real social media posts, lets you describe what you want to see in plain language, and uses AI to re-rank your feed based on your preferences — not the platform's algorithm.

## How it works

1. You describe what you want to see (or pick a mood/preset)
2. Clarity pulls real posts from Reddit
3. An AI (Cohere) scores each post on relevance, toxicity, and spam
4. Posts are re-ranked — good stuff rises, junk sinks or gets hidden
5. You can compare your feed vs. the original algorithm order

## Features

- Natural-language preference input
- Mood selector (Relax, Learn, Laugh, Explore)
- Quick presets (Animals, Foodie, Science, Creative, Fitness)
- AI-powered content scoring and classification
- Toxic, sponsored, and rage-bait filters
- Side-by-side comparison (Your Feed vs Original)
- Feed Quality Score (0-100)
- Session timer with wellbeing nudge
- View original post links
- Mobile responsive

## Setup

1. Clone the repo
2. Install dependencies:
```
pip3 install flask cohere requests python-dotenv
```
3. Create a `.env` file:
```
COHERE_API_KEY=your-key-here
```
4. Run the app:
```
python3 app.py
```
5. Open http://127.0.0.1:5000 in your browser

## Tech Stack

- **Frontend:** HTML/CSS/JS (Flask templates)
- **Backend:** Flask (Python)
- **AI:** Cohere API (command-a-03-2025)
- **Data:** Reddit public API
- **Design:** Glassmorphism, responsive, dark theme

## Team

Built for Prototyping with Data and AI