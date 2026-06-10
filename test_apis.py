"""
test_apis.py
Quick 3-API comparison for CM verification on 5 states.
"""

import os, httpx, time
from dotenv import load_dotenv

load_dotenv()

TAVILY_KEY    = os.environ.get("TAVILY_API_KEY", "")
PPLX_KEY      = os.environ.get("PERPLEXITY_API_KEY", "")
GEMINI_KEY    = os.environ.get("GEMINI_API_KEY", "")

TEST_STATES = ["Karnataka", "West Bengal", "Bihar", "Kerala", "Tamil Nadu"]

# ── Tavily ────────────────────────────────────────────────────────────────────

def tavily(state):
    if not TAVILY_KEY:
        return "NO KEY"
    try:
        r = httpx.post("https://api.tavily.com/search", json={
            "api_key": TAVILY_KEY,
            "query": f"Who is the current Chief Minister of {state} India 2025 2026",
            "max_results": 3,
            "include_answer": True,
        }, timeout=30)
        r.raise_for_status()
        d = r.json()
        return (d.get("answer") or "").strip()[:200] or "no answer"
    except Exception as e:
        return f"ERR: {e}"

# ── Perplexity sonar-pro ──────────────────────────────────────────────────────

def perplexity(state):
    if not PPLX_KEY:
        return "NO KEY"
    try:
        r = httpx.post("https://api.perplexity.ai/chat/completions", json={
            "model": "sonar-pro",
            "messages": [
                {"role": "system", "content": "Answer in one sentence. Name only."},
                {"role": "user",   "content": f"Who is the current Chief Minister of {state}, India as of 2026?"},
            ],
            "max_tokens": 100,
        }, headers={"Authorization": f"Bearer {PPLX_KEY}"}, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()[:200]
    except Exception as e:
        return f"ERR: {e}"

# ── Gemini 2.5 Flash with Google Search grounding ────────────────────────────

def gemini(state):
    if not GEMINI_KEY:
        return "NO KEY (add GEMINI_API_KEY to .env)"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
        r = httpx.post(url, json={
            "contents": [{"parts": [{"text": f"Who is the current Chief Minister of {state}, India as of 2026? Answer in one sentence."}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 100},
        }, timeout=30)
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return " ".join(p.get("text","") for p in parts).strip()[:200]
    except Exception as e:
        return f"ERR: {e}"

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    W = 22
    print(f"\n{'State':<16} | {'Tavily':<{W}} | {'Perplexity sonar-pro':<{W}} | Gemini 2.5 Flash")
    print("-" * 100)

    for state in TEST_STATES:
        tv = tavily(state);     time.sleep(1)
        pp = perplexity(state); time.sleep(1)
        gm = gemini(state);     time.sleep(1)

        def t(s, n=W): return s[:n-1]+"~" if len(s)>=n else s

        print(f"\n{state}")
        print(f"  Tavily     : {tv}")
        print(f"  Perplexity : {pp}")
        print(f"  Gemini     : {gm}")

    print("\nDone.")

if __name__ == "__main__":
    main()
