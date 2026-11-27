"""The nightly feed: code finds and fetches the links, the model only ranks what was fetched,
and the app reads the result without asking the model anything."""
from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

import config
import feed
import feed_store


@pytest.fixture(autouse=True)
def no_gap(monkeypatch):
    monkeypatch.setattr(feed, "QUERY_GAP", 0)


@pytest.fixture
def feed_dir(db, tmp_path, monkeypatch):
    d = tmp_path / "feed"
    d.mkdir()
    (d / "people.toml").write_text(
        '[[person]]\nid = "ann"\nname = "Ann"\nqueries = ["garden tips", "claude tips"]\n'
        '[[person]]\nid = "Bad Id!"\nname = "x"\nqueries = ["q"]\n'
        '[[person]]\nid = "noq"\nname = "No queries"\nqueries = []\n')
    (d / "ann.md").write_text("Practical over fluff.")
    monkeypatch.setattr(config, "FEED_DIR", d)
    return d


def _page(title="T", desc="D", image="/img.jpg", body="Body text here."):
    return (f'<html><head><title>{title}</title><meta property="og:description" content="{desc}">'
            f'<meta content="{image}" property="og:image"></head><body><script>x()</script>'
            f'<p>{body}</p></body></html>')


def _cand(n, source="a.com"):
    return {"url": f"https://{source}/{n}", "title": f"Title {n}", "source": source,
            "description": "d", "excerpt": "e", "image_url": "", "query": "q"}


def test_load_people_skips_malformed_entries(feed_dir):
    people = feed.load_people()
    assert [p["id"] for p in people] == ["ann"]
    assert people[0]["rubric"] == "Practical over fluff."
    assert people[0]["queries"] == ["garden tips", "claude tips"]


def test_no_config_means_no_people(tmp_path):
    assert feed.load_people(tmp_path) == []


def test_read_page_pulls_meta_in_any_attribute_order():
    p = feed.read_page(_page(title="A &amp; B"), "https://x.org/post/1")
    assert p["title"] == "A & B"
    assert p["description"] == "D"
    assert p["image_url"] == "https://x.org/img.jpg"
    assert "x()" not in p["excerpt"] and "Body text here." in p["excerpt"]


def test_search_title_beats_a_bare_page_title():
    html = "<html><head><title>MSN</title></head><body>x</body></html>"
    assert feed.read_page(html, "https://msn.com/a", "Real headline")["title"] == "Real headline"
    assert feed.read_page(html, "https://msn.com/a")["title"] == "MSN"


def test_source_drops_www():
    assert feed.source_of("https://www.Example.com/a") == "example.com"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_follows_public_redirects_and_refuses_private_hops(monkeypatch):
    monkeypatch.setattr(feed.search, "_public_only", lambda u: "internal" not in u)

    def handler(req):
        if req.url.path == "/start":
            return httpx.Response(301, headers={"location": "/final"})
        if req.url.path == "/final":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")
        if req.url.path == "/sneaky":
            return httpx.Response(302, headers={"location": "http://internal.ts.net/inbox"})
        return httpx.Response(404)

    with _client(handler) as c:
        url, _, body = feed._get(c, "https://pub.org/start", 100)
        assert url == "https://pub.org/final" and body == b"ok"
        assert feed._get(c, "https://pub.org/sneaky", 100) is None
        assert feed._get(c, "http://internal.ts.net/x", 100) is None


def test_get_refuses_oversized_bodies(monkeypatch):
    monkeypatch.setattr(feed.search, "_public_only", lambda u: True)
    with _client(lambda r: httpx.Response(200, content=b"x" * 500)) as c:
        assert feed._get(c, "https://pub.org/big", 100) is None


def test_searx_tops_up_news_from_general(monkeypatch):
    calls = []

    def once(q, category, time_range):
        calls.append((category, time_range))
        urls = {"news": ["https://n/1", "https://g/1"], "general": ["https://g/1/", "https://g/2", "https://g/3",
                                                                    "https://g/4", "https://g/5"]}[category]
        return [{"url": u, "title": u, "snippet": ""} for u in urls]
    monkeypatch.setattr(feed, "_searx_once", once)
    got = feed.searx("sourdough tips")
    assert calls == [("news", "week"), ("general", "month")]
    assert [g["url"] for g in got] == ["https://n/1", "https://g/1", "https://g/2", "https://g/3", "https://g/4"]


def test_searx_skips_general_when_news_is_enough(monkeypatch):
    calls = []

    def once(q, category, time_range):
        calls.append(category)
        return [{"url": f"https://n/{i}", "title": "t", "snippet": ""} for i in range(9)]
    monkeypatch.setattr(feed, "_searx_once", once)
    assert len(feed.searx("q")) == feed.PER_QUERY and calls == ["news"]


def test_gather_round_robins_queries_and_skips_seen(monkeypatch):
    results = {"a": ["https://s/a1", "https://s/a2"], "b": ["https://s/b1", "https://s/a1/"]}
    monkeypatch.setattr(feed, "searx", lambda q: [{"url": u, "title": u, "snippet": ""} for u in results[q]])
    got = feed.gather({"queries": ["a", "b"]}, seen={"https://s/a2"})
    assert [g["url"] for g in got] == ["https://s/a1", "https://s/b1"]
    assert got[1]["query"] == "b"


def test_gather_survives_a_dead_query(monkeypatch):
    def searx(q):
        if q == "dead":
            raise httpx.ConnectError("down")
        return [{"url": "https://s/1", "title": "t", "snippet": ""}]
    monkeypatch.setattr(feed, "searx", searx)
    assert len(feed.gather({"queries": ["dead", "ok"]}, seen=set())) == 1


def test_parse_picks_holds_the_model_to_the_list():
    cands = [_cand(1), _cand(2), _cand(3), _cand(4, "b.com"), _cand(5, "b.com")]
    raw = json.dumps({"picks": [
        {"n": 9, "why": "not on the list"},
        {"n": 2, "why": "good"},
        {"n": 2, "why": "twice"},
        {"n": True, "why": "bool is not a number"},
        {"n": "3", "why": "string is not a number"},
        {"n": 4, "why": ""},
        {"n": 1, "why": "also good"},
        {"n": 3, "why": "third from a.com"},
        {"n": 5, "why": "fine"},
        {"url": "https://evil/", "why": "model-made link"},
    ]})
    picks = feed.parse_picks(raw, cands)
    assert [p["url"] for p in picks] == ["https://a.com/2", "https://a.com/1", "https://b.com/5"]
    assert picks[0]["why"] == "good"


def test_parse_picks_caps_the_day():
    cands = [_cand(i, f"s{i}.com") for i in range(1, 13)]
    raw = json.dumps({"picks": [{"n": i, "why": "y"} for i in range(1, 13)]})
    assert len(feed.parse_picks(raw, cands)) == feed.MAX_PICKS


def test_parse_picks_rejects_non_json():
    with pytest.raises(ValueError):
        feed.parse_picks("I picked 1 and 3", [_cand(1)])


def _night(monkeypatch, answer):
    monkeypatch.setattr(feed.search, "_public_only", lambda u: True)
    monkeypatch.setattr(feed, "searx", lambda q: [{"url": f"https://{q[0]}.org/post", "title": q, "snippet": "s"}])
    monkeypatch.setattr(feed, "ask_model", answer)

    def handler(req):
        if req.url.path.endswith(".jpg"):
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8jpg")
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=_page(title=f"Post on {req.url.host}").encode())
    return _client(handler)


def test_a_night_stores_ranked_picks_with_cached_images(feed_dir, monkeypatch):
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return json.dumps({"picks": [{"n": 2, "why": "Claude tips she can use today."}]})

    person = feed.load_people()[0]
    with _night(monkeypatch, answer) as c:
        assert feed.run_person(person, dt.date(2026, 10, 15), c) == 1
    assert "Practical over fluff." in prompts[0] and "[2] claude tips (c.org)" in prompts[0]
    day = feed_store.latest("ann")
    assert day["day"] == "2026-10-15"
    (item,) = day["items"]
    assert item["url"] == "https://c.org/post" and item["why"] == "Claude tips she can use today."
    assert (feed_dir / "img" / item["image"]).read_bytes() == b"\xff\xd8jpg"


def test_model_down_keeps_yesterdays_set(feed_dir, monkeypatch):
    feed_store.save_day("ann", "2026-10-14", [{**_cand(1), "why": "yesterday"}])

    def down(prompt):
        raise httpx.ConnectError("ollama down")
    with _night(monkeypatch, down) as c, pytest.raises(httpx.ConnectError):
        feed.run_person(feed.load_people()[0], dt.date(2026, 10, 15), c)
    assert feed_store.latest("ann")["day"] == "2026-10-14"


def test_a_link_is_shown_once_ever(feed_dir, monkeypatch):
    feed_store.save_day("ann", "2026-10-14", [{**_cand(1), "url": "https://g.org/post", "why": "y"}])
    seen = []

    def answer(prompt):
        seen.append(prompt)
        return json.dumps({"picks": [{"n": 1, "why": "y"}]})
    with _night(monkeypatch, answer) as c:
        feed.run_person(feed.load_people()[0], dt.date(2026, 10, 15), c)
    assert "g.org" not in seen[0] and "c.org" in seen[0]


def test_reactions_feed_the_next_ranking(feed_dir):
    feed_store.save_day("ann", "2026-10-14", [{**_cand(1), "why": "y"}, {**_cand(2), "why": "y"}])
    a, b = feed_store.latest("ann")["items"]
    feed_store.react(a["id"], "heart")
    feed_store.react(b["id"], "nope")
    prompt = feed.build_prompt(feed.load_people()[0], [_cand(3)],
                               feed_store.reactions("ann", "heart"), feed_store.reactions("ann", "nope"))
    liked = prompt.split("LIKED RECENTLY:")[1].split("NOT FOR THEM:")
    assert "Title 1" in liked[0] and "Title 2" in liked[1].split("CANDIDATES:")[0]
    with pytest.raises(ValueError):
        feed_store.react(a["id"], "love")


def test_prune_keeps_hearted_and_recent_images(feed_dir):
    img = feed_dir / "img"
    img.mkdir()
    for n in ("old.jpg", "kept.jpg", "new.jpg"):
        (img / n).write_bytes(b"x")
    feed_store.save_day("ann", "2026-09-01", [{**_cand(1), "why": "y", "image": "old.jpg"},
                                              {**_cand(2), "why": "y", "image": "kept.jpg"}])
    feed_store.react(feed_store.latest("ann")["items"][1]["id"], "heart")
    feed_store.save_day("ann", "2026-10-14", [{**_cand(3), "why": "y", "image": "new.jpg"}])
    assert feed.prune_images(dt.date(2026, 10, 15), img) == 1
    assert sorted(p.name for p in img.iterdir()) == ["kept.jpg", "new.jpg"]


@pytest.fixture
def client(feed_dir):
    testclient = pytest.importorskip("fastapi.testclient")
    import hestia
    return testclient.TestClient(hestia.app)


def test_routes_serve_the_day_without_the_model(client, feed_dir):
    (feed_dir / "img").mkdir()
    (feed_dir / "img" / "a.jpg").write_bytes(b"\xff\xd8")
    feed_store.save_day("ann", "2026-10-15", [{**_cand(1), "why": "y", "image": "a.jpg"},
                                              {**_cand(2), "why": "z"}])
    assert client.get("/v1/feed/people").json() == {"people": [{"id": "ann", "name": "Ann"}]}
    day = client.get("/v1/feed", params={"person": "ann"}).json()
    first, second = day["items"]
    assert first["image_url"] == f"/v1/feed/{first['id']}/image" and second["image_url"] is None
    assert "image" not in first
    r = client.get(first["image_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get(f"/v1/feed/{second['id']}/image").status_code == 404
    assert client.post(f"/v1/feed/{first['id']}/react", json={"reaction": "heart"}).json() == \
        {"id": first["id"], "reaction": "heart"}
    assert client.post(f"/v1/feed/{first['id']}/react", json={"reaction": None}).json()["reaction"] is None
    assert client.post(f"/v1/feed/{first['id']}/react", json={"reaction": "love"}).status_code == 400
    assert client.post(f"/v1/feed/{first['id']}/react", json={}).status_code == 400
    assert client.post("/v1/feed/9999/react", json={"reaction": "nope"}).status_code == 404
    assert client.get("/v1/feed", params={"person": "../etc"}).status_code == 400
    assert client.get("/v1/feed", params={"person": "nobody"}).json() == {"day": None, "items": []}


def test_a_night_where_every_search_is_empty_fails_the_run(monkeypatch, capsys):
    """2026-11-09 to 11-17: SearXNG lost DNS, every night exited 0, and no alert fired."""
    monkeypatch.setattr(feed, "load_people", lambda: [{"id": "a"}, {"id": "b"}])
    monkeypatch.setattr(feed, "run_person", lambda *a, **k: (_ for _ in ()).throw(feed.NoCandidates("x")))
    monkeypatch.setattr(feed, "prune_images", lambda *a: 0)
    assert feed.main() == 1
    assert "SearXNG" in capsys.readouterr().err


def test_one_person_with_nothing_new_is_a_normal_night(monkeypatch):
    def run(person, *a, **k):
        if person["id"] == "a":
            raise feed.NoCandidates("x")
        return 3
    monkeypatch.setattr(feed, "load_people", lambda: [{"id": "a"}, {"id": "b"}])
    monkeypatch.setattr(feed, "run_person", run)
    monkeypatch.setattr(feed, "prune_images", lambda *a: 0)
    assert feed.main() == 0


def test_rank_request_matches_resident_num_ctx():
    import backend
    assert feed.rank_request("p")["options"]["num_ctx"] == backend.NUM_CTX
