import json

import pytest

from app.sources.leaderboard import LeaderboardSchemaError, parse_leaderboard
from tests.conftest import load_fixture


def body(doc) -> bytes:
    return json.dumps(doc).encode()


def test_parses_real_rows():
    doc = load_fixture("leaderboard_sample.json")
    result = parse_leaderboard(body(doc))
    assert len(result.entries) == len(doc["leaderboardRows"])
    e = result.entries[0]
    assert set(e.windows) == {"day", "week", "month", "alltime"}
    assert e.address.startswith("0x") and len(e.address) == 42
    assert result.duplicates == 0 and not result.unknown_windows


def test_duplicate_and_case_normalisation():
    doc = load_fixture("leaderboard_sample.json")
    dup = dict(doc["leaderboardRows"][0], ethAddress=doc["leaderboardRows"][0]["ethAddress"].upper().replace("0X", "0x"))
    doc["leaderboardRows"].append(dup)
    result = parse_leaderboard(body(doc))
    assert result.duplicates == 1
    assert len(result.entries) == len(doc["leaderboardRows"]) - 1


def test_unknown_window_and_extra_field_are_reported_not_fatal():
    doc = load_fixture("leaderboard_sample.json")
    row = doc["leaderboardRows"][0]
    row["windowPerformances"].append(["quarter", {"pnl": "1", "roi": "0", "vlm": "0"}])
    row["newField"] = 1
    result = parse_leaderboard(body(doc))
    assert result.unknown_windows == {"quarter"}
    assert result.extra_fields == {"newField"}


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("leaderboardRows"),
    lambda d: d["leaderboardRows"][0].update(ethAddress="0x123"),
    lambda d: d["leaderboardRows"][0].update(accountValue="abc"),
    lambda d: d["leaderboardRows"][0].update(accountValue="NaN"),
    lambda d: d["leaderboardRows"][0]["windowPerformances"].pop(),  # drop allTime
    lambda d: d["leaderboardRows"][0].pop("windowPerformances"),
    lambda d: d.update(leaderboardRows=[]),
])
def test_schema_drift_fails_loudly(mutate):
    doc = load_fixture("leaderboard_sample.json")
    mutate(doc)
    with pytest.raises(LeaderboardSchemaError):
        parse_leaderboard(body(doc))


def test_non_json_body():
    with pytest.raises(LeaderboardSchemaError):
        parse_leaderboard(b"<html>")
