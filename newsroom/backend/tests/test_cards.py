import re
from pathlib import Path

from sqlalchemy import text

from nr.cards.render import render_for_packet
from tests.helpers import candidate_event


def test_card_numbers_come_from_the_packet(sf):
    eid = candidate_event(sf)
    with sf() as s:
        pid, packet = s.execute(text("SELECT id, content FROM fact_packet WHERE news_event_id = :e"), {"e": eid}).one()
    r = render_for_packet(sf, pid, "position_card", "zh")
    with sf() as s:
        assets = s.execute(text("SELECT kind, file_path, sha256, data_hash, template_version, fact_packet_id "
                                "FROM media_asset ORDER BY kind")).all()
    assert [a[0] for a in assets] == ["card_png", "card_svg"] and all(a[5] == pid and a[4] == "v1" for a in assets)
    svg = Path(assets[1][1]).read_text()
    assert Path(assets[0][1]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    displays = {d for f in packet["facts"] for d in f["display"].get("zh", [])}
    values = re.findall(r'font-weight="700" fill="[^"]+">([^<]+)</text>', svg)
    shown = [v for v in values if any(ch.isdigit() for ch in v)]
    assert shown and all(v in displays for v in shown), (shown, displays)
    assert "观测样本" in svg and "事实包 v1" in svg
    # same inputs, same hash
    assert render_for_packet(sf, pid, "position_card", "zh")["data_hash"] == r["data_hash"]
