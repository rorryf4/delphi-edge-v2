import psycopg
import pytest

from delphi_jobs import seed_reference
from factories import CFB, NFL, seed, team


def count(conn, table: str) -> int:
    return conn.execute(f"select count(*) from {table}").fetchone()[0]


def test_seed_creates_reference_data(db):
    seed(db)
    assert count(db, "sports") == 1
    assert count(db, "leagues") == 2
    assert count(db, "seasons") == 2
    assert count(db, "teams") == len(NFL) + len(CFB)
    assert count(db, "team_seasons") == len(NFL) + len(CFB)
    kinds = dict(db.execute("select slug, kind from experts").fetchall())
    assert kinds == {"delphiedge": "model", "market": "market"}
    outlets = {r[0] for r in db.execute("select slug from outlets").fetchall()}
    assert outlets == {"covers", "sports-illustrated", "cbs-sports", "yahoo-sports"}


def test_seed_is_idempotent(db):
    seed(db)
    before = {t: count(db, t) for t in ("teams", "team_aliases", "team_seasons", "experts", "outlets", "seasons")}
    seed(db)
    after = {t: count(db, t) for t in before}
    assert before == after


def test_rename_updates_existing_team_instead_of_duplicating(db):
    seed(db)
    renamed = [team("251", "Texas (renamed)", ["Texas (renamed)"]), CFB[1]]
    seed_reference.run(db, 2026, {"nfl": NFL, "cfb": renamed})
    names = [r[0] for r in db.execute(
        "select t.name from teams t join team_aliases a on a.team_id = t.id "
        "where a.source = 'cfbd' and a.alias = '251'").fetchall()]
    assert names == ["Texas (renamed)"]
    assert count(db, "teams") == len(NFL) + len(CFB)


def test_ambiguous_names_are_not_aliases(db):
    report = seed(db)
    assert report["nfl"]["ambiguous_names_skipped"] == ["New York"]
    rows = db.execute(
        "select alias from team_aliases where source = 'name' and alias_key = 'newyork'"
    ).fetchall()
    assert rows == []
    jets = db.execute(
        "select t.slug from team_aliases a join teams t on t.id = a.team_id "
        "where a.source = 'name' and a.alias_key = 'jets'"
    ).fetchone()
    assert jets == ("nyj",)


@pytest.mark.parametrize("written, slug", [
    ("Hawaii", "hawaii"), ("HAWAI'I", "hawaii"), ("San Jose State", "san-jose-state"),
    ("san josé st.", "san-jose-state"), ("Miami OH", "miami-oh"), ("Miami (Ohio)", None),
])
def test_names_match_regardless_of_punctuation_and_accents(db, written, slug):
    cfb = [
        team("62", "Hawai'i", ["Hawai'i"]),
        team("23", "San José State", ["San José State", "San José St."]),
        team("193", "Miami (OH)", ["Miami (OH)"]),
    ]
    seed_reference.run(db, 2026, {"nfl": NFL, "cfb": cfb})
    found = db.execute(
        "select t.slug from team_aliases a join teams t on t.id = a.team_id "
        "where a.source = 'name' and a.alias_key = public.alias_key(%s)", (written,)
    ).fetchone()
    assert (found[0] if found else None) == slug


def test_slug_collision_is_refused(db):
    seed(db)
    clash = [*CFB, team("999", "Texas")]  # new source id, same slug as existing Texas
    with pytest.raises(RuntimeError, match="slug 'texas'"):
        with db.transaction():
            seed_reference.run(db, 2026, {"nfl": NFL, "cfb": clash})


@pytest.mark.parametrize("name, slug", [
    ("Texas A&M", "texas-am"), ("Miami (OH)", "miami-oh"), ("Hawai'i", "hawaii"),
    ("San José State", "san-jose-state"), ("UL Monroe", "ul-monroe"),
])
def test_slugify(name, slug):
    assert seed_reference.slugify(name) == slug
