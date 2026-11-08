# Library: grounded answers from extension publications

Status: designed 2026-10-23; built 2026-10-24. The `library` tool searches by EmbeddingGemma
vectors (keyword when the embedder is down), code appends the source when an answer names none,
and code adds the pharmacist line to dose and stopping questions. A similarity floor
(0.38) turns away questions the shelf clearly doesn't cover. The inventory join is built (2026-11-05, INVENTORY.md, "The grounded answer"): code attaches what the house has on hand among the things the passages name.

## The idea

Hestia answers garden, soil and outdoor-skills questions from model memory today. The model is
often right, sometimes confidently wrong, and it never says where an answer came from. University
extension services publish exactly the material a homestead needs: tested, regional, free to read.
A local library of those publications, searched by code and cited in every answer, turns "the
model thinks" into "the extension says, page 4".

Joined with the inventory (tools, seeds, products, what is planted, all already rows in
`records_store`), the brain can answer the questions that matter most: not "how do you treat
powdery mildew" but "what can I do about the mildew on the squash with what I already have".

This keeps the project's split. Code finds the passages and the inventory rows. The model
reasons over what code handed it, and cites it.

## Shape

- **Corpus:** PDFs chosen by hand, stored under `data/library/` (gitignored). Publications are
  free to read but not licensed for redistribution, so nothing from them enters the repo. A
  tracked `library.example.toml` shows the manifest format; the real manifest is gitignored.
- **Manifest, one entry per document:** title, institution, publication number, year, region
  (state, and hardiness zone where it applies), topic, source URL. Region and year are not
  decoration: they rank results and travel with every citation.
- **Ingest (offline, by hand):** extract text per page, chunk by heading or about 300 words with
  overlap, keep doc and page on every chunk. Store in `hestia.db` beside the other stores.
- **Search is hybrid:** SQLite FTS5 (BM25) plus EmbeddingGemma vectors, merged by reciprocal rank.
  Extension text is heavy with exact terms (species names, products, "Brix"), where keyword search
  wins; vectors carry the "my squash leaves have white powder" phrasing. EmbeddingGemma runs on
  the CPU, so the 5080 stays the brain's, and it is the same embedder as the small router.
  Results from the household's own region rank first.
- **Tool:** `library` (read-only) returns the top passages with title, institution, year and
  page. A scoping skill routes garden and outdoor questions to it, as `garden_bed` does today.
- **Citations are checked in code:** an answer built on library passages must name a source it
  was given. A cited source that was not in the passages is caught the same way an unbacked
  "logged it" is caught today.
- **Inventory join:** for "with what I have" questions, code pulls matching inventory rows
  (products, tools, seeds) alongside the passages. No vectors needed for that half.

## Rules the answers keep

- **Regulations carry a year.** Hunting, trapping and fishing rules are state law and change
  every season. Passages about them are cited with the publication year and the answer says to
  check the current regulations.
- **No "safe to eat" from retrieval.** For wild edibles and mushrooms, Hestia cites and describes.
  It never clears something as edible on the strength of a passage.
- **Medication is cited, never advised.** Emergency-preparedness publications cover keeping a
  supply of life-sustaining medication through an outage. Hestia cites what they say about
  planning and storage, and sends dose, substitution or stopping questions to a pharmacist or
  doctor. Code backs this, not just the prompt.
- **Out-of-region advice is labelled** as such, not presented as local.

## Network

Nothing in the hot path. The corpus is downloaded once, by the operator, from publications they
picked; the brain never fetches a document and the model never adds one. See SECURITY.md,
"The library".

## Documents from the phone

Added 2026-11-05. A household files its own papers from the iPhone app: the app reads the pages
with Apple's on-device text recognition and posts only the text (PHONE-APP.md, "Documents into
the library"). The box files it through the same `index_doc` as the PDF shelf, so chunking,
keyword and vector search, and citations by title and page all work unchanged. No model.

- Each is kept as `phone:<the app's id>`; the folder ingest never removes those, since they are
  not files in `data/library/`. A phone removes only documents a phone filed.
- A resend with the same id replaces the document.
- If the embedder is down when a document arrives, it is filed and keyword search finds it, the
  phone is told, and the next ingest embeds it. Until then vector search is off for the whole
  library (`_load_vectors` refuses a partial index), the same rule as the PDF ingest.
- Household papers are not extension publications: the similarity floor (0.38) was tuned on
  the operator's shelf, so watch the first real questions about manuals and warranties.
- Routing sends warranty, manual, serial or model number, instructions and user guide
  questions to the library.

## First slice

1. 20 to 30 PDFs from the household's own state extension: what is in the ground now, soil
   biology and testing, the pests already on record.
2. 30 real questions, taken from chat history and the utterance log, each with the passage a
   good answer needs. Written before any search code, never tuned against.
3. Ingest, the `library` tool, keyword-only search first.
4. Score keyword-only against hybrid on the 30, at K>=5. Hybrid earns its dependency only if it
   clearly wins.
5. Then the inventory join, then the wider corpus (survival skills, camping, hunting, trapping).

Steps 1 and 2 are the operator's: picking sources and writing questions needs someone who knows
the garden.

## Search eval, 2026-10-24

128 questions written by the operator without seeing results (`brain/eval_library.py`; the
questions stay in the gitignored library folder). 56 documents, 556 passages.

| Method | Right document first | In top 3 | In top 5 | MRR |
|---|---|---|---|---|
| Keyword (FTS5, BM25) | 89 (70%) | 110 | 113 (88%) | 0.78 |
| Vectors (EmbeddingGemma) | 113 (88%) | 127 | 128 (100%) | 0.94 |
| Hybrid (reciprocal rank) | 105 | 122 | 122 | 0.89 |

Vectors win outright, and fusing keyword in makes them worse, so the brain gets vectors alone.
Most first-place "misses" are a sibling document that also answers (the second composting
sheet, the bee-planting guide for a bee question). Real ones: fungicide for marigolds found
the rose guide, and "unusual fruits to grow" found the climate-resiliency guide. Limits: the
questions were written by someone who knew the titles; there were no `-> none` questions, so
when to say "not in the library" is untested; and this grades retrieval, not answers. At this
size (556 x 768) a plain in-memory cosine search is enough; no vector database.

## Not in the library, 2026-10-24

11 `-> none` questions. Best-match similarity: real questions lowest 0.412 (median 0.613);
clearly off-shelf ones (tobacco, mushrooms, soap, greenhouse automation) 0.35 to 0.37. Near
misses score like real questions: foraging rules finds the chicken-permit sheet at 0.55, pond
nutrients the wildlife-landscaping sheet at 0.51. So the floor is set low, at 0.38, to catch only
the clearly off-shelf; turning away a real question costs more than letting a near miss through.
The model's reading is the second gate, and end to end it mostly holds: pond and raspberry
varieties answered "the library doesn't cover that", drip irrigation found the clogged-emitter
advice, tomatoes in raised beds found real transplanting advice in "Planting Your Garden". Two
weak spots: hydrangea leaf curl was answered from the houseplant and apple-aphid sheets without
saying they are about other plants, and "how do I bug in" was read as breaking into a house
(preparedness vocabulary the model lacks). Code no longer appends a source to an answer that
says the library doesn't cover the question.

## Second batch, 2026-10-24

20 more documents from federal sources and neighboring extensions: the Army survival manual
(ATP 3-50.21), the USDA Complete Guide to Home Canning (8 parts), CDC backcountry water
treatment, FEMA's Are You Ready?, and sheets on wild edibles, herbs, elderberries, maple
sugaring, snakebite and emergency drinking water. 75 documents, 1,799 passages. One file was
skipped: its fonts scramble the text layer, and there is no OCR on the box.

Found on the way and fixed: a "Resources" or "References" line early in a document (a table of
contents) cut off everything after it. A 102-page guide had been reduced to 3 passages, and
three first-batch sheets were clipped too. Reference lists now only end the text in the back 40%
of a document. Titles and years the PDFs get wrong are corrected in a private
`data/library/library.toml` (format in `deploy/library.example.toml`).

On 178 questions: vectors put the right document first 81% of the time and in the top 5 for 177;
keyword 70% and 90%. Most first-place misses are a sibling that also answers (the canning guides
overlap, three sources cover boiling water). The cost of the bigger shelf: long documents win
first place more often, so the first batch's questions slipped from 113 to 110 first-place hits,
with the top 5 unchanged at 128 of 128. The 0.38 floor still loses no real question.

Wild food is now code-backed like medication: an edibility question gets a fixed line that the
identification must be confirmed in person. End to end, "how do I bug in" (read as a break-in
before the preparedness sources) now answers from FEMA and the medical-needs sheet.

## Cleaning and scale, 2026-11-07

A pass comparing every passage with every other (`library_store.py overlap`, no model) found
three problems in the 75-document shelf:

- **Junk passages:** 82 of 1,799 were page numbers, figure captions, title scraps and tables
  of contents, embedded like real text. A passage now needs 60 letters and no run of dot
  leaders to be kept. Phone scans are exempt: a short certificate is the whole document.
- **A missing character mapping:** pdftotext writes U+FFFD where a font maps no character, and
  in the canning guides that was every space in the process-time tables (140 passages).
  `clean_pages` reads it as a space.
- **A book split into files:** the canning guide is 8 PDFs, so it could fill every result slot.
  The manifest's `source` names them one source, and the two-per-source limit counts sources.

Re-indexing the whole shelf after the change took 27 seconds on the GPU box. On the 200
questions: vectors unchanged (145 of 178 first, 177 in the top five), keyword one better.

Search was one Python loop over every passage per question: 45 ms here at 1,800 passages, but
about 0.8 s and over a gigabyte at 50,000 (a shelf of books), several times that on a Pi. It is
now one float32 matrix product: 13 ms and 154 MB at 50,000.

The overlap report also shows where the shelf repeats itself (two bee guides, two composting
guides, two tick guides, drought and irrigation, the bedding-plant pest and fungicide guides,
the survival manual and the drinking-water sheet). Those pairs seed the multi-source questions
for the answer eval: a good answer there needs both guides.
