"""Campaign integrity, manual editing and crash-safe scheduling coverage."""

import datetime
import json
import shutil
import subprocess
import time

import pandas as pd
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image
from twitter_text import parse_tweet

from eth_defi.vault_report.ghost import GhostPost
from eth_defi.vault_report.chart_metadata import chart_records
from eth_defi.vault_report.tweet_plan import prepare_campaign, derive_winner_evidence, fetch_campaign_assets, verify_source, TweetEntry, TweetPlan, compile_campaign, digest, extract_charts, file_hash, load_campaign, parse_utc, read_plan, render_plan, render_preview, source_fields, validate_plan, write_json
from eth_defi.vault_report.tweet_scheduler import TweetLedger, account_lock, launch_screen, run_campaign
from eth_defi.vault_report.twitter import AmbiguousPostError, TwitterError, TwitterRateLimit, TwitterWriter, UploadedMedia


@pytest.fixture
def campaign_plan(tmp_path: Path) -> Path:
    """Provide a fully evidenced two-chart campaign with editable Markdown.

    Distinct chart identities exercise cross-entry spacing and source binding.

    :param tmp_path: Isolated bundle.
    :return: Editable plan path.
    """
    (tmp_path / "charts").mkdir()
    entries, charts, evidence = [], [], {}
    for key in ("lending_performance", "protocol_yields"):
        image = tmp_path / "charts" / f"{key}.png"
        Image.new("RGB", (800, 500), "green").save(image)
        charts.append({"key": key, "heading": key, "url": f"https://storage.ghost.io/{key}.png", "context": "Ranking context", "sha256": file_hash(image), "path": f"charts/{key}.png"})
        winners = [{"entity_id": str(i), "name": f"Winner {i}", "handle": f"winner{i}", "user_id": str(i), "rank": i, "official_source": "https://example.org", "verified_at": "2026-10-01"} for i in (1, 2, 3)]
        evidence[key] = {"sha256": file_hash(image), "metric": "annualised 3M return", "winners": winners, "issues": []}
        entries.append(TweetEntry(key, {"id": f"post:{key}", "chart_key": key, "winner_evidence": f"chart-evidence.json#{key}", "status": "approved"}, "October's leaders: @winner1 @winner2 @winner3.\nhttps://tradingstrategy.ai/blog/report", f"charts/{key}.png", "Reviewed chart"))
    source = {"id": "post", "slug": "report", "title": "Best vaults, October 2026", "url": "https://tradingstrategy.ai/blog/report", "status": "published", "charts": charts}
    source["digest"] = digest(source_fields(source))
    write_json(tmp_path / "source-post.json", source)
    write_json(tmp_path / "chart-evidence.json", evidence)
    path = tmp_path / "tweet-plan.md"
    path.write_text(render_plan(TweetPlan({"schema_version": 1, "campaign_id": "post", "account": "tradingprotocol", "report_month": "2026-10", "source_digest": source["digest"], "start_at": "2026-10-02T00:00:00Z", "interval_hours": 12}, entries)))
    return path


def test_hand_edits_are_exact_and_compilations_immutable(campaign_plan: Path) -> None:
    """Preserve manually edited line breaks and retain earlier approved revisions.

    Recompilation must create another file rather than changing a running queue.

    :param campaign_plan: Evidenced editable campaign.
    """
    first = compile_campaign(campaign_plan)
    before = first.read_bytes()
    campaign_plan.write_text(campaign_plan.read_text().replace("October's leaders", "October's lending leaders", 1))
    second = compile_campaign(campaign_plan)
    assert second != first
    assert first.read_bytes() == before
    assert load_campaign(second)["entries"][0]["body"] == read_plan(campaign_plan).entries[0].body
    assert "\nhttps://" in load_campaign(second)["entries"][0]["body"]


def test_chart_tampering_and_missing_winner_block(campaign_plan: Path) -> None:
    """Reject wrong attachments and edited-away mandatory tags.

    A valid-looking body cannot bypass its chart attribution evidence.

    :param campaign_plan: Evidenced editable campaign.
    """
    campaign_plan.write_text(campaign_plan.read_text().replace("@winner3", "Winner three", 1))
    with pytest.raises(ValueError, match="Missing required winner"):
        compile_campaign(campaign_plan)
    campaign_plan.parent.joinpath("charts/lending_performance.png").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Changed or unbound image"):
        validate_plan(campaign_plan)


def test_draft_and_duplicate_yaml_cannot_compile(campaign_plan: Path) -> None:
    """Draft wording and ambiguous YAML configuration cannot start a live queue.

    Preview remains available while manual approval is outstanding.

    :param campaign_plan: Editable campaign.
    """
    campaign_plan.write_text(campaign_plan.read_text().replace("status: approved", "status: draft", 1))
    assert render_preview(campaign_plan).exists()
    with pytest.raises(ValueError, match="manually approved"):
        compile_campaign(campaign_plan)
    campaign_plan.write_text(campaign_plan.read_text().replace("interval_hours: 12", "interval_hours: 12\ninterval_hours: 1"))
    with pytest.raises(ValueError, match="Duplicate YAML"):
        read_plan(campaign_plan)


def test_preview_fences_handles(campaign_plan: Path) -> None:
    """Keep X usernames inside text fences in the GitHub comment.

    Internal evidence links must not notify unrelated GitHub accounts.

    :param campaign_plan: Editable campaign.
    """
    comment = render_preview(campaign_plan).read_text()
    fence = False
    for line in comment.splitlines():
        if line.startswith("```"):
            fence = not fence
        if "@winner" in line:
            assert fence
    assert "![Reviewed chart](https://storage.ghost.io/" in comment


@pytest.mark.parametrize("text,expected", [("english text 日本語 😷 https://example.com", 46), ("👨‍👩‍👧‍👦", 2), ("https://example.com/" + "a" * 200, 23)])
def test_twitter_text_conformance_examples(text: str, expected: int) -> None:
    """Exercise Unicode/URL rules from the official twitter-text fixtures.

    These cases differ from ordinary character or UTF-16 code-unit counting.
    See https://github.com/twitter/twitter-text/tree/master/conformance.

    :param text: Representative fixture input.
    :param expected: X weighted count.
    """
    assert parse_tweet(text).weightedLength == expected


def test_editorial_chart_inventory() -> None:
    """Include manually added editorial charts while ignoring table thumbnails.

    Filename-only discovery would lose charts pasted by the Ghost editor.
    """
    charts, ignored = extract_charts('<h3>New editorial chart</h3><figure><img src="https://storage.ghost.io/image-1.png"></figure><h3>Lending vaults</h3><img src="https://storage.ghost.io/lending_performance.png"><table><tr><td><img src="https://example.org/sparkline.png"></td></tr></table>')
    assert [c["key"] for c in charts] == ["new_editorial_chart", "lending_performance"]
    assert len(ignored) == 1


@pytest.fixture
def account_state(tmp_path: Path) -> Path:
    """Explicitly bootstrap isolated persistent queue state.

    Sending tests never use the operator's real account ledger.

    :param tmp_path: Test directory.
    :return: First-use ledger directory.
    """
    state = tmp_path / "state"
    state.mkdir()
    ledger = TweetLedger(state, bootstrap=True)
    ledger.close()
    return state


def test_spacing_restart_and_revisions(campaign_plan: Path, account_state: Path) -> None:
    """Send once per half-day across restart and edited campaign revisions.

    The fake clock proves overdue tweets cannot burst on resume.

    :param campaign_plan: Reviewed campaign.
    :param account_state: Durable test ledger.
    """
    path = compile_campaign(campaign_plan)
    start = parse_utc("2026-10-02T00:00:00Z").replace(tzinfo=datetime.UTC).timestamp()
    clock = [start - 1000]
    calls = []
    writer = Mock()
    writer.fetch_identity.return_value = {"id": "123", "username": "TradingProtocol"}
    writer.fetch_handles.return_value = {f"winner{i}": {"id": str(i)} for i in (1, 2, 3)}
    writer.upload_chart.return_value = UploadedMedia("media", start + 100000)
    writer.create_post.side_effect = lambda *_: calls.append(clock[0]) or str(len(calls))
    run_campaign(path, account_state, writer, lambda _: None, max_sends=1, now=lambda: clock[0], wait=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert calls == [start]
    campaign_plan.write_text(campaign_plan.read_text().replace("October's leaders", "October's reviewed leaders"))
    revised = compile_campaign(campaign_plan)
    clock[0] = start + 100
    run_campaign(revised, account_state, writer, lambda _: None, now=lambda: clock[0], wait=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert calls == [start, start + 43200]


def test_ambiguous_send_blocks_restart(campaign_plan: Path, account_state: Path) -> None:
    """A connection loss after dispatch never triggers an automatic duplicate.

    Other pending charts are blocked until operator reconciliation.

    :param campaign_plan: Reviewed campaign.
    :param account_state: Durable ledger.
    """
    path = compile_campaign(campaign_plan)
    start = parse_utc("2026-10-02T00:00:00Z").replace(tzinfo=datetime.UTC).timestamp()
    clock = [start - 1000]
    writer = Mock()
    writer.fetch_identity.return_value = {"id": "123", "username": "TradingProtocol"}
    writer.fetch_handles.return_value = {f"winner{i}": {"id": str(i)} for i in (1, 2, 3)}
    writer.upload_chart.return_value = UploadedMedia("media", start + 10000)
    writer.create_post.side_effect = AmbiguousPostError("timeout")
    with pytest.raises(AmbiguousPostError):
        run_campaign(path, account_state, writer, lambda _: None, now=lambda: clock[0], wait=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(ValueError, match="reconciliation"):
        run_campaign(path, account_state, writer, lambda _: None, now=lambda: clock[0])
    assert writer.create_post.call_count == 1


def test_stale_start_and_account_lock(campaign_plan: Path, account_state: Path) -> None:
    """Refuse stale first starts and simultaneous account mutation.

    The lock is shared by launch, live sending, bootstrap and reconciliation.

    :param campaign_plan: Reviewed campaign.
    :param account_state: Durable ledger.
    """
    ledger = TweetLedger(account_state)
    try:
        with pytest.raises(ValueError, match="ten minutes"):
            ledger.register(load_campaign(compile_campaign(campaign_plan)), parse_utc("2026-10-03T00:00:00Z").replace(tzinfo=datetime.UTC).timestamp())
    finally:
        ledger.close()
    with account_lock(account_state):
        with pytest.raises(ValueError, match="account lock"):
            with account_lock(account_state):
                pytest.fail("Second lock was acquired")


def test_writer_never_retries_uncertain_creates(tmp_path: Path) -> None:
    """Classify a provider 503 as an ambiguous write, making only one request.

    A known rate limit has a distinct retry classification.

    :param tmp_path: Unused isolated test directory.
    """
    writer = TwitterWriter("key", "secret", "token", "token-secret")
    writer.session = Mock()
    writer.session.request.return_value.status_code = 503
    with pytest.raises(AmbiguousPostError):
        writer.create_post("Reviewed text", UploadedMedia("media", 9999999999))
    assert writer.session.request.call_count == 1
    writer.session.request.return_value.status_code = 429
    writer.session.request.return_value.headers = {"x-rate-limit-reset": "9999999999"}
    with pytest.raises(TwitterRateLimit):
        writer.create_post("Reviewed text", UploadedMedia("media", 9999999999))


def test_media_failure_does_not_dispatch(campaign_plan: Path, account_state: Path) -> None:
    """A rejected media upload leaves the tweet pending without a text-only send.

    Only the later create call can transition a pending entry into dispatch.

    :param campaign_plan: Reviewed campaign.
    :param account_state: Durable ledger.
    """
    path = compile_campaign(campaign_plan)
    start = parse_utc("2026-10-02T00:00:00Z").replace(tzinfo=datetime.UTC).timestamp()
    clock = [start - 1000]
    writer = Mock()
    writer.fetch_identity.return_value = {"id": "123", "username": "TradingProtocol"}
    writer.fetch_handles.return_value = {f"winner{i}": {"id": str(i)} for i in (1, 2, 3)}
    writer.upload_chart.side_effect = TwitterError("HTTP 402")
    with pytest.raises(TwitterError):
        run_campaign(path, account_state, writer, lambda _: None, now=lambda: clock[0], wait=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    writer.create_post.assert_not_called()
    ledger = TweetLedger(account_state)
    try:
        assert ledger.db.execute("SELECT status FROM tweets WHERE id='post:lending_performance'").fetchone()[0] == "pending"
    finally:
        ledger.close()


def test_reconciliation_is_durable_and_protects_sent_identity(campaign_plan: Path, account_state: Path) -> None:
    """Recover an interrupted intent with an audited existing-tweet resolution.

    A later campaign registration retains the sent row rather than reposting.

    :param campaign_plan: Reviewed campaign.
    :param account_state: Durable ledger.
    """
    campaign = load_campaign(compile_campaign(campaign_plan))
    ledger = TweetLedger(account_state)
    try:
        ledger.register(campaign, 0)
        entry = campaign["entries"][0]
        ledger.begin_send(entry, 100)
        ledger.register(campaign, 200)
        assert ledger.db.execute("SELECT status FROM tweets WHERE id=?", (entry["id"],)).fetchone()[0] == "needs_reconciliation"
        ledger.reconcile(entry["id"], "sent", "Exact body/image confirmed on public account", "98765", 100)
        ledger.register(campaign, 300)
        assert ledger.db.execute("SELECT tweet_id,status FROM tweets WHERE id=?", (entry["id"],)).fetchone()[:] == ("98765", "sent")
        assert ledger.db.execute("SELECT reason FROM operator_audit").fetchone()[0] == "Exact body/image confirmed on public account"
        with pytest.raises(ValueError, match="intent"):
            ledger.begin_send(entry, 400)
    finally:
        ledger.close()


def test_upload_hash_checked_at_read(campaign_plan: Path) -> None:
    """Check the actual upload bytes against approval after an attachment swap.

    The provider receives no media request when the file has changed.

    :param campaign_plan: Reviewed bundle.
    """
    image = campaign_plan.parent / "charts/lending_performance.png"
    expected = file_hash(image)
    image.write_bytes(b"changed after earlier validation")
    writer = TwitterWriter("key", "secret", "token", "token-secret")
    writer.session = Mock()
    with pytest.raises(TwitterError, match="approved attachment"):
        writer.upload_chart(image, "Reviewed alt", expected)
    writer.session.request.assert_not_called()


def test_daily_cap_uses_later_reset() -> None:
    """Defer daily-cap rejection to the longer user reset instead of retrying early.

    Endpoint and daily user reset headers can differ by many hours.
    """
    writer = TwitterWriter("key", "secret", "token", "token-secret")
    writer.session = Mock()
    writer.session.request.return_value.status_code = 429
    writer.session.request.return_value.headers = {"x-rate-limit-reset": "9999999000", "x-user-limit-24hour-reset": "9999999999"}
    with pytest.raises(TwitterRateLimit) as caught:
        writer.create_post("Reviewed body", UploadedMedia("media", 9999999999))
    assert caught.value.reset_at == 9999999999


def test_metadata_keeps_visible_entities_and_missing_attribution() -> None:
    """Exclude invisible rows without silently skipping an unknown leading manager.

    Winner selection is based on the metric and keeps an unresolved first rank.
    """
    frame = pd.DataFrame({"name": ["Absent", "Unknown", "Known"], "curator_slug": [None, None, "known"], "return": [10.0, 9.0, 8.0]}, index=["a", "b", "c"])
    records = chart_records(frame, "vault", "return", ["b", "c"])
    assert [row["entity_id"] for row in records] == ["b", "c"]
    evidence = derive_winner_evidence("risk_return", {"entities": records, "metric": "return"}, {"known": {"handle": "known"}})
    assert evidence["winners"][0]["entity_id"] == "b"
    assert "handle" not in evidence["winners"][0]


def test_restore_and_source_change_pause(campaign_plan: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore exact saved charts and pause when the live source title changes.

    Neither operation accepts changed bytes or silently refreshes an approval.

    :param campaign_plan: Source-bound reviewed bundle.
    :param monkeypatch: Controlled provider reads.
    """
    image = campaign_plan.parent / "charts/lending_performance.png"
    data = image.read_bytes()
    image.unlink()
    monkeypatch.setattr("eth_defi.vault_report.tweet_plan.fetch_image", lambda *_: data)
    fetch_campaign_assets(campaign_plan)
    assert image.read_bytes() == data
    campaign = load_campaign(compile_campaign(campaign_plan))
    ghost = Mock()
    ghost.fetch_post_by_slug.return_value = Mock(id="post", slug="report", title="Changed month, November 2026", status="published", html='<h3>lending_performance</h3><img src="https://storage.ghost.io/lending_performance.png"><h3>protocol_yields</h3><img src="https://storage.ghost.io/protocol_yields.png">')
    with pytest.raises(ValueError, match="Ghost chart/content changed"):
        verify_source(ghost, campaign)


@pytest.mark.skipif(shutil.which("screen") is None, reason="GNU Screen required")
def test_real_screen_readiness_and_environment(campaign_plan: Path, account_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise a real detached Screen handshake using a non-posting stub runner.

    Confirms inherited environment and exact reviewed digest without X writes.

    :param campaign_plan: Reviewed bundle.
    :param account_state: Durable ledger.
    :param monkeypatch: Non-secret inherited test environment.
    """
    path = compile_campaign(campaign_plan)
    campaign = load_campaign(path)
    stub = campaign_plan.parent / "screen-stub.py"
    stub.write_text('import os,json,time\nfrom pathlib import Path\nr=Path(os.environ["READY_FILE"])\nr.write_text(json.dumps({"pid":os.getpid(),"digest":os.environ["APPROVED_CAMPAIGN_DIGEST"]}))\nwhile not Path(os.environ["LAUNCH_GATE"]).exists():time.sleep(0.05)\nPath(os.environ["STATE_DIR"],"inherited.txt").write_text(os.environ["TWEET_TEST_SENTINEL"])\ntime.sleep(30)\n')
    monkeypatch.setenv("TWEET_TEST_SENTINEL", "inherited-test-value")
    session = launch_screen(path, account_state, campaign["digest"], stub)
    try:
        deadline = time.monotonic() + 5
        while not (account_state / "inherited.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (account_state / "inherited.txt").read_text() == "inherited-test-value"
    finally:
        subprocess.run(["screen", "-S", session, "-X", "quit"], check=False)


@pytest.mark.parametrize("candidate_handle", [None, "winner3"])
def test_untagged_winner_requires_named_review_exception(campaign_plan: Path, account_state: Path, candidate_handle: str | None) -> None:
    """Compile a named winner without a verified handle only with editor approval.

    The runner looks up only tagged identities, retaining the untagged winner
    and explicit exception in the immutable campaign.

    :param campaign_plan: Approved fixture to hand edit.
    :param account_state: Durable test state.
    """
    evidence_path = campaign_plan.with_name("chart-evidence.json")
    evidence = json.loads(evidence_path.read_text())
    evidence["lending_performance"]["winners"][2].update(handle=candidate_handle, verified_at=None)
    write_json(evidence_path, evidence)
    plan = read_plan(campaign_plan)
    plan.entries[0].body = plan.entries[0].body.replace("@winner3", "Winner 3")
    campaign_plan.write_text(render_plan(plan))
    with pytest.raises(ValueError, match="missing_handle"):
        compile_campaign(campaign_plan)
    plan.entries[0].metadata["acknowledged_exceptions"] = [{"code": "missing_handle", "rationale": "Name Winner 3 without a tag; editor reviewed this exception"}]
    campaign_plan.write_text(render_plan(plan))
    path = compile_campaign(campaign_plan)
    assert load_campaign(path)["entries"][0]["acknowledged_exceptions"][0]["code"] == "missing_handle"
    clock = [parse_utc("2026-10-02T00:00:00Z").replace(tzinfo=datetime.UTC).timestamp() - 1000]
    writer = Mock()
    writer.fetch_identity.return_value = {"id": "123", "username": "TradingProtocol"}
    writer.fetch_handles.return_value = {f"winner{i}": {"id": str(i)} for i in (1, 2)}
    writer.upload_chart.return_value = UploadedMedia("media", 9999999999)
    writer.create_post.return_value = "321"
    run_campaign(path, account_state, writer, lambda _: None, max_sends=1, now=lambda: clock[0], wait=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    writer.fetch_handles.assert_called_once_with(["winner1", "winner2"])


def test_reuploaded_chart_keys_and_override_receipts(campaign_plan: Path) -> None:
    """Retain logical chart identity for suffixed or renamed Ghost uploads.

    An override choosing an existing winner uses its verified receipt instead
    of the unverified duplicate in the raw entity catalogue.

    :param campaign_plan: Approved campaign with chart evidence.
    """
    charts, _ = extract_charts('<h2>Same heading</h2><img src="https://storage.ghost.io/protocol_tvl-2.png"><img src="https://storage.ghost.io/chain_tvl-3.png">')
    assert [c["key"] for c in charts] == ["protocol_tvl", "chain_tvl"]
    charts, _ = extract_charts('<h2>Same heading</h2><img src="https://storage.ghost.io/renamed.png">', {"https://storage.ghost.io/renamed.png": "protocol_tvl"})
    assert charts[0]["key"] == "protocol_tvl"
    evidence_path = campaign_plan.with_name("chart-evidence.json")
    evidence = json.loads(evidence_path.read_text())
    record = evidence["lending_performance"]
    record["entities"] = [winner | {"verified_at": None} for winner in record["winners"]]
    write_json(evidence_path, evidence)
    plan = read_plan(campaign_plan)
    plan.entries[0].metadata["selection_override"] = {"entity_ids": ["1", "2", "3"], "metric": record["metric"], "rationale": "Review confirms the same chart-bound winners"}
    campaign_plan.write_text(render_plan(plan))
    assert load_campaign(compile_campaign(campaign_plan))["entries"][0]["winners"][0]["verified_at"] == "2026-10-01"


def test_unicode_unreviewed_mention_and_wrong_variant_block(campaign_plan: Path) -> None:
    """Reject X Unicode mentions and a locally swapped unreviewed chart variant.

    Exact image binding cannot be overridden through an evidence hash field.

    :param campaign_plan: Source-bound campaign.
    """
    campaign_plan.write_text(campaign_plan.read_text().replace("October's leaders", "October's leaders ＠unreviewed", 1))
    with pytest.raises(ValueError, match="Mention has no reviewed"):
        compile_campaign(campaign_plan)
    image = campaign_plan.parent / "charts/lending_performance.png"
    Image.new("RGB", (800, 500), "blue").save(image)
    evidence_path = campaign_plan.with_name("chart-evidence.json")
    evidence = json.loads(evidence_path.read_text())
    evidence["lending_performance"]["attachment_sha256"] = file_hash(image)
    write_json(evidence_path, evidence)
    with pytest.raises(ValueError, match="Changed or unbound image"):
        validate_plan(campaign_plan)


def test_regeneration_carries_verification_but_rechecks_context(campaign_plan: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Carry reviewed wording and identity receipts only across unchanged context.

    An editorial heading change retains wording as a suggestion but removes
    approval, even when the actual chart image and ranking are unchanged.

    :param campaign_plan: Reviewed source revision.
    :param tmp_path: New revision directories.
    :param monkeypatch: Deterministic chart reads.
    """
    source_path = campaign_plan.with_name("source-post.json")
    source = json.loads(source_path.read_text())
    for chart in source["charts"]:
        chart["alt"] = "Reviewed chart"
    source["digest"] = digest(source_fields(source))
    write_json(source_path, source)
    plan = read_plan(campaign_plan)
    plan.header["source_digest"] = source["digest"]
    campaign_plan.write_text(render_plan(plan))
    evidence = json.loads(campaign_plan.with_name("chart-evidence.json").read_text())
    for record in evidence.values():
        for winner in record["winners"]:
            winner["verified_at"] = None
    raw_evidence = tmp_path / "raw-evidence.json"
    write_json(raw_evidence, evidence)
    html = "".join(f'<h3>{key}</h3><figure><img alt="Reviewed chart" src="https://storage.ghost.io/{key}.png"></figure><table><tr><td>Ranking context</td></tr></table>' for key in ("lending_performance", "protocol_yields"))
    client = Mock()
    client.fetch_post_by_slug.return_value = GhostPost(id="post", slug="report", title=source["title"], status="published", html=html, published_at=None, updated_at=None)
    data = (campaign_plan.parent / "charts/lending_performance.png").read_bytes()
    monkeypatch.setattr("eth_defi.vault_report.tweet_plan.fetch_image", lambda *_: data)
    unchanged = prepare_campaign(client, "report", tmp_path / "revisions", raw_evidence, campaign_plan)
    assert read_plan(unchanged).entries[0].metadata["status"] == "approved"
    carried = json.loads(unchanged.with_name("chart-evidence.json").read_text())
    assert carried["lending_performance"]["winners"][0]["verified_at"] == "2026-10-01"
    client.fetch_post_by_slug.return_value.html = html.replace("<h3>lending_performance</h3>", "<h3>Updated lending context</h3>")
    changed = prepare_campaign(client, "report", tmp_path / "revisions", raw_evidence, campaign_plan)
    assert read_plan(changed).entries[0].metadata["status"] == "draft"
    assert read_plan(changed).entries[0].body == read_plan(campaign_plan).entries[0].body


def test_known_rate_limit_retries_after_reset(campaign_plan: Path, account_state: Path) -> None:
    """Retry a definite provider rejection after reset without a catch-up burst.

    A failed create remains pending, while the next confirmed dispatch controls
    the account-wide spacing for later chart entries.

    :param campaign_plan: Reviewed bundle.
    :param account_state: Durable isolated queue.
    """
    path = compile_campaign(campaign_plan)
    start = parse_utc("2026-10-02T00:00:00Z").replace(tzinfo=datetime.UTC).timestamp()
    clock = [start - 1000]
    writer = Mock()
    writer.fetch_identity.return_value = {"id": "123", "username": "TradingProtocol"}
    writer.fetch_handles.return_value = {f"winner{i}": {"id": str(i)} for i in (1, 2, 3)}
    writer.upload_chart.return_value = UploadedMedia("media", 9999999999)
    writer.create_post.side_effect = [TwitterRateLimit(start + 30), "555"]
    run_campaign(path, account_state, writer, lambda _: None, max_sends=1, now=lambda: clock[0], wait=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert clock[0] == start + 30
    assert writer.create_post.call_count == 2
    ledger = TweetLedger(account_state)
    try:
        assert ledger.db.execute("SELECT status,tweet_id FROM tweets WHERE id='post:lending_performance'").fetchone()[:] == ("sent", "555")
        assert ledger.effective_time(load_campaign(path)["entries"][1]) == start + 43230
    finally:
        ledger.close()


def test_retweet_mention_alt_brackets_and_line_endings(campaign_plan: Path) -> None:
    """Detect RT-prefix mentions and preserve bracketed alt text on round trip.

    Preview and compilation reject Windows line endings consistently, so the
    review can never silently display different body bytes than the runner.

    :param campaign_plan: Editable campaign.
    """
    plan = read_plan(campaign_plan)
    plan.entries[0].alt = "Chart [3M returns]"
    campaign_plan.write_text(render_plan(plan))
    assert read_plan(campaign_plan).entries[0].alt == "Chart [3M returns]"
    compile_campaign(campaign_plan)
    plan.entries[0].body += " RT@unreviewed"
    campaign_plan.write_text(render_plan(plan))
    with pytest.raises(ValueError, match="Mention has no reviewed"):
        compile_campaign(campaign_plan)
    campaign_plan.write_bytes(campaign_plan.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="LF line endings"):
        render_preview(campaign_plan)
    with pytest.raises(ValueError, match="LF line endings"):
        compile_campaign(campaign_plan)
