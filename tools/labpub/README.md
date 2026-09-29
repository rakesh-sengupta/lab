# labpub

One publication list, `data/publications.json`, feeds both the lab website and
your CV. `labpub` keeps it up to date. Python 3.8+ standard library only;
nothing to `pip install`.

## One-time setup (Linux)

```bash
cd ~/path/to/lab                       # your clone of rakesh-sengupta/lab
chmod +x tools/labpub/labpub.py
mkdir -p ~/.local/bin
ln -sf "$PWD/tools/labpub/labpub.py" ~/.local/bin/labpub
labpub check                           # should print: OK — 46 entries …
```

If `labpub` isn't found afterwards, add `export PATH="$HOME/.local/bin:$PATH"`
to `~/.bashrc`.

Then tell it where your CV lives, in `tools/labpub/config.json`:

```json
"cv_publications_tex": "~/Documents/cv/publications.tex",
```

## Everyday use

```bash
labpub add 10.1007/978-3-032-31170-2_10    # from a DOI: looks it up on Crossref
labpub add --manual                         # no DOI yet (in press, IGI chapters…)
labpub cv                                   # rewrite the CV's publications.tex
labpub push                                 # check, commit, push; the site updates in ~1 min
```

`add` shows the entry as it will look on the website and in the CV, asks for
the website filter categories and whether it is a flagship paper, and lets you
edit any field before saving. It offers to convert Crossref's Title Case to
sentence case, keeping acronyms and the names listed under `keep_case` in the
config.

Then compile the CV as usual (`latexmk -xelatex main.tex`). `main.tex` pulls in
the generated file with `\input{publications}`.

Other commands:

```bash
labpub list                   # everything, newest first (* = flagship)
labpub list --type journal
labpub orcid                  # works on your ORCID record that aren't listed yet
labpub orcid --apply          # … and add them, one question per work
labpub check                  # validate; also lists TODOs and unreviewed entries
labpub push --no-push         # commit only
```

## The weekly ORCID check

`.github/workflows/orcid-check.yml` runs every Monday at 09:00 IST. If your
ORCID record has something the list doesn't, it adds it on the branch
`labpub/orcid-check` and opens a pull request titled *New publications found
on ORCID*. Nothing goes live until you merge it. If you leave a PR open, later
weeks update that same PR rather than opening new ones.

Automatically added entries carry `"review": true`. Before merging, open
`data/publications.json` in the PR (GitHub's pencil icon works), check authors,
title case, venue and `cats`, then delete the `"review": true` line. You can
also merge first and fix locally; `labpub check` lists anything still unreviewed.

**Required once:** Settings → Actions → General → *Workflow permissions*:
choose *Read and write permissions* and tick *Allow GitHub Actions to create
and approve pull requests*. To try it immediately: Actions tab → *Weekly ORCID
publication check* → *Run workflow*.

The check is only as complete as your ORCID record. Adding works to ORCID
(including via its Crossref auto-update) is what feeds it.

## The data file

Each entry looks like this; fields that don't apply are simply left out.

```json
{
  "id": "sengupta2027long",
  "type": "proceedings",
  "year": 2027,
  "authors": ["R. Sengupta"],
  "title": "Long-range temporal correlations in enumeration: …",
  "venue": "ComSIA 2026",
  "series": "Lecture Notes in Networks and Systems",
  "volume": "2058",
  "publisher": "Springer",
  "doi": "10.1007/978-3-032-31170-2_10",
  "cats": ["numerosity", "theory"],
  "flagship": false,
  "web": true
}
```

| field | meaning |
|---|---|
| `type` | `journal`, `proceedings`, `chapter`, `book` or `edited` |
| `status` | e.g. `"in press"`, `"accepted"`; shown instead of the year, and puts the item in the CV's *In press* list |
| `authors` | `"R. Sengupta"` style. Your name is bolded; names in `students` get the CV dagger |
| `venue` | journal name, or short conference name such as `ICCCNT 2024` |
| `book`, `editors` | for chapters |
| `cats` | website filters: `numerosity vwm time bci theory quantum society` |
| `flagship` | shows the Flagship tag and appears under that filter |
| `web` | `false` keeps it on the CV but off the website |
| `cv_section` | override where it goes in the CV (`press`, `books`, …) |
| `note` | starts with `TODO:` → flagged by `labpub check` and copied into the CV source as a comment |

Hand edits are fine; run `labpub check` afterwards. Saving through `labpub`
never reorders untouched entries, so diffs stay small.

## Tests

```bash
python3 tools/labpub/test_labpub.py
```

These use recorded Crossref and ORCID responses, so they run offline.
