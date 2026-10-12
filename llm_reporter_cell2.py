# %% Cell 2: ask an LLM (through OpenRouter) to write the report
"""
Needs an OpenRouter API key (https://openrouter.ai/keys). Where the key is read from:
  1. Colab: the key icon in the left bar -> add a secret named OPENROUTER_API_KEY -> allow access.
  2. Terminal: set the environment variable OPENROUTER_API_KEY.
  3. Otherwise you are asked to paste it (hidden while typing).
Never put the key inside the code.
"""

import getpass
import json
import os

import requests

# ---------------------------------------------------------------------------
# SETTINGS for Cell 2
# ---------------------------------------------------------------------------
OPENROUTER_MODEL = "qwen/qwen3.7-flash"     # or a specific model, e.g. "anthropic/claude-sonnet-4.5"; see openrouter.ai/models
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
REPORT_WORDS = 500                       # the report must stay under this many words
BOOKS_FOR_LLM = 15                       # top books listed in the prompt (keyword totals cover all)
EVIDENCE_BOOKS = 5                       # books whose keyword quotes are shown to the LLM
EVIDENCE_QUOTES_PER_KEYWORD = 1
REPORT_LANGUAGE = "English"
OUTPUT_REPORT = "quran_keyword_report.md"
JSON_FILE = "quran_keyword_books.json"   # must match OUTPUT_JSON of Cell 1


def get_api_key():
    try:
        from google.colab import userdata        # Colab secrets
        return userdata.get("OPENROUTER_API_KEY")
    except Exception:
        pass
    return os.environ.get("OPENROUTER_API_KEY") or getpass.getpass("OpenRouter API key: ")


def build_messages(summary_text, data):
    labels = data["meta"]["keywords"]
    mode = data["meta"].get("mode", "QK")
    cautions = ["the sample passages are normalized text, not an edition to cite"]
    if "K" in mode:
        cautions.insert(0, "keyword counts are lower bounds")
    if "Q" in mode:
        cautions.insert(0, "quotation counts include formulaic Quranic phrases and are an upper "
                           "estimate of deliberate citation")
    evidence = []
    for b in data["books"][:EVIDENCE_BOOKS]:
        for lab in labels:
            for q in b["keyword_quotes"].get(lab, [])[:EVIDENCE_QUOTES_PER_KEYWORD]:
                evidence.append(f"- {b['uri']} [{lab}, {q['page'] or 'page unknown'}]: ...{q['quote']}...")
    system = (
        "You help a historian of Islamic law read the output of a text-mining run over the OpenITI "
        "corpus. Write in a humane, process-oriented academic register, not a polemical one. "
        "Use ONLY the numbers and passages supplied; never recompute, round or invent figures, and "
        "say so if the data cannot answer something. Refer to books by their OpenITI URI. "
        f"Write in {REPORT_LANGUAGE}, no more than {REPORT_WORDS} words, with these short sections: "
        "1) What was searched; 2) What the figures show; 3) Books to read first, and why; "
        "4) Cautions. In the cautions, state that " + "; that ".join(cautions) + ". "
        "Only discuss what the run actually measured (mode: " + mode + ").")
    user = (f"RESULTS (computed by code)\n{summary_text}\n\n"
            f"SAMPLE PASSAGES (for the top books)\n" + "\n".join(evidence or ["(none)"]))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_openrouter(messages, api_key):
    r = requests.post(
        OPENROUTER_URL, timeout=180,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "X-Title": "OpenITI Quran keyword report"},
        json={"model": OPENROUTER_MODEL, "messages": messages, "temperature": 0.3})
    if r.status_code != 200:
        hint = {401: "the API key was rejected", 402: "the account has no credit for this model",
                429: "rate limit reached: wait a minute and run the cell again"}.get(r.status_code, "")
        raise RuntimeError(f"OpenRouter returned HTTP {r.status_code}"
                           f"{' - ' + hint if hint else ''}\n{r.text[:300]}")
    body = r.json()
    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Unexpected reply from OpenRouter: {str(body)[:300]}")


def make_report():
    with open(JSON_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if not data["books"]:
        raise SystemExit("No books passed the filter - run Cell 1 again with a wider range or, for Q/QK, "
                         "a lower minimum number of quotations.")
    summary_text = summarize_results(JSON_FILE, out_path=None, top=BOOKS_FOR_LLM)   # from Cell 1
    messages = build_messages(summary_text, data)
    api_key = get_api_key()

    report = call_openrouter(messages, api_key)
    if len(report.split()) > REPORT_WORDS:               # one retry if the model ran over the limit
        messages += [{"role": "assistant", "content": report},
                     {"role": "user", "content": f"Too long ({len(report.split())} words). Rewrite in "
                                                 f"under {REPORT_WORDS} words, keeping every figure unchanged."}]
        report = call_openrouter(messages, api_key)

    n = len(report.split())
    note = "" if n <= REPORT_WORDS else f"\n\n(Note: {n} words, over the {REPORT_WORDS}-word target.)"
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write(report + note + "\n")
    print(report + note)
    print(f"\nSaved {OUTPUT_REPORT} ({n} words, model: {OPENROUTER_MODEL})")
    return report


if in_notebook() or ask("Write the report with an LLM through OpenRouter now? (y/N)", "n").lower().startswith("y"):
    report = make_report()
