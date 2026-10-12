# OpenITI Keyword Finder: How to Run It

This script reads a collection of classical Arabic books, finds the words you choose, and gives you:

- **`keyword_report.md`**: a readable report (counts per book, with short quotes and page markers).
- **`keyword_occurrences.json`**: the same findings in a machine-friendly file, ready to give to an AI tool.
- **`top_books_<label>.png`**: an optional bar chart (one per keyword).

You do not need to understand the code. You only edit a few lines at the top, in the section called **SETTINGS**.

---

## Part 1. Run it in Google Colab (easiest, nothing to install)

1. Go to <https://colab.research.google.com> and sign in with a Google account.
2. Click **New notebook**.
3. Open `openiti_keyword_monolith.py` in any text editor, select everything, and copy it.
4. Paste it into the first code cell of the notebook (the box with a ▶ button).
5. Edit the SETTINGS (see Part 3 and Part 4).
6. Click the ▶ button. The first run downloads the books (about 445 MB) and takes a few minutes. Later runs on the same session are faster because the books stay downloaded.
7. When it finishes, click the **folder icon** on the left. Your files appear there. Right-click a file and choose **Download**.

> **Important:** Colab erases its files when the session ends. Download your results before closing the tab.

---

## Part 2. Run it in a terminal or IDE (VS Code, etc.)

**One-time check.** Open a terminal (in VS Code: menu *Terminal → New Terminal*) and type:

```
python --version
git --version
```

Both should print a version number. If not, install Python (python.org) and Git (git-scm.com). On Mac or Linux, use `python3` instead of `python`. On Windows, try `py` if `python` does not work.

**Optional, for the chart only:**

```
pip install matplotlib
```

**Run:**

1. Put `openiti_keyword_monolith.py` in a folder, for example `Documents/keyword-finder`.
2. In the terminal, go to that folder:
   ```
   cd Documents/keyword-finder
   ```
3. Edit the SETTINGS with any editor (see Part 3 and Part 4), and save.
4. Run:
   ```
   python openiti_keyword_monolith.py
   ```
5. The result files appear in the same folder.

> If a chart window pops up, close it to let the script finish. The PNG file is already saved.

---

## Part 3. What to change

Open the script. At the top you will see the SETTINGS block. Change only the text **inside the quotation marks** (or the number or `None`). Keep the quotation marks and the commas.

### A. Change the source (which books)

```python
REPO_URL = "https://github.com/OpenITI/0850AH"
LOCAL_CORPUS_DIR = "./openiti_corpus_0850"
```

- `REPO_URL` is the online collection to download. OpenITI splits its books into repositories by time period. `0850AH` holds authors who died around 825–850 AH. Other periods are on the OpenITI GitHub page (<https://github.com/OpenITI>) and use the same naming pattern, such as `0900AH`. Check that the page exists before using it.
- `LOCAL_CORPUS_DIR` is the folder where the download is kept.
- **When you change `REPO_URL`, also change `LOCAL_CORPUS_DIR`** to a new name, such as `"./openiti_corpus_0900"`. Otherwise the script sees the old folder, thinks it is already downloaded, and searches the old books.

To use books you already have on your computer, put the OpenITI folder on your computer, set `LOCAL_CORPUS_DIR` to its path, and the script will use it without downloading. The folder must contain a `data` folder inside it.

### B. Change the keywords

```python
KEYWORDS = {
    "العرف": "al_urf",
    "ضرورة": "darura",
}
```

Each line has the form `"Arabic word": "short_english_label",`. See Part 4 for how to write them well.

### C. Change the output

```python
OUTPUT_JSON = "keyword_occurrences.json"
OUTPUT_MD = "keyword_report.md"
REPORT_TOP_BOOKS = 15
```

| Setting | What it does | Example |
|---|---|---|
| `OUTPUT_JSON` | Name of the JSON file | `"my_results.json"` |
| `OUTPUT_MD` | Name of the report. Use `None` (no quotes) for no report | `None` |
| `REPORT_TOP_BOOKS` | How many books the report lists per keyword | `30` |

### D. Other useful settings

| Setting | What it does | Default |
|---|---|---|
| `MAX_BOOKS` | Only scan the first N books. Use `20` for a quick test, `None` for all | `None` |
| `WINDOW` | How many characters of context to show on each side of a hit | `60` |
| `MAX_QUOTES_PER_BOOK` | How many sample quotes to keep per keyword per book | `3` |
| `ALLOW_PREFIXES` | `True` also finds forms like والعرف and بالعرف. `False` finds only the exact word | `True` |
| `PRIMARY_ONLY` | `True` skips duplicate digital copies of the same book | `True` |

To turn off the chart, delete (or put a `#` at the start of) the last two lines of the script:

```python
    for label in results["totals"]:
        plot_top_books(results, label)
```

---

## Part 4. How to write a keyword clearly

**Rule 1: Type plain Arabic letters. You do not need vowel marks.**
The script removes vowel marks (ـَ ـُ ـِ ـّ) from both your keyword and the books, and treats أ إ آ ا as the same letter, and ى and ي as the same letter. So `العُرْف` and `العرف` are the same.

**Rule 2: Write the word the way it appears in the books, with its article if it is a noun.**
`العرف` (custom) and `عرف` (he knew) look alike without vowel marks. If you search for `عرف`, you will also count the verb "he knew", and most of your results will be wrong. In a test on one corpus, `عرف` gave about 4,274 hits, mostly the verb. `العرف` gave 838 hits, and the ones I read were the legal meaning.

**Rule 3: One keyword per line, inside quotation marks, followed by a comma.**

```python
KEYWORDS = {
    "العرف": "al_urf",
    "الضرورة": "al_darura",
    "المصلحة": "maslaha",
}
```

**Rule 4: For a phrase, put a normal space between the words.**
`"اهل الحل والعقد": "ahl_hall"` works. The script finds the phrase even if the book breaks the line in the middle of it.

**Rule 5: The label (after the colon) is plain English letters, with no spaces.**
Use letters, numbers and underscores only. It becomes the name in the JSON file and the report. Each label must be different.

**Rule 6: Use `ALLOW_PREFIXES` to catch attached prefixes.**
With `ALLOW_PREFIXES = True`, searching `ضرورة` also finds `الضرورة`, `للضرورة`, `وضرورة` and `بالضرورة`. This helps, but it can also bring in unwanted forms. Always read some quotes.

**Rule 7: The script does not find suffixes or other forms.**
It will not find `عرفه` (with "his") or `ضرورات` (plural). If you need them, add them as extra lines:

```python
    "ضرورة": "darura",
    "ضرورات": "darurat",
```

So the counts are **at least** this many, never more than the true total.

**Common mistakes**

| Mistake | Fix |
|---|---|
| `"العرف" "al_urf"` (missing colon) | `"العرف": "al_urf",` |
| Missing comma at the end of a line | Add `,` |
| Missing closing `}` | The block must end with `}` on its own line |
| Smart quotes `“ ”` copied from Word | Use straight quotes `" "`. Type them again in the editor |
| Same label used twice | Give each keyword its own label |

---

## Part 5. Reading the results

- Open `keyword_report.md` first. In VS Code, press `Ctrl+Shift+V` to see it formatted. Or paste it into any Markdown viewer.
- **The script finds candidates. You confirm them.** Read the quotes before using any count in your writing, because a word can have different meanings in different books.
- Quotes are shown without vowel marks, because the text was cleaned before searching. The page marker (for example `V01P074`) points to volume 1, page 74 in the OpenITI text, which is not always the same as a printed edition's page number.
- A page shown as `null` or "page unknown" means the hit came before the first page marker in that book.

---

## Part 6. If something goes wrong

| Message or problem | What to do |
|---|---|
| `git clone failed` | Check the internet connection and that `REPO_URL` is spelled correctly. |
| `No such file` or 0 books scanned | Check that `LOCAL_CORPUS_DIR` has a `data` folder inside it. If you changed `REPO_URL`, delete the old folder or use a new folder name. |
| `SyntaxError` | A quote, comma, colon or bracket is missing in the keyword block. Compare it with the example in Part 4. |
| Zero hits | Try `ALLOW_PREFIXES = True`, check the spelling, or try a different time period. |
| It is slow | The first download is large. Use `MAX_BOOKS = 20` to test first. |
| `ModuleNotFoundError: matplotlib` | Run `pip install matplotlib`, or turn off the chart (Part 3D). |
| `python` not recognized (Windows) | Try `py openiti_keyword_monolith.py`. |

To start over with a fresh download, delete the corpus folder (`openiti_corpus_0850` or whatever you set) and run again.
