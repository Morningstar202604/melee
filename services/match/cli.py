"""melee CLI: one binary drives the whole product surface.

argparse-only by design: the CLI is the PyInstaller entry point,
so every extra dependency is a new packaging failure mode.

Three audiences:
    melee serve                      # operators: run the server
    melee games/stats/archive ...    # integrators: scripted reads
    melee match create/open ...      # players: run & watch a match
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from .version import APP_VERSION


def _request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    token: str = "",
    timeout: float = 30.0,
    lang: str = "",
) -> tuple[int, object]:
    """Thin HTTP client on stdlib only: zero packaging burden for the exe."""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    if lang:
        headers["Accept-Language"] = lang
    data = json.dumps(body).encode("utf-8") if body is not None else None
    # 结构门禁：拦 file:/ 自定义 scheme 与 userinfo（DNS 级校验太慢，CLI 逐次调用不划算）。
    # 校验失败要转成"干净的 CLI 错误"：直接把 OutboundAddressError 抛出去，
    # 用户看到的是 Python 栈回溯，而不是"这个地址不支持"。
    from .outbound import OutboundAddressError, check_url_components

    try:
        check_url_components(base)
    except OutboundAddressError as exc:
        raise SystemExit(f"error: {exc}") from None
    request = urllib.request.Request(
        base.rstrip("/") + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - scheme 已由上方 check_url_components 校验
            raw = response.read()
            if "json" in (response.headers.get("content-type") or ""):
                return response.status, json.loads(raw.decode("utf-8"))
            return response.status, raw
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, payload
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: cannot reach {base} ({exc.reason})") from None


def _fail(status: int, body: object) -> int:
    print(f"error {status}: {body}", file=sys.stderr)
    return 1


def _fetch(args, path: str, *, timeout: float = 30.0) -> tuple[int, Any]:
    """只读 GET：统一 token/lang/timeout，非 200 直接把退出码和错误打印掉。"""
    status, body = _request(
        args.api, path, token=args.token, lang=args.lang, timeout=timeout
    )
    if status != 200:
        return _fail(status, body), None
    return 0, body


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _table(rows: list, columns: list, headers: list | None = None) -> None:
    """Aligned ASCII table, stdlib only."""
    headers = headers or columns
    widths = {}
    for column, head in zip(columns, headers, strict=True):
        cells = [str(row.get(column, "")) for row in rows]
        widths[column] = max(len(str(head)), *(len(c) for c in cells)) if cells else len(str(head))
    line = "  ".join(str(h).ljust(widths[c]) for c, h in zip(columns, headers, strict=True))
    print(line)
    print("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        print("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))



# ---------- serve: operators entry, the exe default ----------


def _cmd_serve(args) -> int:
    _add_repo_root_to_path()
    if args.db:
        os.environ["MELEE_DB"] = args.db
    os.environ.setdefault("MELEE_PORT", str(args.port))
    os.environ.setdefault("MELEE_HOST", args.host)
    import uvicorn

    config = uvicorn.Config(
        _serve_target(),
        host=args.host,
        port=args.port,
        proxy_headers=False,
        timeout_graceful_shutdown=10,
    )
    uvicorn.Server(config).run()
    return 0


def _serve_target():
    """Build the FastAPI app; import lazily so read-only commands stay fast."""
    from .api import create_app

    return create_app()


def _add_repo_root_to_path() -> None:
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)


# ---------- read-only query commands ----------


def _cmd_games(args) -> int:
    code, body = _fetch(args, "/games")
    if code:
        return code
    if args.json:
        _print_json(body)
        return 0
    rows = [
        {
            "id": g["gameId"],
            "label": g["label"],
            "players": "x".join(str(n) for n in g["players"]),
            "source": g["source"],
        }
        for g in body["games"]
    ]
    _table(rows, ["id", "label", "players", "source"])
    return 0


def _cmd_stats(args) -> int:
    code, body = _fetch(args, "/stats")
    if code:
        return code
    if args.json:
        _print_json(body)
        return 0
    summary = (
        f"matches={body.get('matches', 0)}  finished={body.get('finished', 0)}  "
        f"events={body.get('events', 0)}  live={body.get('live', 0)}"
    )
    print(summary)
    by_game = body.get("byGame") or []
    if by_game:
        _table(
            [{"game": g["label"], "matches": g["matches"]} for g in by_game[:12]],
            ["game", "matches"],
        )
    return 0



def _cmd_archive(args) -> int:
    from urllib.parse import urlencode

    params = {"limit": args.limit, "offset": args.offset}
    if args.game:
        params["game"] = args.game
    if args.status:
        params["status"] = args.status
    if args.q:
        params["q"] = args.q
    code, body = _fetch(args, "/archive?" + urlencode(params))
    if code:
        return code
    if args.json:
        _print_json(body)
        return 0
    rows = [
        {
            "matchId": item["matchId"],
            "game": item["gameLabel"],
            "status": item["status"],
            "winner": item.get("winner") or "-",
            "events": item["events"],
        }
        for item in body["items"]
    ]
    _table(rows, ["matchId", "game", "status", "winner", "events"])
    return 0


def _cmd_health(args) -> int:
    deadline = time.time() + max(0, args.wait)
    while True:
        try:
            status, body = _request(args.api, "/health", timeout=5)
            if status == 200:
                if args.json:
                    _print_json(body)
                else:
                    print("ok", json.dumps(body.get("checks", {})))
                return 0
        except SystemExit:
            pass
        if time.time() >= deadline:
            print(f"error: service not healthy at {args.api}", file=sys.stderr)
            return 1
        time.sleep(1)


# ---------- match commands: run & consume matches ----------


def _cmd_match_create(args) -> int:
    players = [{"id": name} for name in args.player]
    if args.delay:
        for spec in players:
            spec["delayMs"] = args.delay
    body = {"game": args.game, "players": players}
    if args.seed is not None:
        body["seed"] = args.seed
    if args.max_questions:
        body["maxQuestions"] = args.max_questions
    status, result = _request(
        args.api, "/matches", method="POST", body=body, token=args.token
    )
    if status not in (200, 201):
        return _fail(status, result)
    match_id = result["matchId"]
    if args.json:
        _print_json(result)
    else:
        print(match_id)
    if args.open_browser:
        _open_watch(args.api, match_id)
    return 0


def _cmd_match_get(args) -> int:
    code, body = _fetch(args, "/matches/" + args.match_id + "/recap?lang=" + args.lang)
    if code:
        return code
    if args.json:
        _print_json(body)
        return 0
    print(body.get("oneLine", ""))
    print(
        f"turns={body.get('turns', 0)}  fouls={body.get('rejected', 0)}  "
        f"problems={len(body.get('problems', []))}"
    )
    for highlight in body.get("highlights", []):
        print(" -", highlight.get("title", ""), "(seq", str(highlight.get("seq")) + ")")
    return 0



def _cmd_match_events(args) -> int:
    """Stream events to stdout; --follow polls until the match ends.

    SSE is the browser path; a CLI consumer wants newline-delimited JSON,
    which is exactly what /events returns, so polling with after= is
    both simpler and resumable.
    """
    after = args.after
    ended = False
    while True:
        status, body = _request(
            args.api,
            f"/matches/{args.match_id}/events?after={after}",
            token=args.token,
            lang=args.lang,
        )
        if status != 200:
            return _fail(status, body)
        events = body.get("events", [])
        for event in events:
            if event.get("type") == "match.ended":
                ended = True
            line = json.dumps(event, ensure_ascii=False)
            if args.type:
                if event.get("type") not in args.type.split(","):
                    continue
            print(line)
        if events:
            after = events[-1]["seq"]
        if not args.follow or ended:
            return 0
        time.sleep(args.interval)


def _open_watch(api: str, match_id: str, lang: str = "") -> None:
    query = "match=" + match_id
    if lang:
        query = "lang=" + lang + "&" + query
    url = api.rstrip("/") + "/watch.html?" + query
    print(url)
    webbrowser.open(url)


def _cmd_match_open(args) -> int:
    _open_watch(args.api, args.match_id, args.lang)
    return 0


# ---------- season commands ----------


def _cmd_seasons(args) -> int:
    code, body = _fetch(args, "/seasons")
    if code:
        return code
    items = body.get("seasons", [])
    if args.json:
        _print_json(body)
        return 0
    rows = [
        {
            "seasonId": s["seasonId"],
            "name": s["name"],
            "game": s.get("game", ""),
            "fixtures": s.get("fixtures", 0),
            "played": s.get("played", 0),
        }
        for s in items
    ]
    _table(rows, ["seasonId", "name", "game", "fixtures", "played"])
    return 0


def _cmd_webhooks(args) -> int:
    """Webhook 投递记录，或对某场比赛重发。"""
    if args.redispatch:
        if not args.match_id:
            print("error: --redispatch 需要给出 matchId", file=sys.stderr)
            return 1
        status, body = _request(
            args.api, "/webhooks/redispatch", method="POST",
            body={"matchId": args.match_id}, token=args.token, timeout=120.0,
        )
        if status != 200:
            return _fail(status, body)
        if args.json:
            _print_json(body)
            return 0
        for item in body.get("results", []):
            print(("ok   " if item.get("delivered") else "fail ") + item.get("url", ""))
        return 0

    query = f"/webhooks/deliveries?limit={args.limit}"
    if args.match_id:
        query += f"&match_id={args.match_id}"
    code, body = _fetch(args, query)
    if code:
        return code
    if args.json:
        _print_json(body)
        return 0
    summary = body.get("summary", {})
    print(f"total={summary.get('total', 0)} failed={summary.get('failed', 0)}")
    rows = [
        {
            "at": item.get("at", ""),
            "matchId": item.get("matchId", ""),
            "status": item.get("status", ""),
            "http": item.get("httpCode") if item.get("httpCode") is not None else "-",
            "attempts": item.get("attempts", ""),
            "error": (item.get("error") or "")[:40],
        }
        for item in body.get("items", [])
    ]
    if rows:
        _table(rows, ["at", "matchId", "status", "http", "attempts", "error"])
    return 0


def _cmd_season_run(args) -> int:
    status, body = _request(
        args.api,
        f"/seasons/{args.season_id}/run",
        method="POST",
        body={},
        token=args.token,
        timeout=600.0,
    )
    if status != 200:
        return _fail(status, body)
    if args.json:
        _print_json(body)
        return 0
    print("played:", body.get("played", 0), " skipped:", body.get("skipped", 0))
    for outcome in body.get("matches", []):
        print(" -", outcome.get("matchId"), outcome.get("winner") or "draw")
    return 0



# ---------- argument parser ----------


def _global_parser(p: argparse.ArgumentParser) -> None:
    """子命令级全局旗标：默认 None，让 main() 能区分"没传"与"顶层值"。

    顶层也接受同一组旗标（见 build_parser 的 top_* dest），
    所以 `melee --api URL games` 与 `melee games --api URL` 都合法。
    """
    p.add_argument("--api", dest="api", default=None)
    p.add_argument("--token", dest="token", default=None)
    p.add_argument("--lang", dest="lang", default=None, choices=["en", "zh"])
    p.add_argument("--json", dest="json", action="store_true", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="melee",
        description="melee broadcast studio command line",
    )
    parser.add_argument("--version", action="version", version="melee " + APP_VERSION)
    # 顶层也接受全局旗标（dest 用 top_ 前缀，避免被子命令默认值覆盖）。
    parser.add_argument("--api", dest="top_api", default=os.environ.get("MELEE_API", "http://127.0.0.1:8099"))
    parser.add_argument("--token", dest="top_token", default=os.environ.get("MELEE_TOKEN", ""))
    parser.add_argument("--lang", dest="top_lang", default=os.environ.get("MELEE_LANG", "en"), choices=["en", "zh"])
    parser.add_argument("--json", dest="top_json", action="store_true", default=False)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the melee service (the exe default)")
    _global_parser(serve)
    serve.add_argument("--host", default=os.environ.get("MELEE_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("MELEE_PORT", "8099")))
    serve.add_argument("--db", default=os.environ.get("MELEE_DB", ""))
    serve.set_defaults(func=_cmd_serve)

    games = sub.add_parser("games", help="list registered game types")
    _global_parser(games)
    games.set_defaults(func=_cmd_games)

    stats = sub.add_parser("stats", help="site statistics")
    _global_parser(stats)
    stats.set_defaults(func=_cmd_stats)

    archive = sub.add_parser("archive", help="list matches")
    _global_parser(archive)
    archive.add_argument("--limit", type=int, default=12)
    archive.add_argument("--offset", type=int, default=0)
    archive.add_argument("--game", default="")
    archive.add_argument("--status", default="", choices=["", "live", "ended"])
    archive.add_argument("--q", default="")
    archive.set_defaults(func=_cmd_archive)

    health = sub.add_parser("health", help="probe /health; --wait N retries N seconds")
    _global_parser(health)
    health.add_argument("--wait", type=int, default=0)
    health.set_defaults(func=_cmd_health)

    match = sub.add_parser("match", help="create / inspect / open matches")
    match_sub = match.add_subparsers(dest="match_command", required=True)

    create = match_sub.add_parser("create", help="create a match")
    _global_parser(create)
    create.add_argument("--game", default="twenty-questions")
    create.add_argument("--seed", type=int, default=None)
    create.add_argument("--player", action="append", required=True)
    create.add_argument("--delay", type=int, default=None, help="per-move delay in ms")
    create.add_argument("--max-questions", type=int, default=None)
    create.add_argument("--open", dest="open_browser", action="store_true")
    create.set_defaults(func=_cmd_match_create)

    get = match_sub.add_parser("get", help="recap summary of a match")
    _global_parser(get)
    get.add_argument("match_id")
    get.set_defaults(func=_cmd_match_get)

    events = match_sub.add_parser("events", help="dump / follow the event stream")
    _global_parser(events)
    events.add_argument("match_id")
    events.add_argument("--after", type=int, default=-1)
    events.add_argument("--follow", action="store_true")
    events.add_argument("--interval", type=float, default=1.0)
    events.add_argument("--type", default="", help="comma-separated type filter")
    events.set_defaults(func=_cmd_match_events)

    export = match_sub.add_parser("export", help="export a match to a portable single file")
    _global_parser(export)
    export.add_argument("match_id")
    export.add_argument("--out", default="")
    export.set_defaults(func=_cmd_match_export)

    imp = match_sub.add_parser("import", help="import a portable file as a replay-only match")
    _global_parser(imp)
    imp.add_argument("file")
    imp.add_argument("--open", dest="open_browser", action="store_true")
    imp.set_defaults(func=_cmd_match_import)

    ep = match_sub.add_parser(
        "import-episode",
        help="import a Kaggle/TextArena/melee episode as a replay-only broadcast",
    )
    _global_parser(ep)
    ep.add_argument("file")
    ep.add_argument(
        "--format",
        default="auto",
        choices=("auto", "kaggle", "textarena", "episode"),
        help="input format (default: auto-detect)",
    )
    ep.add_argument("--open", dest="open_browser", action="store_true")
    ep.add_argument("--license", default="", help="dataset license shown on the watch page (e.g. CC BY 4.0)")
    ep.add_argument("--source-url", dest="source_url", default="", help="original dataset/episode URL shown in provenance")
    ep.add_argument("--source-label", dest="source_label", default="", help="human-readable source label")
    ep.set_defaults(func=_cmd_match_import_episode)

    live = match_sub.add_parser(
        "import-live",
        help="push one external episode snapshot into a live broadcast",
    )
    _global_parser(live)
    live.add_argument("file", help="JSON snapshot containing steps up to now")
    live.add_argument(
        "--format",
        default="auto",
        choices=("auto", "kaggle", "textarena", "episode"),
        help="input format (default: auto-detect)",
    )
    live.add_argument("--finished", action="store_true", help="mark this snapshot as the external final state")
    live.add_argument("--license", default="", help="dataset license shown on the watch page")
    live.add_argument("--source-url", dest="source_url", default="")
    live.add_argument("--source-label", dest="source_label", default="")
    live.set_defaults(func=_cmd_match_import_live)

    batch = match_sub.add_parser(
        "import-batch",
        help="import a directory/glob of Kaggle/TextArena episodes and write a report",
    )
    _global_parser(batch)
    batch.add_argument("path", help="file, directory, or path to scan")
    batch.add_argument("--pattern", default="*.json", help="glob under a directory (default: *.json)")
    batch.add_argument(
        "--format",
        default="auto",
        choices=("auto", "kaggle", "textarena", "episode"),
        help="input format (default: auto-detect)",
    )
    batch.add_argument("--limit", type=int, default=0, help="import at most N files (0 = all)")
    batch.add_argument(
        "--sample", default="hash", choices=("hash", "random", "first"),
        help="how to pick files: hash (default, reproducible spread), random, or first",
    )
    batch.add_argument("--shuffle", type=int, default=None, metavar="SEED", help="shuffle files with a deterministic seed")
    batch.add_argument(
        "--via-api", dest="via_api", action="store_true",
        help="POST each episode to the running API (auth/quota/rate limits are enforced)",
    )
    batch.add_argument("--api-delay", dest="api_delay", type=float, default=0.2,
        help="seconds between API calls (default 0.2)")
    batch.add_argument("--db", default=os.environ.get("MELEE_DB", "melee.db"), help="SQLite database for direct mode; ignored with --via-api")
    batch.add_argument("--license", default="", help="dataset license recorded on every imported episode")
    batch.add_argument("--source-url", dest="source_url", default="", help="original dataset URL recorded on every imported episode")
    batch.add_argument("--source-label", dest="source_label", default="", help="human-readable source label")
    batch.add_argument("--out", default="", help="write the full JSON report here")
    batch.add_argument("--open-first", dest="open_first", action="store_true", help="open the first imported match")
    batch.set_defaults(func=_cmd_match_import_batch)

    open_cmd = match_sub.add_parser("open", help="open the watch page in a browser")
    _global_parser(open_cmd)
    open_cmd.add_argument("match_id")
    open_cmd.set_defaults(func=_cmd_match_open)

    hooks = sub.add_parser("webhooks", help="inspect webhook deliveries, or re-send one")
    _global_parser(hooks)
    hooks.add_argument("match_id", nargs="?", default="", help="filter by match, or the match to re-send")
    hooks.add_argument("--limit", type=int, default=20, help="how many records (1-500)")
    hooks.add_argument("--redispatch", action="store_true", help="re-send the terminal webhook for this match")
    hooks.set_defaults(func=_cmd_webhooks)

    seasons = sub.add_parser("seasons", help="list seasons")
    _global_parser(seasons)
    seasons.set_defaults(func=_cmd_seasons)

    mcp = sub.add_parser("mcp", help="expose melee as MCP tools over stdio (for Claude/Cursor/agents)")
    _global_parser(mcp)
    mcp.set_defaults(func=_cmd_mcp)

    run = sub.add_parser("season-run", help="advance a season now")
    _global_parser(run)
    run.add_argument("season_id")
    run.set_defaults(func=_cmd_season_run)

    return parser


def _resolve_globals(args: argparse.Namespace) -> None:
    """子命令级旗标优先；没传时落到顶层值（顶层默认已读环境变量）。"""
    for name in ("api", "token", "lang"):
        if getattr(args, name, None) is None:
            setattr(args, name, getattr(args, f"top_{name}"))
    if not args.json:
        args.json = args.top_json


def _cmd_mcp(args) -> int:
    if args.token and not os.environ.get("MELEE_TOKEN"):
        os.environ["MELEE_TOKEN"] = args.token
    if args.api and args.api != "http://127.0.0.1:8099" and not os.environ.get("MELEE_API"):
        os.environ["MELEE_API"] = args.api
    from .mcp_server import main as mcp_main

    return mcp_main()


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _resolve_globals(args)
    return args.func(args)




def _cmd_match_export(args) -> int:
    status, body = _request(
        args.api, "/matches/" + args.match_id + "/export", token=args.token
    )
    if status != 200:
        return _fail(status, body)
    out = Path(args.out) if args.out else Path(args.match_id + ".melee.json")
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print("exported:", out, "events:", len(body.get("events", [])))
    return 0


def _cmd_match_import_episode(args) -> int:
    from .episode import MAX_EPISODE_BYTES

    file = Path(args.file)
    if file.stat().st_size > MAX_EPISODE_BYTES:
        print(f"error: episode file exceeds {MAX_EPISODE_BYTES} bytes", file=sys.stderr)
        return 1
    document = json.loads(file.read_text(encoding="utf-8"))
    path = f"/matches/import-episode?format={args.format}"
    status, body = _request(
        args.api, path, method="POST",
        body={"format": args.format, "episode": document, "metadata": _episode_metadata(args)},
        token=args.token,
    )
    if status != 200:
        return _fail(status, body)
    if args.json:
        _print_json(body)
    else:
        print(
            "imported episode:",
            body.get("matchId"),
            "source:",
            body.get("source"),
            "winner:",
            body.get("winner") or "—",
            "events:",
            body.get("events"),
        )
    if args.open_browser:
        _open_watch(args.api, body.get("matchId", ""), args.lang)
    return 0


def _episode_metadata(args) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "license": getattr(args, "license", ""),
            "sourceUrl": getattr(args, "source_url", ""),
            "sourceLabel": getattr(args, "source_label", ""),
        }.items()
        if value
    }


def _cmd_match_import_live(args) -> int:
    from .episode import MAX_EPISODE_BYTES

    file = Path(args.file)
    if file.stat().st_size > MAX_EPISODE_BYTES:
        print(f"error: episode file exceeds {MAX_EPISODE_BYTES} bytes", file=sys.stderr)
        return 1
    document = json.loads(file.read_text(encoding="utf-8"))
    body = {
        "format": args.format,
        "episode": document,
        "finished": bool(args.finished),
        "metadata": _episode_metadata(args),
    }
    status, result = _request(
        args.api, "/matches/import-episode/live", method="POST", body=body, token=args.token
    )
    if status != 200:
        return _fail(status, result)
    if args.json:
        _print_json(result)
    else:
        print(
            "live episode:",
            result.get("matchId"),
            "steps:",
            result.get("steps"),
            "appended:",
            result.get("appended"),
            "state:",
            "finished" if result.get("finished") else "live",
        )
        print("watch:", args.api.rstrip("/") + "/watch.html?match=" + str(result.get("matchId")))
    return 0


def _cmd_match_import_batch(args) -> int:
    from .batch import discover_episode_files, import_batch, write_report

    try:
        files = discover_episode_files(args.path, pattern=args.pattern)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    quota_exhausted = False

    def via_api(document: dict[str, Any]) -> dict[str, Any]:
        import time as _time

        nonlocal quota_exhausted
        body = {"format": args.format, "episode": document, "metadata": _episode_metadata(args)}
        for attempt in range(4):
            status, payload = _request(
                args.api, "/matches/import-episode", method="POST", body=body,
                token=args.token, lang=args.lang,
            )
            if status == 200 and isinstance(payload, dict):
                if args.api_delay > 0:
                    _time.sleep(args.api_delay)
                return payload
            detail = str(payload)
            if status == 429 and "配额已用尽" in detail:
                # 日配额不是瞬时限流，重试只会把整批拖成几十个失败项。
                quota_exhausted = True
                raise RuntimeError(f"HTTP {status}: {payload}")
            if status == 429 and attempt < 3:
                _time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {status}: {payload}")
        raise RuntimeError("HTTP 429: request was still rate-limited after retries")

    def on_item(item: dict[str, Any]) -> None:
        if args.json:
            return
        if item.get("status") in ("imported", "duplicate"):
            print(
                f"[{item.get('matchId')}] {Path(item['file']).name} "
                f"{item.get('game')} / {item.get('winner') or '—'}",
                file=sys.stderr,
            )
        else:
            print(f"[failed] {Path(item['file']).name}: {item.get('error')}", file=sys.stderr)

    report = import_batch(
        args.db,
        files,
        fmt=args.format,
        limit=args.limit,
        shuffle_seed=args.shuffle,
        sample=args.sample,
        on_item=on_item,
        import_document=via_api if args.via_api else None,
        metadata=_episode_metadata(args),
        should_stop=(lambda: quota_exhausted) if args.via_api else None,
    )
    if args.via_api:
        report["transport"] = "api"
        report["api"] = args.api
    if args.out:
        out = write_report(report, args.out)
        print("report:", out)
    if args.json:
        _print_json(report)
    else:
        print(
            f"batch import: {report['imported']} imported / {report.get('duplicates', 0)} duplicate / "
            f"{report['failed']} failed (selected {report['selected']} of {report['found']})"
            + ("  [stopped: daily quota exhausted]" if quota_exhausted else "")
        )
        failures = [item for item in report["items"] if item.get("status") != "imported"]
        if failures:
            _table(
                [
                    {"file": Path(item["file"]).name, "error": item.get("error")}
                    for item in failures[:12]
                ],
                ["file", "error"],
            )
        first = next(
            (item for item in report["items"] if item.get("status") == "imported"),
            None,
        )
        if first:
            print(
                "first:",
                first.get("matchId"),
                "watch:",
                args.api.rstrip("/") + "/watch.html?match=" + str(first.get("matchId")),
            )
            if args.open_first:
                _open_watch(args.api, first.get("matchId", ""), args.lang)
    return 0 if report["failed"] == 0 else 2


def _cmd_match_import(args) -> int:
    document = json.loads(Path(args.file).read_text(encoding="utf-8"))
    status, body = _request(
        args.api, "/matches/import", method="POST", body=document, token=args.token
    )
    if status != 200:
        return _fail(status, body)
    if args.json:
        _print_json(body)
    else:
        print("imported:", body.get("matchId"), "events:", body.get("events"))
    if args.open_browser:
        _open_watch(args.api, body.get("matchId", ""), args.lang)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
