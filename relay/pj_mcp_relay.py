#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""MCP over stdio for PlotJuggler running in a web browser (app.plotjuggler.io).

A web page can neither listen on a port nor be started by an agent, so the page connects OUT to
this relay on 127.0.0.1 and long-polls it; the agent talks MCP to the relay over stdio. Python
standard library only: nothing to install. One relay per agent session.

Page protocol (PlotJuggler's McpBrowserLink, docs/MCP_SERVER.md "Browser relay"). Every request
carries `X-PJ-Link: <SECRET>`, a random secret `launch` hands out in the page URL it opens
(?mcp=<relay>/?key=<SECRET>), kept in the user's cache (mode 0600) so tabs survive a relay restart. Pages and
processes without it cannot join as PlotJuggler. The secret is as private as that URL: it is in the browser's
history and command line and reaches the app's server in the query, and it is only good against this loopback relay. Being
a custom header it also makes a browser ask first (CORS preflight), answered only for ALLOWED_ORIGINS.

  POST /link/hello {"title", "protocol", "browser"} -> {"id"}      a page joins (one instance)
  GET  /link/next?id=                      -> {"seq", "client", "message"} | 204 after POLL_S | 404 unknown id
  POST /link/reply?id= {"seq", "response"} -> 204                  the answer to message `seq`
  GET  /file/<token>/<name>                -> the file open_file named (the page cannot read the disk)

Environment: PJ_RELAY_PORT (default: the first free of 7381-7390), PJ_WASM_URL (the app to open,
default https://app.plotjuggler.io/), PJ_RELAY_ORIGINS (more allowed origins, comma-separated, e.g. a
local http://127.0.0.1:8080 serving the WASM package).
"""

import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.3.0"
WASM_URL = os.environ.get("PJ_WASM_URL", "https://app.plotjuggler.io/")
POLL_S = 20  # a page's poll is held this long when there is nothing to send
LIVE_S = 120  # a page that has not polled for this long is gone (closed tab, or a page frozen that long)
CALL_TIMEOUT_S = 300  # as the desktop bridge: a long import or a dialog may hold a call
LAUNCH_TIMEOUT_S = 180  # the first load of the app downloads tens of MB
HERE = Path(__file__).resolve().parent
BUNDLED_TOOLS = HERE / "browser-tools.json"
CACHED_TOOLS = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "plotjuggler-agent" / "browser-tools.json"


def origin_of(url):
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"




def relay_key():
    """The secret pages must send. Kept (mode 0600) across runs, so a tab outlives a relay restart: the new run
    answers it 404 and it says hello again. PJ_RELAY_KEY overrides it (tests)."""
    if os.environ.get("PJ_RELAY_KEY"):
        return os.environ["PJ_RELAY_KEY"]
    path = CACHED_TOOLS.parent / "relay-key"
    try:
        key = path.read_text().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(24)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        os.chmod(path, 0o600)
    except OSError as e:
        print("pj_mcp_relay: cannot keep the relay key, tabs will not survive a restart:", e, file=sys.stderr)
    return key


SECRET = relay_key()
ALLOWED_ORIGINS = {origin_of(WASM_URL), "https://app.plotjuggler.io"} | {
    o.strip().rstrip("/") for o in os.environ.get("PJ_RELAY_ORIGINS", "").split(",") if o.strip()
}


def origin_allowed(origin):
    """No Origin: not a browser (PlotJuggler desktop's --mcp-relay), which still needs the secret. A local server
    (serve_wasm.mjs) gets in only through PJ_RELAY_ORIGINS."""
    return origin is None or origin in ALLOWED_ORIGINS


def log(*args):
    print("pj_mcp_relay:", *args, file=sys.stderr, flush=True)


class Tab:
    def __init__(self, title, browser):
        self.title = title
        self.browser = browser  # its tool list is the browser build's
        self.last_seen = time.monotonic()
        self.queue = []  # messages for the page, oldest first
        self.cond = threading.Condition()

    def live(self):
        return time.monotonic() - self.last_seen < LIVE_S


class Relay:
    def __init__(self):
        self.lock = threading.Lock()
        self.tabs = {}  # id -> Tab
        self.selected = None
        self.client = ""
        self.seq = 0
        self.waiting = {}  # seq -> [event, response]
        self.files = {}  # token -> path
        self.base = ""
        self.joined = threading.Condition(self.lock)

    # --- the page side -------------------------------------------------------------------------

    def hello(self, title, browser):
        with self.lock:
            tab_id = secrets.token_hex(16)
            self.tabs[tab_id] = Tab(str(title)[:200], browser is True)
            self.joined.notify_all()
        log("page joined:", tab_id)
        return tab_id

    def next_for(self, tab_id):
        """The page's next message, None after POLL_S, or KeyError for an unknown page."""
        tab = self.tabs[tab_id]
        tab.last_seen = time.monotonic()
        with tab.cond:
            tab.cond.wait_for(lambda: tab.queue, timeout=POLL_S)
            item = tab.queue.pop(0) if tab.queue else None
        tab.last_seen = time.monotonic()
        return item

    def reply(self, tab_id, seq, response):
        with self.lock:
            waiter = self.waiting.get(seq)
            if tab_id not in self.tabs or waiter is None or waiter[2] != tab_id:
                return
            waiter[1] = response
            waiter[0].set()

    # --- the agent side ------------------------------------------------------------------------

    def live_tabs(self):
        with self.lock:
            for tab_id in [t for t, tab in self.tabs.items() if not tab.live()]:
                del self.tabs[tab_id]
            return dict(self.tabs)

    def current(self):
        """The selected page while it lives, else the only live one; None otherwise."""
        tabs = self.live_tabs()
        if self.selected in tabs:
            return self.selected
        return next(iter(tabs)) if len(tabs) == 1 and self.selected is None else None

    def ask(self, tab_id, message, timeout=CALL_TIMEOUT_S):
        """Sends `message` to the page and waits for its JSON-RPC response; None on timeout or a closed page."""
        event = threading.Event()
        with self.lock:
            tab = self.tabs.get(tab_id)
            if tab is None:
                return None
            self.seq += 1
            seq = self.seq
            self.waiting[seq] = [event, None, tab_id]
        with tab.cond:
            tab.queue.append({"seq": seq, "client": self.client, "message": message})
            tab.cond.notify_all()
        try:
            deadline = time.monotonic() + timeout
            while not event.wait(1.0):
                if time.monotonic() > deadline or not tab.live():
                    return None
            return self.waiting[seq][1]
        finally:
            with self.lock:
                self.waiting.pop(seq, None)

    def serve_file(self, path):
        token = secrets.token_urlsafe(16)
        with self.lock:
            self.files[token] = path
        return f"{self.base}/file/{token}/{urllib.parse.quote(os.path.basename(path))}"


RELAY = Relay()


class PageHandler(BaseHTTPRequestHandler):
    server_version = "pj_mcp_relay/" + VERSION

    def log_message(self, *args):  # stdout is the MCP channel; stay quiet on stderr too
        pass

    def cors(self):
        origin = self.headers.get("Origin")
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def answer(self, status, body=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(status)
        self.cors()
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def admitted(self, link=True):
        secret_ok = hmac.compare_digest(self.headers.get("X-PJ-Link", "").encode(), SECRET.encode())
        if not origin_allowed(self.headers.get("Origin")) or (link and not secret_ok):
            self.answer(403)
            return False
        return True

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > 64 * 1024 * 1024:
            raise ValueError("bad body length")
        return json.loads(self.rfile.read(length) or b"null")

    def do_OPTIONS(self):  # CORS preflight, also Chrome's Local Network Access one
        if not origin_allowed(self.headers.get("Origin")):
            self.answer(403)
            return
        self.send_response(204)
        self.cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-PJ-Link")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path.startswith("/file/"):
            self.send_file(url.path.split("/")[2])
            return
        if url.path != "/link/next":
            self.answer(404)
            return
        if not self.admitted():
            return
        tab_id = urllib.parse.parse_qs(url.query).get("id", [""])[0]
        try:
            item = RELAY.next_for(tab_id)
        except KeyError:
            self.answer(404)
            return
        self.answer(204) if item is None else self.answer(200, item)

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path not in ("/link/hello", "/link/reply"):
            self.answer(404)
            return
        if not self.admitted():
            return
        try:
            body = self.body()
        except ValueError:
            self.answer(400)
            return
        if url.path == "/link/hello":
            body = body if isinstance(body, dict) else {}
            self.answer(200, {"id": RELAY.hello(body.get("title", ""), body.get("browser"))})
            return
        if not isinstance(body, dict) or not isinstance(body.get("seq"), int):
            self.answer(400)
            return
        RELAY.reply(urllib.parse.parse_qs(url.query).get("id", [""])[0], body["seq"], body.get("response"))
        self.answer(204)

    def send_file(self, token):
        path = RELAY.files.get(token)
        if path is None:
            self.answer(404)
            return
        if not self.admitted(link=False):
            return
        try:
            with open(path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                self.send_response(200)
                self.cors()
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                while chunk := f.read(1 << 20):
                    self.wfile.write(chunk)
        except OSError:
            self.answer(404)


# --- MCP over stdio ---------------------------------------------------------------------------------

RELAY_TOOLS = [
    {
        "name": "list_instances",
        "description": "PlotJuggler pages open in the user's web browser and connected to this session: id, title, "
        "whether this session uses it. Only needed when several are open.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "select_instance",
        "description": "Use the PlotJuggler page with this id (from list_instances) for every later call.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        "annotations": {"readOnlyHint": False, "openWorldHint": False},
    },
    {
        "name": "launch",
        "description": "Open PlotJuggler in the user's web browser (nothing to install), connected to this session, "
        "and use it. Returns once the page is ready (the first load can take a minute). Ask the user first: "
        "it opens a new browser tab.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "annotations": {"readOnlyHint": False, "openWorldHint": False},
    },
]

INSTRUCTIONS = (
    "PlotJuggler is the user's time-series and 3D data viewer. In this session it runs in the user's web "
    "browser (the desktop app is not installed), connected through a local relay. No page is connected yet: "
    "before anything else ask the user whether to open PlotJuggler in the browser now (call launch; nothing to "
    "install) or to install the desktop app from https://plotjuggler.io (then restart this session). Then start "
    "with get_status."
)


def text_result(text, error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return result


def client_name(params):
    info = params.get("clientInfo") or (params.get("_meta") or {}).get("io.modelcontextprotocol/clientInfo") or {}
    return info.get("name", "") if isinstance(info, dict) else ""


def lifecycle(method, params):
    described = {
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "plotjuggler", "title": "PlotJuggler (browser)", "version": VERSION},
        "instructions": INSTRUCTIONS,
    }
    if method == "initialize":
        asked = params.get("protocolVersion")
        known = asked in ("2025-03-26", "2025-06-18", "2025-11-25")
        return {"protocolVersion": asked if known else "2025-06-18", **described}
    if method == "server/discover":
        return {"supportedVersions": ["2026-07-28", "2025-11-25", "2025-06-18"], **described}
    if method == "ping":
        return {}
    return None


def page_tools():
    """The browser app's tools: the last list a page gave (newest build), else the one shipped here."""
    for path in (CACHED_TOOLS, BUNDLED_TOOLS):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return []


def remember_tools(tools):
    try:
        CACHED_TOOLS.parent.mkdir(parents=True, exist_ok=True)
        CACHED_TOOLS.write_text(json.dumps(tools))
    except OSError:
        pass


def list_tools():
    tab = RELAY.current()
    if tab is not None:
        response = RELAY.ask(tab, {"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}}, timeout=10)
        tools = ((response or {}).get("result") or {}).get("tools")
        if isinstance(tools, list):
            page = RELAY.live_tabs().get(tab)
            if page is not None and page.browser:  # a desktop --mcp-relay window lists desktop-only tools
                remember_tools(tools)
            return tools + RELAY_TOOLS
    return page_tools() + RELAY_TOOLS


def launch():
    before = set(RELAY.live_tabs())
    relay = f"{RELAY.base}/?key={SECRET}"
    url = WASM_URL + ("&" if "?" in WASM_URL else "?") + "mcp=" + urllib.parse.quote(relay, safe="")
    opened = webbrowser.open(url)
    with RELAY.lock:
        joined = RELAY.joined.wait_for(lambda: set(RELAY.tabs) - before, timeout=LAUNCH_TIMEOUT_S)
        if joined:
            RELAY.selected = sorted(set(RELAY.tabs) - before)[0]
    if joined:
        return text_result(json.dumps({"launched": RELAY.selected, "selected": RELAY.selected}))
    how = "It opened in the browser" if opened else "No browser could be opened from here"
    return text_result(
        f"{how}, but no PlotJuggler page connected within {LAUNCH_TIMEOUT_S} s. Ask the user to open {url} in "
        "Chrome, Edge or Firefox and to allow access to this computer if the browser asks (the page reaches "
        "this relay on 127.0.0.1), then call list_instances.",
        True,
    )


def call_tool(message, params):
    name = params.get("name")
    args = params.get("arguments") or {}
    if name == "list_instances":
        tabs = RELAY.live_tabs()
        return text_result(
            json.dumps([{"id": t, "title": tab.title, "selected": t == RELAY.selected} for t, tab in tabs.items()])
        )
    if name == "select_instance":
        if args.get("id") in RELAY.live_tabs():
            RELAY.selected = args["id"]
            return text_result(json.dumps({"selected": RELAY.selected}))
        return text_result(f"no connected PlotJuggler page with id {json.dumps(args.get('id'))} (see list_instances)", True)
    if name == "launch":
        return launch()
    tab = RELAY.current()
    if tab is None:
        if RELAY.selected is not None:
            RELAY.selected = None
            return text_result("the PlotJuggler page this session used was closed; call list_instances or launch", True)
        count = len(RELAY.live_tabs())
        if count == 0:
            return text_result(
                "PlotJuggler is not open. Ask the user whether to open it in the browser (call launch) or to "
                "install the desktop app from https://plotjuggler.io and restart this session.",
                True,
            )
        return text_result(f"{count} PlotJuggler pages are connected: call list_instances, then select_instance.", True)
    RELAY.selected = tab  # stay on it even if another page joins later
    if name == "open_file":
        # The page cannot read this computer's disk: the relay serves the file, the page downloads it.
        path = args.get("path", "")
        if not isinstance(path, str) or not os.path.isabs(path) or not os.path.isfile(path):
            return text_result("path must be an absolute path to an existing file", True)
        args = {**args, "url": RELAY.serve_file(path), "name": os.path.basename(path)}
        message = {**message, "params": {**params, "arguments": args}}
    response = RELAY.ask(tab, message)
    if response is None:
        return text_result(
            f"PlotJuggler did not answer within {CALL_TIMEOUT_S} s or its page was closed. If the page is still "
            "open it keeps working: poll get_status.",
            True,
        )
    if "error" in response:
        return text_result((response.get("error") or {}).get("message", "error"), True)
    return response.get("result", {})


OUT_LOCK = threading.Lock()
MCP_OUT = sys.stdout  # main() points it at the real stdout and fd 1 at stderr


def handle(line):
    try:
        message = json.loads(line)
    except ValueError:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        known_id = message.get("id") if isinstance(message, dict) and isinstance(message.get("id"), (str, int)) and not isinstance(message.get("id"), bool) else None
        return {"jsonrpc": "2.0", "id": known_id, "error": {"code": -32600, "message": "invalid request"}}
    if "id" not in message:
        return None  # a notification
    method = message["method"]
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if name := client_name(params):
        RELAY.client = name[:64]
    reply = {"jsonrpc": "2.0", "id": message["id"]}
    try:
        if (result := lifecycle(method, params)) is not None:
            tab = RELAY.current()
            if "instructions" in result and tab is not None:
                # The page's own instructions say what it has loaded.
                own = RELAY.ask(tab, {**message, "params": params}, timeout=5)
                instructions = ((own or {}).get("result") or {}).get("instructions")
                if isinstance(instructions, str):
                    result["instructions"] = instructions
            reply["result"] = result
        elif method == "tools/list":
            reply["result"] = {"tools": list_tools()}
        elif method == "tools/call" and isinstance(params.get("name"), str):
            reply["result"] = call_tool(message, params)
        elif method == "tools/call":
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": "tools/call needs a string name"}}
        else:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "method not found"}}
    except Exception as e:  # noqa: BLE001 - one bad call must not end the session
        reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": f"internal error: {e}"}}
    return reply


def answer(line):
    reply = handle(line)
    if reply is not None:
        with OUT_LOCK:
            MCP_OUT.write(json.dumps(reply) + "\n")
            MCP_OUT.flush()


def listen():
    wanted = os.environ.get("PJ_RELAY_PORT")
    ports = [int(wanted)] if wanted else range(7381, 7391)
    for port in ports:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), PageHandler)
        except OSError:
            continue
        server.daemon_threads = True
        RELAY.base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log("listening for PlotJuggler pages on", RELAY.base)
        return True
    log("no free port in", list(ports), "(set PJ_RELAY_PORT)")
    return False


def main():
    global MCP_OUT
    # stdout is the MCP channel: keep it for replies only, and send fd 1 to stderr, so a child that
    # inherits it (the browser webbrowser.open starts, "Opening in existing browser session.") cannot
    # write into the protocol. UTF-8 both ways: Windows pipes default to the ANSI code page.
    # Likewise stdin: a console browser (w3m, lynx: what webbrowser picks without a display) would read the agent's input.
    MCP_OUT = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    mcp_in = os.fdopen(os.dup(sys.stdin.fileno()), encoding="utf-8")
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, sys.stdin.fileno())
    os.close(devnull)
    if not listen():
        return 1
    # A call may wait minutes (an import, the user's dialog): each one gets its own thread.
    for line in mcp_in:
        if line.strip():
            threading.Thread(target=answer, args=(line,), daemon=True).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
