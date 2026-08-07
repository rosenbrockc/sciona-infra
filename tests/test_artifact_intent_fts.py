from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260807000000_artifact_intent_fts.sql"
)


def test_artifact_search_uses_discipline_neutral_relaxed_fts() -> None:
    text = MIGRATION.read_text(encoding="utf-8")

    assert "public.catalog_relaxed_tsquery(query_text)" in text
    assert "public.catalog_search_document(" in text
    assert "candidates.document @@ candidates.relaxed_tsq" in text
    assert "TO anon, authenticated" in text
    assert "ecg" not in text.lower()
    assert "cardio" not in text.lower()
