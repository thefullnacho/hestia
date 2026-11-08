"""The library: print furniture stripped, passages cite one page, search can't be steered by syntax."""
from __future__ import annotations

import library_store as lib

HEADER = "Growing Garlic | State Extension | Coll..."


def _web_pages():
    return [
        f"{HEADER}\nGarlic Cultivation\nPublication EXT037 | October 2023\nPlant cloves in fall.\n"
        "10/23/26, 22:50\n1 of 3",
        f"{HEADER}\nMulch with straw after the ground freezes, four to six inches deep, and pull it\n"
        "back in spring when shoots appear.\n10/23/26, 22:50\n2 of 3",
        f"{HEADER}\nHarvest when lower leaves brown.\nReferences\nSmith, J. (2020). Garlic.\n"
        "10/23/26, 22:50\n3 of 3",
    ]


def test_running_headers_stamps_and_references_are_dropped():
    pages = lib.clean_pages(_web_pages())
    assert len(pages) == 3
    assert all(HEADER not in p and "of 3" not in p and "22:50" not in p for p in pages)
    assert pages[2] == "Harvest when lower leaves brown."
    assert "Smith" not in " ".join(pages)


def test_passages_never_cross_a_page_and_short_tails_merge():
    long_page = " ".join(f"w{i}" for i in range(lib.CHUNK_WORDS + 10))
    parts = lib.chunks([long_page, "short page"])
    assert [p for p, _ in parts] == [1, 2]   # a 50-word tail folds into page 1's one passage
    assert parts[0][1].endswith(f"w{lib.CHUNK_WORDS + 9}")


def test_long_page_overlaps_windows():
    page = " ".join(f"w{i}" for i in range(lib.CHUNK_WORDS * 2))
    parts = lib.chunks([page])
    assert len(parts) == 3
    first, second = parts[0][1].split(), parts[1][1].split()
    assert first[-lib.OVERLAP_WORDS:] == second[:lib.OVERLAP_WORDS]


def test_titles_from_the_pdf_or_the_file_name():
    assert lib.title_of("x.pdf", "Growing Garlic | State Extension") == \
        "Growing Garlic"
    assert lib.title_of("POWDERY-MILDEW.pdf", "PowerPoint Presentation") == "Powdery Mildew"
    assert lib.title_of("Tick-Control.pdf", None) == "Tick Control"
    assert lib.title_of("a.pdf", "LANDSCAPING FOR WILDLIFE") == "Landscaping For Wildlife"


def test_publication_number_and_year():
    assert lib.meta_of("Publication EXT177 | January 2026") == ("EXT177", 2026)
    assert lib.meta_of("Publication # EXT037 |October 2023") == ("EXT037", 2023)
    assert lib.meta_of("© 2004 The Station") == (None, 2004)
    assert lib.meta_of("no dates here") == (None, None)


def test_index_and_search_cite_title_year_and_page(db):
    lib.index_doc("garlic.pdf", _web_pages(), f"{HEADER}")
    hits = lib.search("when should I mulch garlic")
    assert hits[0]["page"] == 2 and hits[0]["year"] == 2023 and hits[0]["pub"] == "EXT037"
    assert hits[0]["title"] == "Growing Garlic"


def test_reindex_replaces_and_one_document_cannot_flood_results(db):
    pages = [f"garlic page {n} " + "garlic " * 50 for n in range(3)]
    lib.index_doc("a.pdf", pages, "Doc A here")
    lib.index_doc("a.pdf", pages, "Doc A here")
    assert len(lib.docs()) == 1 and lib.docs()[0]["passages"] == 3
    assert len(lib.search("garlic", k=5)) == lib.PER_DOC


def test_question_text_is_never_fts_syntax(db):
    lib.index_doc("a.pdf", ["Check for ticks on dogs after every walk in tall grass and brush, then again at bedtime."], "Ticks here")
    assert lib.fts_query('ticks" OR NEAR(* -dogs') == '"ticks" OR "near" OR "dogs"'
    assert lib.search('ticks" OR NEAR(* -dogs')
    assert lib.search("the and of") == []


def test_an_early_resources_heading_does_not_end_the_document():
    pages = ["Contents\nResources\nGrowing", "Resources\nPlant in spring.", "Prune in winter.",
             "Harvest in fall.", "References\nSmith 2020"]
    out = lib.clean_pages(pages)
    assert "Plant in spring." in out[1] and out[3] == "Harvest in fall." and out[4] == ""


def test_manifest_fixes_title_and_year_and_skips_unreadable_files(db, tmp_path, monkeypatch):
    (tmp_path / "library.toml").write_text(
        '["a.pdf"]\ntitle = "Real Title"\nyear = 2015\n\n["b.pdf"]\nskip = true\n')
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / name).write_bytes(b"%PDF")
    monkeypatch.setattr(lib, "_read_pdf", lambda path: (["Some text here. " * 10], "slug"))
    monkeypatch.setattr(lib, "embed_missing", lambda: 0)
    report = lib.ingest(tmp_path)
    assert report["skipped"] == ["b.pdf"]
    assert [(d["title"], d["year"]) for d in lib.docs()] == [("Real Title", 2015)]


def test_junk_passages_are_not_kept_but_a_phone_scan_always_is(db):
    pages = ["12", "Figure 2. CORRECT CUT 11",
             "Contents Composting ........ 3 Mulch ........ 5 Watering ........ 7 Pests ........ 9",
             "Turn the pile every two weeks so the middle heats and the outside gets its turn too."]
    lib.index_doc("guide.pdf", pages, "Guide here")
    assert [p["page"] for p in lib.search("turn the pile", k=5)] == [4]
    assert lib.docs()[0]["passages"] == 1
    lib.index_doc(lib.SCAN_PREFIX + "abcdef12", ["Cert 42"], fix={"title": "Shares"})
    assert next(d for d in lib.docs() if d["scanned"])["passages"] == 1


def test_a_missing_character_mapping_reads_as_a_space():
    assert lib.clean_pages(["Recommended\ufffdprocess\ufffdtime for Tuna"]) == \
        ["Recommended process time for Tuna"]


def test_a_book_in_several_files_is_one_source(db):
    text = ["garlic " * 80]
    for f in ("guide1.pdf", "guide2.pdf", "guide3.pdf"):
        lib.index_doc(f, text, f"{f} title here", fix={"source": "Canning Book"})
    lib.index_doc("other.pdf", text, "Other garlic guide")
    got = lib.search("garlic", k=5)
    assert len(got) == lib.PER_DOC + 1          # the book counts once, the other guide too
    assert {d["source"] for d in lib.docs()} == {"Canning Book", None}
