"""
verify_politicians.py
Checks every state's Chief Minister in our DB against Gemini (Google Search grounded).

Usage:
    python verify_politicians.py
"""

import os
import sys
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types

BACKEND_URL = "https://netalog-backend.onrender.com"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

if not GEMINI_API_KEY:
    sys.exit("ERROR: GEMINI_API_KEY not set in .env")

_client = genai.Client(api_key=GEMINI_API_KEY)
_SEARCH_TOOL = types.Tool(google_search=types.GoogleSearch())


def gemini_current_cm(state_name: str) -> str:
    prompt = (
        f"Who is the current Chief Minister of {state_name} as of 2026? "
        "Reply with the full name only, no extra text."
    )
    response = _client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(tools=[_SEARCH_TOOL]),
    )
    return response.text.strip()


def names_match(scraped: str, gemini_text: str) -> bool:
    scraped_lower = scraped.lower()
    gemini_lower = gemini_text.lower()
    if scraped_lower in gemini_lower:
        return True
    # Majority of significant words (>3 chars) must appear in the answer
    words = [w for w in scraped_lower.split() if len(w) > 3]
    if not words:
        return False
    hits = sum(1 for w in words if w in gemini_lower)
    return hits >= max(1, (len(words) + 1) // 2)


def main():
    with httpx.Client(timeout=30) as client:
        states = client.get(f"{BACKEND_URL}/states").raise_for_status().json()

    print(f"Verifying Chief Ministers for {len(states)} states...\n")

    ok, wrong, no_cm, errors = 0, 0, 0, 0

    for state in sorted(states, key=lambda s: s["name"]):
        state_id   = state["id"]
        state_name = state["name"]

        # Fetch our scraped CM
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{BACKEND_URL}/politicians",
                params={"state_id": state_id, "position": "Chief Minister", "limit": 1},
            )
            cms = resp.raise_for_status().json()

        if not cms:
            print(f"NO CM : {state_name} — not in DB")
            no_cm += 1
            continue

        scraped_name = cms[0]["name"]

        # Ask Gemini with Google Search grounding
        try:
            gemini_answer = gemini_current_cm(state_name)
            time.sleep(1)   # stay within free-tier rate limit
        except Exception as exc:
            print(f"ERROR : {state_name} — {exc}")
            errors += 1
            continue

        if names_match(scraped_name, gemini_answer):
            print(f"OK    : {state_name} — {scraped_name}")
            ok += 1
        else:
            print(f"WRONG : {state_name} — we have '{scraped_name}', actual is '{gemini_answer}'")
            wrong += 1

    print(f"\n--- Summary ---")
    print(f"OK     : {ok}")
    print(f"WRONG  : {wrong}")
    print(f"NO CM  : {no_cm}  (ministers not tagged for these states yet)")
    print(f"ERRORS : {errors}")


if __name__ == "__main__":
    main()
