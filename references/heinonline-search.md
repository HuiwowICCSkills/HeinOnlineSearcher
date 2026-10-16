# HeinOnline search syntax and multi-field advanced search

Verified 2026-10-16 on the live site through the GDUFS LibVPN session
(`gdufs-auth-session` + the shared headless-Chrome helper). HeinOnline states it
runs on a Lucene/SOLR search platform (LibGuide, "Advanced Search Syntax Guide").

Sources re-read while learning this:
- The in-product **Search Help** panel (`-T- Search Syntax`) on `HOL/Welcome` and
  `HOL/LuceneSearch`.
- Knowledge Base, "Using Advanced Search Syntax":
  <https://help.heinonline.org/kb/advanced-search-syntax/>
- LibGuide, "Advanced Search Syntax Guide":
  <https://libguides.heinonline.org/searching/advanced-search-syntax-guide>

## Search-bar symbols (Search Help)

The adapter submits the query string verbatim, so these symbols can be used
directly, e.g. `search 'command responsibility NOT Bemba' --channels heinonline`.
Boolean operators must be capitalised in the search bar.

| Symbol | Meaning | Example |
| --- | --- | --- |
| `AND`, `&&` | both terms must appear | `evidence AND admissibility` |
| `OR` | either term may appear | `brief OR evidence` |
| `NOT`, `!` | exclude the following term | `"presidential impeachment" !clinton` |
| `+` | the term after `+` must appear | `+nuclear energy` |
| `-` | exclude the term after `-` | `whistleblower -"donald trump"` |
| `"..."` | exact phrase | `"love canal"` |
| `( )` | group clauses / sub-queries | `(watershed OR "water rights") AND planning` |
| `~N` | phrase proximity, words in any order | `"uber transportation law"~10` |
| `w/N` or `/N` | two single terms within N words | `jury w/5 discrimination` |
| `w/s` or `/s` | same sentence | `uber w/s transportation` |
| `w/p` or `/p` | same paragraph (defined as ~75 words) | `uber w/p transportation` |
| `w/seg` or `/seg` | same segment (defined as ~100 words) | `uber w/seg transportation` |
| `?` | single-character wildcard | `te?t` -> text, test |
| `*` | multi-character wildcard | `brief*` -> brief, briefs, briefed |
| `atleastN("term")` | term occurs at least N times | `discrimination AND atleast10("law firm")` |
| `^N` | boost a term's relevance | `"native American economy"^4 "cherokee tribe"` |
| `~`, `~0.8` | fuzzy match and its similarity parameter | `roam~0.8` |
| `[A TO B]`, `{A TO B}` | inclusive / exclusive range, optionally fielded | `date:[1938 TO 1944]`, `title:{Aida TO Carmen}` |
| `title:` and friends | field-scoped or field-grouped terms | `title:(+ King + "civil rights")` |

Documented caveats:
- `atleast`: `*` or `?` cannot be used inside it; the operator itself may be
  upper or lower case; `AND`/`OR` may be used inside.
- `"..."~N`: no other syntax inside the quotes.
- `w/`: exactly two single terms; not phrases, not more than two terms.

## Multi-field advanced search

- Page: **Advanced Search** on any search page ->
  `HOL/LuceneSearch?collection=all&searchtype=open`.
- "Search HeinOnline" rows (up to six) use:
  - field selects `typea`..`typef` with values `title` (Section Title),
    `voltitle` (Book Title), `creator` (Author), `text` (Full Text);
  - term inputs `termsa`..`termsf`;
  - between-row operator selects `operator`, `operatorb`..`operatore` with
    values `AND`, `OR`, `NOT`, ` ~5`, ` ~10`, ` ~25`;
  - optional year range `yearlo`/`yearhi`;
  - hidden `searchtype=field`, `collection=all`, `submit=Go`; the form submits by
    GET back to `HOL/LuceneSearch`.
- The catalog rows above it are a separate publication-level form posting to
  `HOL/TitleLookUp`.
- The single search box on the welcome page instead submits
  `terms=...&collection=all&searchtype=advanced&typea=text&tabfrom=&submit=Go&sendit=&all=true`
  and accepts the same symbols.

### Using it from the adapter

```powershell
& '<skill-dir>/run.ps1' advanced --field 'title=command responsibility' --field 'text=Article 28' --join AND --year-lo 2005 --year-hi 2015 --limit 5 --out './hein-advanced.json'
```

`build_advanced_url()` turns up to six `{field, terms, join}` rows into the GET
URL; `search_advanced()` renders it through the same browser session and parses
the first result page. Report fields: `mode: "advanced"`, `requested_url`,
`query_echo` (the query as HeinOnline interpreted it), `total`, `results`,
`warnings`, `pagination`.

Between-field operators supported by the adapter: `AND`, `OR`, `NOT`. The form
additionally offers Within 5/10/25, but on 2026-10-16 both direct-URL encodings
failed at All Databases scope (space-encoded value re-rendered the search form;
`+`-encoded value returned `504 Gateway Time-out`), so the adapter does not
expose it. For proximity, combine both terms inside one field and use `w/N` or
`"..."~N` in that field's value.

### Manual URL shape

    https://access.heinonline.com/HOL/LuceneSearch?termsa=...&typea=title&operator=AND&termsb=...&typeb=text&yearlo=&yearhi=&searchtype=field&collection=all&submit=Go

Through LibVPN, render the entry form instead:
`https://libvpn.gdufs.edu.cn/-https://access.heinonline.com/HOL/LuceneSearch?...`.

## Live verification (2026-10-16, All Databases, GDUFS session)

| Query | Where | Result-page echo | Hits |
| --- | --- | --- | --- |
| `effective control` | search bar | `(effective control)` | 1,767,800 |
| `"effective control"` | search bar | `("effective control")` | 66,096 |
| `effective OR control` | search bar | `(effective OR control)` | 4,311,890 |
| `command responsibility` | search bar | `(command responsibility)` | 337,084 |
| `command responsibility NOT Bemba` | search bar | `(command responsibility NOT Bemba)` | 336,037 |
| `command responsib*` | search bar | `(command responsib*)` | 438,654 |
| `command w/5 responsibility` | search bar | `(command w/5 responsibility)` | 11,673 |
| title=`command responsibility` AND text=`Article 28` | advanced | `((title:(command responsibility) AND (Article 28)))` | 151 |
| title=`command responsibility` OR text=`Article 28` | advanced | `((title:(command responsibility) OR (Article 28)))` | 1,722,818 |
| title=`command responsibility` NOT text=`Article 28` | advanced | `((title:(command responsibility) NOT (Article 28)))` | 22 |

Counts are one-day snapshots and change as content grows; the point is that each
operator visibly redefined the result set. `query_echo` in adapter reports is
the way to confirm how a query was interpreted.

## Boundaries

- All Databases scope (`collection=all`). The database-selection modal carries
  its own `face_quers` parameters; its exact request form was not verified.
- The adapter returns the first result page only; deeper pagination is not
  implemented.
- Search rows are metadata and summaries, not full text; full text remains a
  separate `download` step.
- Range queries, fuzzy, boost, `atleast` and field grouping are documented by
  HeinOnline and quoted above, but were not each re-tested live in this pass;
  the verified set is the table above.
