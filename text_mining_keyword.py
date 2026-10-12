"""
OpenITI keyword monolith (Google Colab ready, standard library + optional matplotlib).

Pipeline:  clone one OpenITI repo -> read each Arabic book -> clean + normalize
           -> count keywords with `re` -> save counts AND short quotes as JSON
           -> (optional) bar chart.

The JSON is built to be handed to an LLM in the next step: every hit keeps its
book URI, page marker and a short quote, so any claim can be checked at source.
"""

import bisect
import json
import os
import re
import subprocess
from collections import Counter

# ---------------------------------------------------------------------------
# 0. SETTINGS - change these
# ---------------------------------------------------------------------------
REPO_URL = "https://github.com/OpenITI/0850AH"   # one repo = one 25-year slice (AH)
LOCAL_CORPUS_DIR = "./openiti_corpus_0850"
OUTPUT_JSON = "keyword_occurrences.json"
OUTPUT_MD = "keyword_report.md"     # human-readable report; set to None to skip
REPORT_TOP_BOOKS = 15               # books listed (with quotes) per keyword in the report

# {Arabic keyword: short Latin label}. The label is used as the JSON key and in
# charts (matplotlib draws Arabic letters disconnected).
# Mind homographs: bare "عرف" also counts the verb "he knew" and "فعرف به", which
# swamps the legal noun. Writing the noun WITH the article ("العرف") avoids that.
KEYWORDS = {
    "العرف": "al_urf",
    "ضرورة": "darura",
}

ALLOW_PREFIXES = True      # also count wa-/fa-/bi-/ka-/li-/al- forms, e.g. والعرف
PRIMARY_ONLY = True        # skip duplicate digitizations (keep PRIMARY_VERSION only)
WINDOW = 60                # characters of context to quote on each side of a hit
MAX_QUOTES_PER_BOOK = 3    # quotes kept per keyword per book (keeps JSON small)
MAX_BOOKS = None           # e.g. 20 for a quick test run; None = whole repo


# ---------------------------------------------------------------------------
# 1. GET THE CORPUS
# ---------------------------------------------------------------------------
def clone_corpus(repo_url, dest):
    """Shallow-clone a repo (latest snapshot only, far smaller than full history)."""
    if os.path.isdir(dest) and os.listdir(dest):
        print(f"Using existing corpus folder: {dest}")
        return dest
    print(f"Cloning {repo_url} ... (can take a few minutes)")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, dest],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed:\n{result.stderr}")
    return dest


# ---------------------------------------------------------------------------
# 2. FIND THE BOOKS
# ---------------------------------------------------------------------------
# Version files look like  0826IbnCiraqi.Juz.ShamAY0035240-ara1  (+ optional
# .completed / .mARkdown / .inProgress). Metadata files end in .yml.
VERSION_FILE = re.compile(r"-ara\d+(?:\.(?:completed|mARkdown|inProgress))?$")
STATUS_SUFFIX = re.compile(r"\.(?:completed|mARkdown|inProgress)$")


def is_primary(version_path):
    """Read the sibling .yml: primary versions list PRIMARY_VERSION under ISSUES."""
    yml_path = STATUS_SUFFIX.sub("", version_path) + ".yml"
    if not os.path.exists(yml_path):
        return True  # no metadata to judge by: keep it
    with open(yml_path, encoding="utf-8-sig", errors="replace") as f:
        return re.search(r"ISSUES#*:.*PRIMARY_VERSION", f.read()) is not None


def find_books(corpus_dir, primary_only=True):
    paths = []
    for folder, _, files in os.walk(os.path.join(corpus_dir, "data")):
        for name in files:
            if VERSION_FILE.search(name):
                path = os.path.join(folder, name)
                if not primary_only or is_primary(path):
                    paths.append(path)
    return sorted(paths)


# ---------------------------------------------------------------------------
# 3. CLEAN + NORMALIZE  (each rule is a scholarly choice)
# ---------------------------------------------------------------------------
TASHKEEL = re.compile("[ً-ْٰ]")     # vowel marks + dagger alif
PAGE_MARK = re.compile(r"PageV\d+P\d+")
MILESTONE = re.compile(r"\bms\d+\b")
LINE_START = re.compile(r"^(?:~~|#+(?:\s*\|+)?)\s*")  # "~~" continuation, "# " paragraph
ARABIC_LETTER = "ء-ي"


def normalize(text):
    text = TASHKEEL.sub("", text).replace("ـ", "")   # vowels, tatweel
    text = re.sub("[أإآٱ]", "ا", text)                    # unify alif forms
    return text.replace("ى", "ي")                         # unify ya / alif maqsura


def load_text(path):
    """Return (clean text, page_positions). OpenITI breaks lines every ~70 chars
    ("~~" lines), so lines are joined into one string before searching; that way
    a multi-word keyword can still match across a line break."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        lines = f.read().splitlines()

    header_end = next((i for i, l in enumerate(lines) if "#META#Header#End#" in l), -1)
    body = lines[header_end + 1:] if header_end >= 0 else [
        l for l in lines if not l.startswith("#META#")
    ]

    pieces, page_positions, pos = [], [], 0
    for line in body:
        for m in PAGE_MARK.finditer(line):
            page_positions.append((pos, m.group(0)[4:]))   # e.g. "V01P002"
        line = PAGE_MARK.sub(" ", line)
        line = MILESTONE.sub(" ", LINE_START.sub("", line))
        line = " ".join(normalize(line).split())
        if line:
            pieces.append(line)
            pos += len(line) + 1
    return " ".join(pieces), page_positions


# ---------------------------------------------------------------------------
# 4. MATCH KEYWORDS WITH re
# ---------------------------------------------------------------------------
# Optional clitics before the word: wa-/fa-, then bi-/ka-/li-/al- (or lil-).
PREFIXES = r"(?:[وف])?(?:[بكل]?ال|لل|[بكل])?"


def build_pattern(keyword, allow_prefixes):
    """\\b is unreliable for Arabic (it treats vowel marks as word breaks), so
    the word edges are defined explicitly as 'not an Arabic letter'. Suffixes
    (-hu, -at, -un ...) are NOT matched: add variants to KEYWORDS if you need them."""
    core = r"\s+".join(re.escape(w) for w in normalize(keyword).split())
    prefix = PREFIXES if allow_prefixes else ""
    return re.compile(rf"(?<![{ARABIC_LETTER}]){prefix}{core}(?![{ARABIC_LETTER}])")


def page_at(page_positions, pos):
    starts = [p for p, _ in page_positions]
    i = bisect.bisect_right(starts, pos) - 1
    return page_positions[i][1] if i >= 0 else None


# ---------------------------------------------------------------------------
# 5. SCAN THE CORPUS
# ---------------------------------------------------------------------------
def scan_corpus(corpus_dir, keywords):
    patterns = {label: build_pattern(kw, ALLOW_PREFIXES) for kw, label in keywords.items()}
    paths = find_books(corpus_dir, PRIMARY_ONLY)
    if MAX_BOOKS:
        paths = paths[:MAX_BOOKS]
    print(f"Scanning {len(paths)} books ...")

    totals, books = Counter(), {}
    for path in paths:
        uri = STATUS_SUFFIX.sub("", os.path.basename(path))
        text, page_positions = load_text(path)
        entry = {"counts": {}, "quotes": {}}
        for label, pattern in patterns.items():
            quotes, n = [], 0
            for m in pattern.finditer(text):
                n += 1
                if len(quotes) < MAX_QUOTES_PER_BOOK:
                    start, end = max(0, m.start() - WINDOW), m.end() + WINDOW
                    quotes.append({"page": page_at(page_positions, m.start()),
                                   "quote": text[start:end]})
            if n:
                entry["counts"][label] = n
                entry["quotes"][label] = quotes
                totals[label] += n
        if entry["counts"]:                      # keep only books with hits
            entry["death_ah"] = int(uri[:4]) if uri[:4].isdigit() else None
            books[uri] = entry

    return {
        "meta": {
            "repo": REPO_URL,
            "keywords": {label: kw for kw, label in keywords.items()},
            "allow_prefixes": ALLOW_PREFIXES,
            "primary_only": PRIMARY_ONLY,
            "window_chars": WINDOW,
            "books_scanned": len(paths),
            "books_with_hits": len(books),
            "note": "Quotes are normalized text (no vowel marks, unified alif/ya).",
        },
        "totals": dict(totals),
        "books": books,
    }


# ---------------------------------------------------------------------------
# 6. OPTIONAL CHART
# ---------------------------------------------------------------------------
def plot_top_books(results, label, n=10):
    import matplotlib.pyplot as plt   # preinstalled in Colab
    ranked = sorted(
        ((uri, b["counts"][label]) for uri, b in results["books"].items() if label in b["counts"]),
        key=lambda x: x[1], reverse=True)[:n]
    if not ranked:
        print(f"No hits for '{label}' - nothing to plot.")
        return
    names, counts = zip(*ranked)
    plt.figure(figsize=(9, 0.5 * len(ranked) + 1.5))
    plt.barh(names[::-1], counts[::-1], color="#b1532a")
    plt.xlabel(f"hits for '{label}'")
    plt.title(f"Top books for '{label}'")
    plt.tight_layout()
    plt.savefig(f"top_books_{label}.png", dpi=150)
    plt.show()


# ---------------------------------------------------------------------------
# 6b. MARKDOWN REPORT (human-readable twin of the JSON; also fine as LLM input)
# ---------------------------------------------------------------------------
def write_markdown_report(results, path, top_books=REPORT_TOP_BOOKS):
    meta = results["meta"]
    out = ["# Keyword findings report", ""]
    out += [f"- Corpus: {meta['repo']}",
            f"- Books scanned: {meta['books_scanned']} "
            f"(with hits: {meta['books_with_hits']})",
            f"- Prefixes allowed: {meta['allow_prefixes']}; "
            f"primary versions only: {meta['primary_only']}",
            f"- {meta['note']}", ""]

    out += ["## Summary", "", "| Keyword | Label | Total hits | Books with hits |",
            "|---|---|---:|---:|"]
    for label, kw in meta["keywords"].items():
        n_books = sum(1 for b in results["books"].values() if label in b["counts"])
        out.append(f"| {kw} | {label} | {results['totals'].get(label, 0)} | {n_books} |")
    out.append("")

    for label, kw in meta["keywords"].items():
        ranked = sorted(
            ((uri, b) for uri, b in results["books"].items() if label in b["counts"]),
            key=lambda x: x[1]["counts"][label], reverse=True)[:top_books]
        out += [f"## {kw} ({label})", ""]
        if not ranked:
            out += ["No hits.", ""]
            continue
        out += [f"Top {len(ranked)} books by hits.", "",
                "| # | Book (URI) | Death (AH) | Hits |", "|---:|---|---:|---:|"]
        for i, (uri, b) in enumerate(ranked, 1):
            out.append(f"| {i} | `{uri}` | {b.get('death_ah') or '?'} | {b['counts'][label]} |")
        out.append("")
        for uri, b in ranked:
            out.append(f"### `{uri}`  - {b['counts'][label]} hits")
            out.append("")
            for q in b["quotes"][label]:
                page = q["page"] or "page unknown"
                out.append(f"> ...{q['quote'].replace(chr(10), ' ')}...  ")
                out.append(f"> *({page})*")
                out.append("")
    out.append("---")
    out.append("Counts are lower bounds (suffixed forms are not matched). "
               "Quotes are samples, not every hit; confirm each in the source text.")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# ---------------------------------------------------------------------------
# 7. RUN
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    keywords = dict(KEYWORDS)
    if not keywords:   # fall back to asking, as in the original draft
        typed = input("Keywords, comma-separated, without the article ال: ")
        keywords = {k.strip(): k.strip() for k in typed.split(",") if k.strip()}

    clone_corpus(REPO_URL, LOCAL_CORPUS_DIR)
    results = scan_corpus(LOCAL_CORPUS_DIR, keywords)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {OUTPUT_JSON}")
    if OUTPUT_MD:
        write_markdown_report(results, OUTPUT_MD)
        print(f"Saved {OUTPUT_MD}")
    print("Totals:", results["totals"])
    for label in results["totals"]:
        plot_top_books(results, label)
