import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, datetime

try:
    import anthropic
except ImportError:
    print("anthropic package not found. Run: pip install anthropic")
    sys.exit(1)

CONFERENCES_FILE = "conferences.json"
TODAY = date.today()
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def fetch_page(url: str, timeout: int = 15) -> str | None:
    """Download a page and return its text, or None on failure."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; BiomedDeadlines-bot/1.0; "
                "+https://jambuzz.github.io/biomeddeadlines/)"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Try UTF-8, fall back to latin-1
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1")
    except Exception as e:
        print(f"  ⚠️  Could not fetch {url}: {e}")
        return None


def ask_claude_for_deadline(conf_name: str, url: str, page_text: str) -> dict:
    """
    Ask Claude to extract the next abstract submission deadline from a page.
    Returns a dict: { "found": bool, "date": "YYYY-MM-DD" or None,
                      "confidence": "high"/"medium"/"low", "note": str }
    """
    # Trim page to first ~6000 chars to stay within token budget
    snippet = page_text[:6000]

    prompt = f"""You are helping maintain a tracker of biomedical conference abstract deadlines.

Conference: {conf_name}
URL: {url}

Below is text scraped from the conference website. Your job is to find the ABSTRACT SUBMISSION deadline for the NEXT upcoming cycle (likely 2027, since 2026 deadlines have already passed).

Do NOT return a deadline that has already passed (before today, {TODAY.isoformat()}).

Respond ONLY with a JSON object — no explanation, no markdown, no backticks. Format:
{{
  "found": true or false,
  "date": "YYYY-MM-DD" or null,
  "confidence": "high", "medium", or "low",
  "note": "brief explanation of what you found or why you couldn't find it"
}}

"confidence" rules:
- high: explicit date like "March 15, 2027" or "15 March 2027"
- medium: month + year given ("March 2027") — use the 1st of that month
- low: vague ("early 2027", "spring 2027") — make a reasonable guess and say so in note

--- PAGE TEXT START ---
{snippet}
--- PAGE TEXT END ---"""

    try:
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"found": False, "date": None, "confidence": "low",
                "note": f"Claude returned non-JSON: {e}"}
    except Exception as e:
        return {"found": False, "date": None, "confidence": "low",
                "note": f"API error: {e}"}


def deadline_is_past(iso_date: str) -> bool:
    try:
        d = date.fromisoformat(iso_date)
        return d < TODAY
    except ValueError:
        return False  # malformed date — leave it alone


def main():
    if not os.path.exists(CONFERENCES_FILE):
        print(f"❌ {CONFERENCES_FILE} not found.")
        sys.exit(1)

    with open(CONFERENCES_FILE, "r") as f:
        conferences = json.load(f)

    updated = []
    review_flags = []  # conferences Claude updated — for the GitHub Issue summary

    for conf in conferences:
        name = conf.get("name", "Unknown")
        deadline = conf.get("abstractDeadline", "")
        url = conf.get("url", "")

        if not deadline or not deadline_is_past(deadline):
            print(f"  ✅ {name} — deadline not past, skipping.")
            updated.append(conf)
            continue

        if not url:
            print(f"  ⚠️  {name} — no URL, skipping.")
            updated.append(conf)
            continue

        print(f"\n🔍 {name} — deadline {deadline} is past. Fetching {url} ...")
        page = fetch_page(url)

        if not page:
            print(f"  ⚠️  Could not fetch page.")
            updated.append(conf)
            continue

        result = ask_claude_for_deadline(name, url, page)
        print(f"  Claude says: {result}")

        if result.get("found") and result.get("date"):
            new_date = result["date"]
            old_date = conf["abstractDeadline"]

            # Sanity check: new date must be in the future
            try:
                if date.fromisoformat(new_date) <= TODAY:
                    print(f"  ⚠️  Returned date {new_date} is still in the past — skipping.")
                    updated.append(conf)
                    continue
            except ValueError:
                print(f"  ⚠️  Returned date '{new_date}' is not valid ISO format — skipping.")
                updated.append(conf)
                continue

            conf["abstractDeadline"] = new_date
            conf["autoUpdated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            conf["autoUpdateNote"] = result.get("note", "")
            conf["autoUpdateConfidence"] = result.get("confidence", "unknown")
            conf["needsHumanReview"] = result.get("confidence") != "high"

            review_flags.append({
                "name": name,
                "old": old_date,
                "new": new_date,
                "confidence": result.get("confidence"),
                "note": result.get("note", ""),
                "url": url,
            })

            print(f"  ✅ Updated {name}: {old_date} → {new_date} ({result.get('confidence')} confidence)")
        else:
            print(f"  ❌ No new date found: {result.get('note', '')}")
            # Mark as needing manual attention
            conf["needsHumanReview"] = True
            conf["autoUpdateNote"] = result.get("note", "Could not find new deadline automatically")

        updated.append(conf)

    # Write back
    with open(CONFERENCES_FILE, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"\n📝 conferences.json updated.")

    # Output a summary for GitHub Actions / the Issue body
    if review_flags:
        print("\n--- REVIEW SUMMARY ---")
        for r in review_flags:
            flag = "⚠️ NEEDS REVIEW" if r["confidence"] != "high" else "✅"
            print(f"{flag} {r['name']}: {r['old']} → {r['new']} ({r['confidence']})")
            print(f"   Note: {r['note']}")
            print(f"   URL: {r['url']}")

        # Write to a file so the GitHub Action can post it as an Issue
        issue_body = "## 🤖 Auto-updated conference deadlines\n\n"
        issue_body += "The following conferences were updated by the deadline bot. "
        issue_body += "Please verify any marked ⚠️ before the next deploy.\n\n"
        issue_body += "| Conference | Old deadline | New deadline | Confidence | Note |\n"
        issue_body += "|---|---|---|---|---|\n"
        for r in review_flags:
            flag = "⚠️" if r["confidence"] != "high" else "✅"
            issue_body += f"| {r['name']} | {r['old']} | {r['new']} | {flag} {r['confidence']} | {r['note']} |\n"
        issue_body += f"\n_Run at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_"

        with open("update_summary.md", "w") as f:
            f.write(issue_body)
        print("\nWrote update_summary.md")
    else:
        print("\nNo conferences were updated.")


if __name__ == "__main__":
    main()
