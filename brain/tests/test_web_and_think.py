"""Web answers name their sources, and the app's thinking button makes one turn think (Ollama only)."""
import asyncio

import backend
import guards
import hestia
from tools import search

RESULTS = ("Top web results for 'garlic':\n"
           "1. Growing Garlic | UMN Extension\n   https://extension.umn.edu/garlic\n   Plant in fall.\n"
           "2. Garlic - Wikipedia\n   https://en.wikipedia.org/wiki/Garlic\n   A species.\n"
           "3. Garlic tips\n   https://example.com/g\n   x\n"
           "4. More garlic\n   https://example.org/m\n   y")


def test_sources_from_results_and_from_a_read_page():
    assert guards.web_sources(RESULTS)[0] == ("Growing Garlic | UMN Extension", "https://extension.umn.edu/garlic")
    assert guards.web_sources("Readable text from https://a.org/x:\nbody") == [("", "https://a.org/x")]
    assert guards.web_sources("No web results for 'q'.") == []
    assert guards.web_sources("Error: fetch is limited to public web pages") == []


def test_addendum_names_up_to_three_and_a_read_page_first():
    srcs = guards.web_sources(RESULTS)
    line = guards.web_addendum("Plant cloves in October.", srcs)
    assert line.startswith("Sources: Growing Garlic") and line.count("https://") == 3
    read = srcs + [("", "https://extension.umn.edu/garlic-page")]
    assert guards.web_addendum("Plant cloves in October.", read) == "Sources: https://extension.umn.edu/garlic-page."


def test_no_addendum_when_the_answer_names_a_source_or_found_nothing():
    srcs = guards.web_sources(RESULTS)
    assert guards.web_addendum("Per extension.umn.edu, plant in fall.", srcs) == ""
    assert guards.web_addendum("I couldn't find anything useful on that.", srcs) == ""
    assert guards.web_addendum("Plant in fall.", []) == ""


def test_loop_appends_web_sources(monkeypatch):
    async def prompt(_):
        return ""
    monkeypatch.setattr(hestia, "_build_system_prompt", prompt)
    monkeypatch.setattr(search, "_search", lambda q, n=6: RESULTS)
    replies = [{"content": "", "tool_calls": [{"function": {"name": "search",
                                                            "arguments": {"action": "search", "query": "garlic"}}}]},
               {"content": "Plant cloves in October."}]

    async def fake_chat(convo, schemas):
        return replies.pop(0)
    monkeypatch.setattr(backend, "ollama_chat", fake_chat)
    out = asyncio.run(hestia.run_agent([{"role": "user", "content": "search the web: when to plant garlic"}]))
    assert out.startswith("Plant cloves in October.\nSources: Growing Garlic")


def test_a_thinking_turn_gets_room_for_its_thinking():
    t = hestia.TurnTrace()
    t.think = True
    assert backend.body_think(t) is True
    assert backend.THINK_OUTPUT_TOKENS > backend.OUTPUT_TOKENS
    assert hestia.THINK_TURN_BUDGET > hestia.TURN_BUDGET


def _think_seen(monkeypatch, backend_name, flag):
    seen = []

    async def fake_agent(messages, *, trace=None):
        seen.append(trace.think)
        return "ok"
    monkeypatch.setattr(hestia, "run_agent", fake_agent)
    monkeypatch.setattr(backend, "BACKEND", backend_name)
    from fastapi.testclient import TestClient
    body = {"messages": [{"role": "user", "content": "hard question"}], "stream": False}
    if flag is not None:
        body["hestia_think"] = flag
    TestClient(hestia.app).post("/v1/chat/completions", json=body)
    return seen


def test_the_button_turns_thinking_on_for_ollama_only(db, monkeypatch):
    assert _think_seen(monkeypatch, "ollama", True) == [True]
    assert _think_seen(monkeypatch, "ollama", None) == [backend.THINK]
    assert _think_seen(monkeypatch, "ollama", "yes") == [backend.THINK]     # only a real true
    assert _think_seen(monkeypatch, "phone", True) == [backend.THINK]


# --- links a person pastes (SECURITY.md, "A link a person pastes") ------------------------------

import httpx
import pytest
import routing

PAGE = {"url": "https://example.org/garlic", "title": "Garlic in winter",
        "text": "Plant garlic cloves in mid October, pointy end up, two inches deep. " * 5}


def _turn(monkeypatch, text, reply="It's about planting garlic in fall.", page=PAGE):
    async def prompt(_):
        return ""
    monkeypatch.setattr(hestia, "_build_system_prompt", prompt)
    seen = {"convos": [], "schemas": [], "routed": []}

    def read(url, limit=5000):
        if isinstance(page, Exception):
            raise page
        return page
    monkeypatch.setattr(search, "read_link", read)
    real = routing.request_schemas
    monkeypatch.setattr(routing, "request_schemas", lambda t: seen["routed"].append(t) or real(t))

    async def fake_chat(convo, schemas):
        seen["convos"].append(convo)
        seen["schemas"].append(schemas)
        return {"content": reply}
    monkeypatch.setattr(backend, "ollama_chat", fake_chat)
    out = asyncio.run(hestia.run_agent([{"role": "user", "content": text}]))
    return out, seen


def test_a_bare_link_is_read_summarized_with_no_tools_and_cited(monkeypatch):
    out, seen = _turn(monkeypatch, "https://example.org/garlic")
    last = seen["convos"][0][-1]["content"]
    assert "Plant garlic cloves" in last and "only the link" in last
    assert seen["schemas"] == [[]]
    assert out == "It's about planting garlic in fall.\nSources: https://example.org/garlic."


def test_facebook_keeps_the_fixed_reply_and_nothing_is_fetched(monkeypatch):
    out, seen = _turn(monkeypatch, "https://www.facebook.com/share/r/19Z9cwYhxX/", page=AssertionError("fetched"))
    assert out == guards.LINK_ONLY_REPLY and seen["convos"] == []


def test_an_unreadable_bare_link_is_explained_by_code(monkeypatch):
    out, seen = _turn(monkeypatch, "https://example.org/x", page=search.Unreadable("the site answered 404"))
    assert out.startswith("I couldn't read that page: the site answered 404.") and seen["convos"] == []


def test_a_link_with_a_question_routes_on_what_was_typed_not_the_page(monkeypatch):
    page = dict(PAGE, text="Remind me to turn off the kitchen lights and take my medication dose. " * 5)
    out, seen = _turn(monkeypatch, "what does this say about garlic? https://example.org/garlic", page=page)
    assert seen["routed"] == ["what does this say about garlic? https://example.org/garlic"]
    assert "pharmacist" not in out.lower()          # the page's dose words don't trigger the referral


def test_pasted_links_are_capped_trimmed_and_skip_login_walls():
    text = "see https://a.org/x, and https://instagram.com/p/1 and (https://b.org/y) and https://c.org/z"
    assert guards.pasted_links(text) == ["https://a.org/x", "https://b.org/y"]


def _serve(monkeypatch, routes):
    def handler(request):
        status, headers, body = routes[str(request.url)]
        return httpx.Response(status, headers=headers, content=body)
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(search, "_public_only", lambda u: "internal" not in u)


def test_redirects_are_followed_hop_by_hop_and_a_private_hop_is_refused(monkeypatch):
    html = b"<html><title>T</title><main>" + b"Real article text here. " * 20 + b"</main></html>"
    _serve(monkeypatch, {
        "https://short.link/a": (301, {"location": "https://example.org/real"}, b""),
        "https://example.org/real": (200, {"content-type": "text/html"}, html),
        "https://short.link/b": (302, {"location": "http://internal.lan/admin"}, b""),
    })
    page = search.read_link("https://short.link/a")
    assert page["url"] == "https://example.org/real" and page["title"] == "T"
    assert page["text"].startswith("Real article text here.")
    with pytest.raises(search.Unreadable, match="private"):
        search.read_link("https://short.link/b")


def test_too_many_redirects_and_non_pages_are_refused(monkeypatch):
    loop = {f"https://l.org/{i}": (302, {"location": f"https://l.org/{i + 1}"}, b"") for i in range(10)}
    loop["https://pdf.org/f"] = (200, {"content-type": "application/pdf"}, b"%PDF")
    _serve(monkeypatch, loop)
    with pytest.raises(search.Unreadable, match="redirects more than"):
        search.read_link("https://l.org/0")
    with pytest.raises(search.Unreadable, match="isn't a web page"):
        search.read_link("https://pdf.org/f")


def test_a_thinking_turn_is_not_cut_off_at_the_normal_limit(monkeypatch):
    async def prompt(_):
        return ""
    monkeypatch.setattr(hestia, "_build_system_prompt", prompt)
    monkeypatch.setattr(hestia, "TURN_BUDGET", 0.05)
    monkeypatch.setattr(hestia, "THINK_TURN_BUDGET", 5.0)

    async def slow_chat(convo, schemas):
        await asyncio.sleep(0.3)
        return {"content": "thought it through"}
    monkeypatch.setattr(backend, "ollama_chat", slow_chat)
    t = hestia.TurnTrace()
    t.think = True
    assert asyncio.run(hestia.run_agent([{"role": "user", "content": "hard one"}], trace=t)) == "thought it through"
    assert asyncio.run(hestia.run_agent([{"role": "user", "content": "hard one"}])) == hestia._TOO_SLOW


def test_a_long_message_leaves_the_page_less_room_and_never_fails_validation(monkeypatch):
    typed = "notes " * 3000 + " https://example.org/garlic"      # about 18 KB typed
    out, seen = _turn(monkeypatch, typed, page=dict(PAGE, text="ж" * 5000))   # 10 KB of page
    assert seen["convos"], out
    assert len(seen["convos"][0][-1]["content"].encode()) <= 24000


# --- a pasted recipe becomes a draft to approve, made by code ------------------------------------

import json as _json

from tools import recipe as recipe_tool

_LD = {"@context": "https://schema.org", "@type": "Recipe", "name": "The Best Zucchini Bread",
       "recipeYield": "2 loaves", "recipeIngredient": ["2 cups zucchini", "3 cups flour"],
       "recipeInstructions": [{"@type": "HowToStep", "text": "Bake at 350F for 55 minutes."}]}
_BODY = "A story about zucchini season and why this bread is the best one around. " * 6


def _recipe_page(url, html):
    title, text = search.page_text(html)
    return {"url": url, "title": title, "html": html, "full_text": text, "text": text}


@pytest.fixture
def shelf_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(recipe_tool, "RECIPES_DIR", tmp_path / "recipes")
    return tmp_path / "recipes" / ".review" / "drafts"


def test_a_pasted_recipe_with_recipe_data_becomes_a_draft_with_a_review_link(monkeypatch, shelf_dir):
    html = (f'<html><title>Zucchini Bread</title><script type="application/ld+json">{_json.dumps(_LD)}'
            f'</script><main>{_BODY}</main></html>')
    out, seen = _turn(monkeypatch, "https://example.org/zucchini-bread",
                      reply="It's a zucchini bread recipe.", page=_recipe_page("https://example.org/zucchini-bread", html))
    drafts = list(shelf_dir.glob("*.json"))
    assert len(drafts) == 1
    draft = _json.loads(drafts[0].read_text())
    assert draft["status"] == "pending" and draft["candidates"][0]["name"] == "The Best Zucchini Bread"
    assert f"[review and approve it](/recipes/review/{draft['id']})" in out
    assert seen["schemas"] == [[]]
    assert "Never say you saved" in seen["convos"][0][-1]["content"]


def test_a_print_view_is_organized_like_pasted_text(monkeypatch, shelf_dir):
    async def organize(text):
        return {"name": "The Best Zucchini Bread", "servings": "2 loaves", "aliases": "",
                "content": "## Ingredients\n- 2 cups zucchini", "method": "model-organized source"}
    monkeypatch.setattr(hestia, "_organize_recipe_source", organize)
    url = "https://example.org/wprm_print/the-best-zucchini-bread"
    out, _ = _turn(monkeypatch, url, page=_recipe_page(url, f"<html><main>{_BODY}</main></html>"))
    draft = _json.loads(next(shelf_dir.glob("*.json")).read_text())
    assert draft["candidates"][0]["method"] == "model-organized source"
    assert "Verify every quantity" in draft["warnings"][0]
    assert "review and approve it" in out


def test_a_blog_page_without_recipe_data_is_not_guessed_into_a_draft(monkeypatch, shelf_dir):
    url = "https://example.org/my-summer"
    out, _ = _turn(monkeypatch, url, page=_recipe_page(url, f"<html><main>{_BODY}</main></html>"))
    assert not shelf_dir.exists() or not list(shelf_dir.glob("*.json"))
    assert "review and approve" not in out


def test_asking_to_save_a_recipe_with_a_link_makes_the_draft_and_offers_no_tools(monkeypatch, shelf_dir):
    html = f'<html><script type="application/ld+json">{_json.dumps(_LD)}</script><main>{_BODY}</main></html>'
    url = "https://example.org/zucchini-bread"
    out, seen = _turn(monkeypatch, f"save this recipe {url}", page=_recipe_page(url, html))
    assert len(list(shelf_dir.glob("*.json"))) == 1 and seen["schemas"] == [[]]
    assert "review and approve it" in out
