# %% [markdown]
# # OpenITI: keywords + Quran quotations, asynchronous (PhD level)
# **Cell 1** asks in the terminal what to run: `Q` (Quran quotations only), `K` (keywords only)
# or `QK` (both), then what to search. It clones the OpenITI repos for the year range (in
# parallel), reads every Arabic book (in parallel), and writes JSON, then a plain list built from
# that JSON. With Q or QK only books quoting the Quran more than the threshold are kept.
# **Cell 2** sends that list to an LLM through OpenRouter and saves the written report.

# %% Cell 1: search, count, JSON, summary list
"""
Pipeline:  you choose Q / K / QK and what to search (asked in the terminal)
           -> year range -> OpenITI repos (async git clone) -> every primary Arabic book
           (async + process pool) -> keyword hits (K) and/or Quran quotation count (Q) per book
           -> Q/QK: keep books with MORE than the threshold of quotations; K: keep books with a hit
           -> JSON -> summarize_results(): exact totals + books ranked.

Run it:  python phd_quran_keyword_async.py     (in Colab: press play on Cell 1 and answer the prompts)

All numbers are computed by code. The LLM in Cell 2 only writes the prose.
Run in Colab or in a terminal (python this_file.py). Needs git; no other installs.
"""

import asyncio
import bisect
import json
import multiprocessing
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 0. SETTINGS - change these
# ---------------------------------------------------------------------------
# The terminal asks for these; pressing Enter keeps the value written here.
DEFAULT_MODE = "QK"        # Q = Quran quotations only, K = keywords only, QK = both
START_AH = 800             # year range = author DEATH dates, in hijri years (inclusive)
END_AH = 900
GITHUB_ORG = "https://github.com/OpenITI"   # repos are named 0025AH, 0050AH ... 1425AH
WORK_DIR = "./openiti_repos"                # cloned repos land in ./openiti_repos/<code>

# {Arabic keyword: short Latin label}. Write nouns with the article (العرف, not عرف).
# Default list: used when you press Enter at the keyword question.
KEYWORDS = {
    "العرف": "al_urf",
    "ضرورة": "darura",
}
ALLOW_PREFIXES = True      # also count wa-/fa-/bi-/ka-/li-/al- forms, e.g. والعرف
PRIMARY_ONLY = True        # skip duplicate digitizations (keep PRIMARY_VERSION only)
WINDOW = 60                # characters of context quoted on each side of a keyword hit
MAX_QUOTES_PER_BOOK = 3    # keyword quotes kept per keyword per book

MIN_QURAN_QUOTES = 50      # keep only books that quote the Quran MORE than this many times
QURAN_MIN_WORDS = 5        # words in a row that must match a verse (4 = looser, 6 = stricter)

MAX_PARALLEL_CLONES = 3    # git clones running at the same time
WORKERS = os.cpu_count() or 2   # books analysed at the same time
MAX_BOOKS_PER_REPO = None  # e.g. 20 for a quick test run; None = every book

OUTPUT_JSON = "quran_keyword_books.json"
OUTPUT_SUMMARY = "quran_keyword_summary.md"

# >>> LLM (OpenRouter): suggests synonyms in Cell 1 (optional) and writes the report in Cell 2
OPENROUTER_MODEL = "openrouter/auto"     # or a specific model, e.g. "anthropic/claude-sonnet-4.5"; see openrouter.ai/models
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SYNONYMS_PER_KEYWORD = 6                 # most terms the LLM may propose per keyword
 
OUTPUT_JSON = "quran_keyword_books.json"
OUTPUT_SUMMARY = "quran_keyword_summary.md"

# ---------------------------------------------------------------------------
# 1. WHICH REPOS, AND GET THEM (async)
# ---------------------------------------------------------------------------
def repos_for_range(start_ah, end_ah):
    """OpenITI repo 0850AH holds authors who died 826-850 AH (25-year slices)."""
    return [f"{c:04d}AH" for c in range(25, 1426, 25) if c >= start_ah and c - 24 <= end_ah]


async def clone_repo(code, semaphore):
    """Shallow `git clone` without blocking the other clones. Skips repos already on disk."""
    dest = os.path.join(WORK_DIR, code)
    if os.path.isdir(dest) and os.listdir(dest):
        print(f"  {code}: already on disk")
        return dest
    os.makedirs(WORK_DIR, exist_ok=True)
    part = dest + ".part"                       # clone here first, rename when complete
    shutil.rmtree(part, ignore_errors=True)
    async with semaphore:
        print(f"  {code}: cloning ...")
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", "--quiet", f"{GITHUB_ORG}/{code}", part,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git clone of {code} failed:\n{err.decode(errors='replace')}")
    os.replace(part, dest)
    print(f"  {code}: done")
    return dest


# ---------------------------------------------------------------------------
# 2. FIND + CLEAN BOOKS  (same rules as the Master script)
# ---------------------------------------------------------------------------
VERSION_FILE = re.compile(r"-ara\d+(?:\.(?:completed|mARkdown|inProgress))?$")
STATUS_SUFFIX = re.compile(r"\.(?:completed|mARkdown|inProgress)$")
TASHKEEL = re.compile("[ً-ْٰ]")
PAGE_MARK = re.compile(r"PageV\d+P\d+")
MILESTONE = re.compile(r"\bms\d+\b")
LINE_START = re.compile(r"^(?:~~|#+(?:\s*\|+)?)\s*")
ARABIC_LETTER = "ء-ي"
PREFIXES = r"(?:[وف])?(?:[بكل]?ال|لل|[بكل])?"


def is_primary(version_path):
    yml_path = STATUS_SUFFIX.sub("", version_path) + ".yml"
    if not os.path.exists(yml_path):
        return True
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


def normalize(text):
    text = TASHKEEL.sub("", text).replace("ـ", "")
    text = re.sub("[أإآٱ]", "ا", text)
    return text.replace("ى", "ي")


def load_text(path):
    """(clean text as ONE string, [(position, page marker)]). Lines are joined first so
    phrases that the book breaks across lines can still be found."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        lines = f.read().splitlines()
    header_end = next((i for i, l in enumerate(lines) if "#META#Header#End#" in l), -1)
    body = lines[header_end + 1:] if header_end >= 0 else [l for l in lines if not l.startswith("#META#")]
    pieces, page_positions, pos = [], [], 0
    for line in body:
        for m in PAGE_MARK.finditer(line):
            page_positions.append((pos, m.group(0)[4:]))
        line = PAGE_MARK.sub(" ", line)
        line = MILESTONE.sub(" ", LINE_START.sub("", line))
        line = " ".join(normalize(line).split())
        if line:
            pieces.append(line)
            pos += len(line) + 1
    return " ".join(pieces), page_positions


def page_at(page_positions, pos):
    i = bisect.bisect_right([p for p, _ in page_positions], pos) - 1
    return page_positions[i][1] if i >= 0 else None


def build_pattern(keyword, allow_prefixes):
    core = r"\s+".join(re.escape(w) for w in normalize(keyword).split())
    return re.compile(rf"(?<![{ARABIC_LETTER}]){PREFIXES if allow_prefixes else ''}{core}(?![{ARABIC_LETTER}])")


# ---------------------------------------------------------------------------
# 3. THE QURAN INDEX  (text comes from OpenITI itself: 0025AH / 0001Quran, Tanzil edition)
# ---------------------------------------------------------------------------
def words(s):
    """Arabic words only: no vowel marks, no punctuation, alif/ya unified."""
    s = re.sub(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]", "", s)
    s = re.sub("[أإآٱ]", "ا", s).replace("ى", "ي")
    return re.sub(r"[^ء-ي]+", " ", s).split()


def load_quran_index(quran_dir, n):
    """Map every n-word chunk of every verse -> [((sura, aya), position)]. The basmala chunk
    is left out: it is a formula, not a citation."""
    files = [p for p in find_books(quran_dir, primary_only=False) if "Tanzil" in p]
    if not files:
        raise FileNotFoundError(f"No Tanzil Quran file under {quran_dir}/data/0001Quran")
    basmala = words("بسم الله الرحمن الرحيم")
    skip = {tuple(basmala[i:i + n]) for i in range(len(basmala) - n + 1)}
    verses, current = {}, None
    with open(files[0], encoding="utf-8-sig") as f:
        for line in f:
            m = re.match(r"#\s*(\d+)\|(\d+)\|\s*(.+)", line)
            if m:
                current = (int(m.group(1)), int(m.group(2)))
                verses[current] = m.group(3)
            elif line.startswith("~~") and current:      # long verses continue on "~~" lines
                verses[current] += " " + line[2:]
            else:
                current = None
    index = defaultdict(list)
    for (sura, aya), verse in verses.items():
        w = words(verse)
        if aya == 1 and sura != 1 and w[:len(basmala)] == basmala:
            w = w[len(basmala):]                         # the file glues the basmala onto aya 1
        for j in range(len(w) - n + 1):
            chunk = tuple(w[j:j + n])
            if chunk not in skip:
                index[chunk].append(((sura, aya), j))
    return dict(index)


# ---------------------------------------------------------------------------
# 4. ANALYSE ONE BOOK  (runs inside worker processes)
# ---------------------------------------------------------------------------
_INDEX, _PATTERNS, _N = {}, {}, 5      # Quran index (empty when Q is off), keyword patterns, chunk size


def _init_worker(index, patterns, n):
    global _INDEX, _PATTERNS, _N
    _INDEX, _PATTERNS, _N = index, patterns, n


def count_quran_quotes(tokens):
    """How many places in this book quote the Quran: runs of >= n identical words shared with
    a verse (one changed word tolerated). One place counts once, even if its words occur in
    several verses or run on across neighbouring verses."""
    n = _N
    diagonals = defaultdict(list)                        # (verse, offset) -> chunk positions
    for i in range(len(tokens) - n + 1):
        for verse, j in _INDEX.get(tuple(tokens[i:i + n]), ()):
            diagonals[(verse, i - j)].append(i)
    spans = []
    for positions in diagonals.values():
        run = [positions[0]]
        for p in positions[1:] + [None]:
            if p is not None and p <= run[-1] + n + 1:
                run.append(p)
                continue
            spans.append((run[0], run[-1] + n - 1))
            run = [p]
    count, last_end = 0, -2
    for start, end in sorted(spans):
        if start > last_end + 1:
            count += 1
        last_end = max(last_end, end)
    return count


def death_ah_of(path):
    name = os.path.basename(path)
    return int(name[:4]) if name[:4].isdigit() else None


def analyze_book(job):
    path, repo = job
    uri = STATUS_SUFFIX.sub("", os.path.basename(path))
    text, page_positions = load_text(path)
    tokens = words(text) if _INDEX else []
    entry = {"uri": uri, "repo": repo,
             "death_ah": death_ah_of(path),
             "words": len(tokens) if _INDEX else None, "quran_quotes": count_quran_quotes(tokens) if _INDEX else None,
             "keyword_counts": {}, "keyword_quotes": {}}
    for label, pattern in _PATTERNS.items():
        quotes, n = [], 0
        for m in pattern.finditer(text):
            n += 1
            if len(quotes) < MAX_QUOTES_PER_BOOK:
                a, b = max(0, m.start() - WINDOW), m.end() + WINDOW
                quotes.append({"page": page_at(page_positions, m.start()), "quote": text[a:b]})
        entry["keyword_counts"][label] = n
        if quotes:
            entry["keyword_quotes"][label] = quotes
    return entry


# ---------------------------------------------------------------------------
# 5. THE ASYNC PIPELINE  ->  JSON
# ---------------------------------------------------------------------------
def make_pool(index, patterns):
    """Worker processes (fork) so books are analysed on all cores; threads where fork does not exist."""
    if "fork" in multiprocessing.get_all_start_methods():
        return ProcessPoolExecutor(WORKERS, mp_context=multiprocessing.get_context("fork"),
                                   initializer=_init_worker, initargs=(index, patterns, QURAN_MIN_WORDS))
    _init_worker(index, patterns, QURAN_MIN_WORDS)
    return ThreadPoolExecutor(WORKERS)

async def run_pipeline(mode=DEFAULT_MODE, keywords=None, start_ah=START_AH, end_ah=END_AH,
                       min_quotes=MIN_QURAN_QUOTES, groups=None):
    """mode: "Q" (Quran quotations), "K" (keywords) or "QK" (both). Returns the results dict
    that is also written to OUTPUT_JSON."""
    keywords = KEYWORDS if keywords is None else keywords
    do_q, do_k = "Q" in mode, "K" in mode
    codes = repos_for_range(start_ah, end_ah)
    if not codes:
        raise ValueError(f"No OpenITI repo covers {start_ah}-{end_ah} AH (repos run 0025AH-1425AH).")
    print(f"Repos for {start_ah}-{end_ah} AH: {', '.join(codes)}")
 
    semaphore = asyncio.Semaphore(MAX_PARALLEL_CLONES)
    wanted = codes + (["0025AH"] if do_q and "0025AH" not in codes else [])   # 0025AH holds the Quran
    dirs = dict(zip(wanted, await asyncio.gather(*(clone_repo(c, semaphore) for c in wanted))))
 
    index = load_quran_index(dirs["0025AH"], QURAN_MIN_WORDS) if do_q else {}
    patterns = ({label: build_pattern(kw, ALLOW_PREFIXES) for kw, label in keywords.items()}
                if do_k else {})
 
    jobs = []
    for code in codes:
        paths = [p for p in find_books(dirs[code], PRIMARY_ONLY) if "0001Quran" not in p]
        paths = [p for p in paths if death_ah_of(p) is None or start_ah <= death_ah_of(p) <= end_ah]
        jobs += [(p, code) for p in (paths[:MAX_BOOKS_PER_REPO] if MAX_BOOKS_PER_REPO else paths)]
    print(f"Analysing {len(jobs)} books with {WORKERS} workers ...")
 
    loop = asyncio.get_running_loop()
    analysed, errors = [], []
    with make_pool(index, patterns) as pool:
        async def one(job):
            try:
                analysed.append(await loop.run_in_executor(pool, analyze_book, job))
            except Exception as e:                       # one bad file must not stop the run
                errors.append({"book": os.path.basename(job[0]), "error": repr(e)})
            done = len(analysed) + len(errors)
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)} books done")
        await asyncio.gather(*(one(j) for j in jobs))
 
    if do_q:     # Q or QK: books quoting the Quran MORE than the threshold, most quotations first
        kept = sorted((b for b in analysed if b["quran_quotes"] > min_quotes),
                      key=lambda b: b["quran_quotes"], reverse=True)
    else:        # K: books with at least one keyword hit, most hits first
        hits = lambda b: sum(b["keyword_counts"].values())
        kept = sorted((b for b in analysed if hits(b) > 0), key=hits, reverse=True)
    results = {
        "meta": {
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": mode, "range_ah": [start_ah, end_ah], "repos": codes,
            "keywords": {label: kw for kw, label in keywords.items()} if do_k else {},
            "groups": groups or {},                      # {keyword label: [approved synonym labels]}
            "allow_prefixes": ALLOW_PREFIXES, "primary_only": PRIMARY_ONLY,
            "min_quran_quotes": min_quotes if do_q else None,
            "quran_min_words": QURAN_MIN_WORDS if do_q else None,
            "books_scanned": len(analysed), "books_kept": len(kept), "errors": errors,
            "note": ("Quran count = places in a book sharing >= quran_min_words identical words in a "
                     "row with a verse (basmala ignored; formulaic phrases such as la ilaha illa llah "
                     "are included). Keyword counts are lower bounds (suffixed forms are not matched). "
                     "Quotes are normalized text."),
        },
        "books": kept,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    what = (f"quote the Quran more than {min_quotes} times" if do_q else "contain a keyword")
    print(f"Saved {OUTPUT_JSON}: {len(kept)} of {len(analysed)} books {what}"
          + (f" ({len(errors)} files failed)" if errors else ""))
    return results
 

def run_async(coro):
    """Runs a coroutine from a terminal OR from Colab/Jupyter (where an event loop is already running)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(1) as ex:                    # notebook: use a fresh loop in a helper thread
        return ex.submit(asyncio.run, coro).result()


def in_notebook():
    return "google.colab" in sys.modules or "ipykernel" in sys.modules

# ---------------------------------------------------------------------------
# 6. JSON  ->  SIMPLE LIST  (exact numbers, computed here, not by the LLM)
# ---------------------------------------------------------------------------
def summarize_results(json_path=OUTPUT_JSON, out_path=OUTPUT_SUMMARY, top=None):
    """Keyword totals first (if keywords were searched), then the books ranked high to low.
    `top` limits how many books are listed; the keyword totals always cover every kept book."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    meta, books = data["meta"], data["books"]
    labels = meta["keywords"]                            # {label: Arabic keyword}
    do_q = "Q" in meta.get("mode", "QK")
 
    lines = [f"Mode: {meta.get('mode', 'QK')} | range: {meta['range_ah'][0]}-{meta['range_ah'][1]} AH | "
             f"repos: {', '.join(meta['repos'])}"]
    if do_q:
        lines += [f"Books scanned: {meta['books_scanned']} | books quoting the Quran more than "
                  f"{meta['min_quran_quotes']} times: {meta['books_kept']}",
                  f"Quran quotation = {meta['quran_min_words']}+ identical words in a row shared with a verse."]
    else:
        lines.append(f"Books scanned: {meta['books_scanned']} | books with a keyword hit: {meta['books_kept']}")
    groups = meta.get("groups") or {}
    member_of = {m: g for g, members in groups.items() for m in members}
    if labels:
        def total(ls):
            return (sum(b["keyword_counts"].get(l, 0) for b in books for l in ls),
                    sum(1 for b in books if any(b["keyword_counts"].get(l, 0) > 0 for l in ls)))
        lines += ["", "KEYWORD TOTALS (books listed below)"]
        for label, kw in labels.items():
            if label in member_of:
                continue                                  # printed under its group
            if label in groups:
                hits, n_books = total([label] + groups[label])
                lines.append(f"- GROUP {kw} ({label}) incl. approved synonyms: {hits:,} hits in "
                             f"{n_books} of {len(books)} books")
                for l in [label] + groups[label]:
                    h, nb = total([l])
                    lines.append(f"    - {labels[l]} ({l}): {h:,} hits in {nb} books")
            else:
                hits, n_books = total([label])
                lines.append(f"- {kw} ({label}): {hits:,} hits in {n_books} of {len(books)} books")
    lines += ["", "BOOKS BY QURAN QUOTATIONS (high to low)" if do_q else "BOOKS BY KEYWORD HITS (high to low)"]
    shown = books[:top] if top else books
    for i, b in enumerate(shown, 1):
        parts = ([f"Quran quotations: {b['quran_quotes']:,}"] if do_q else [])
        parts += [f"{lab} {sum(b['keyword_counts'].get(l, 0) for l in [lab] + groups.get(lab, []))}"
                  for lab in labels if lab not in member_of]
        lines.append(f"{i}. {b['uri']} ({b['death_ah']} AH) - " + "; ".join(parts))
    if top and len(books) > top:
        lines.append(f"... {len(books) - top} more books in {json_path}")
 
    text = "\n".join(lines)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return text
 
# 6b. >>> LLM HELPERS (OpenRouter) + SYNONYM EXPANSION  (new)
#     The LLM only PROPOSES extra search terms. You approve them, and the script then counts
#     every approved term exactly like any other keyword. Only the keywords are sent, no book text.
# ---------------------------------------------------------------------------
def get_api_key():
    try:
        from google.colab import userdata        # Colab secrets
        return userdata.get("OPENROUTER_API_KEY")
    except Exception:
        pass
    return os.environ.get("OPENROUTER_API_KEY") or getpass.getpass("OpenRouter API key: ")
 
 
def call_openrouter(messages, api_key, temperature=0.3):
    import requests                                   # preinstalled in Colab; else: pip install requests
    r = requests.post(
        OPENROUTER_URL, timeout=180,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "X-Title": "OpenITI Quran keyword report"},
        json={"model": OPENROUTER_MODEL, "messages": messages, "temperature": temperature})
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
 
 
def suggest_synonyms(keywords):
    """Ask the LLM for related terms. keywords: {Arabic keyword: label}. Returns {keyword: [terms]}."""
    system = (
        "You are an expert in classical Arabic and in the technical vocabulary of Islamic law and "
        f"related disciplines. For each keyword, list up to {SYNONYMS_PER_KEYWORD} Arabic terms that "
        "classical authors used for the same or a very close concept, including common spelling "
        "variants. Write them without vowel marks and, for nouns, with the article ال like the "
        "keyword. Do NOT include words that only share a root but mean something else (for example "
        "the verb 'he knew' for العرف). No explanations. Answer with JSON only, in this shape: "
        '{"<keyword>": ["<term>", "<term>"]}')
    user = "Keywords: " + json.dumps(list(keywords), ensure_ascii=False)
    reply = call_openrouter([{"role": "system", "content": system}, {"role": "user", "content": user}],
                            get_api_key(), temperature=0.2)
    match = re.search(r"\{.*\}", reply, re.S)             # tolerate ```json fences around the answer
    if not match:
        raise RuntimeError(f"The LLM did not return JSON:\n{reply[:300]}")
    proposals = json.loads(match.group(0))
    clean = {}
    for kw in keywords:
        seen, terms = {normalize(kw)}, []
        for t in proposals.get(kw, []):
            t = " ".join(str(t).split())
            if t and len(t.split()) <= 4 and normalize(t) not in seen:
                seen.add(normalize(t))
                terms.append(t)
        clean[kw] = terms[:SYNONYMS_PER_KEYWORD]
    return clean
 
 
def expand_with_llm(keywords):
    """Show the LLM's proposals, let the user approve them, and return
    (keywords incl. approved terms, groups {keyword label: [synonym labels]})."""
    try:
        proposals = suggest_synonyms(keywords)
    except Exception as e:                                # no key, no network, bad reply: carry on without
        print(f"  Could not get synonyms ({e}). Continuing with your keywords only.")
        return keywords, {}
    expanded, groups = dict(keywords), {}
    for kw, label in keywords.items():
        terms = proposals.get(kw, [])
        if not terms:
            continue
        print(f"\n  {kw}  ->  " + "   ".join(f"{i}. {t}" for i, t in enumerate(terms, 1)))
        answer = ask("  Keep which? numbers like 1,3 | a = all | Enter = none", "").lower()
        picked = terms if answer == "a" else [terms[int(n) - 1] for n in re.findall(r"\d+", answer)
                                              if 0 < int(n) <= len(terms)]
        for i, term in enumerate(picked, 1):
            expanded[term] = f"{label}+{i}"
            groups.setdefault(label, []).append(f"{label}+{i}")
    return expanded, groups

# ---------------------------------------------------------------------------
# 7. COMMAND LINE: ask what to run and what to search
# ---------------------------------------------------------------------------
def ask(prompt, default=""):
    """Ask in the terminal; Enter keeps the default. With no keyboard attached the default is used."""
    shown = f" [{default}]" if default != "" else ""
    try:
        return input(f"{prompt}{shown}: ").strip() or str(default)
    except EOFError:
        return str(default)


def parse_keywords(raw):
    """'العرف=al_urf, ضرورة' -> {'العرف': 'al_urf', 'ضرورة': 'darura'}. Labels are optional."""
    keywords, used = {}, set()
    for i, item in enumerate(re.split(r"[,،]", raw), 1):
        kw, _, label = item.partition("=")
        kw, label = " ".join(kw.split()), label.strip().replace(" ", "_")
        if not kw:
            continue
        label = label or KEYWORDS.get(kw) or f"kw{i}"
        while label in used:
            label += "_"
        used.add(label)
        keywords[kw] = label
    return keywords


def ask_user():
    print("\nWhat should run?")
    print("  Q  = how often each book quotes the Quran")
    print("  K  = keyword search")
    print("  QK = both")
    while True:
        mode = "".join(sorted(set(ask("Your choice (Q / K / QK)", DEFAULT_MODE).upper().replace(" ", "")),
                              reverse=True))
        if mode in ("Q", "K", "QK"):
            break
        print("  Please type Q, K or QK.")
 
    keywords, groups = None, {}
    if "K" in mode:
        print("\nKeywords: Arabic, separated by commas. Write nouns with the article (العرف, not عرف).")
        print("Optional short label after =, e.g.  العرف=al_urf, ضرورة=darura")
        default = ", ".join(f"{kw}={label}" for kw, label in KEYWORDS.items())
        while True:
            keywords = parse_keywords(ask("Keywords", default))
            if keywords:
                break
            print("  Please give at least one keyword.")
        # >>> LLM INJECTION POINT: optional synonym expansion (new)
        if ask("\nAsk the LLM (OpenRouter) to propose synonyms for you to approve? (y/N)", "n").lower().startswith("y"):
            keywords, groups = expand_with_llm(keywords)
 
    print("\nYear range = author DEATH dates in hijri years, e.g. 800-900.")
    while True:
        numbers = re.findall(r"\d+", ask("Year range (AH)", f"{START_AH}-{END_AH}"))
        if len(numbers) == 2:
            start_ah, end_ah = sorted(int(n) for n in numbers)
            if repos_for_range(start_ah, end_ah):
                break
        print("  Please give two years between 1 and 1450, like 800-900.")
 
    min_quotes = MIN_QURAN_QUOTES
    if "Q" in mode:
        print("\nOnly books that quote the Quran MORE than this many times are kept.")
        while True:
            answer = ask("Minimum quotations", MIN_QURAN_QUOTES)
            if answer.isdigit():
                min_quotes = int(answer)
                break
            print("  Please type a whole number.")
 
    print(f"\nRunning {mode} for {start_ah}-{end_ah} AH"
          + (f", keywords: {', '.join(keywords)}" if keywords else "")
          + (f", more than {min_quotes} Quran quotations" if "Q" in mode else "") + "\n")
    return {"mode": mode, "keywords": keywords, "start_ah": start_ah, "end_ah": end_ah,
            "min_quotes": min_quotes, "groups": groups}
 
 
def main():
    results = run_async(run_pipeline(**ask_user()))
    print()
    print(summarize_results())
    return results
 
 
results = main()
