# PlotJuggler agent plugin

Lets Claude Code and Codex drive PlotJuggler 4 on your desktop, or in your web browser when the
desktop app is not installed: open logs, read and compute over signals, create derived series and
markers, plot in their own tabs, show 3D/2D scenes, take screenshots. One plugin carries the MCP server
declaration and a skill that teaches the agent the workflow.

## Install

Either PlotJuggler 4.2 or newer on the desktop, which has the MCP server (`plotjuggler4 --mcp-bridge`),
or nothing but `python3`: without a desktop install the plugin runs PlotJuggler in your browser (see
[Browser mode](#browser-mode)), and the agent asks which you prefer. Until 4.2
is released, use a build of the [`main-4.x`](https://github.com/PlotJuggler/PlotJuggler/tree/main-4.x)
branch. Install it and start it once: that is how the plugin finds it (or set `PLOTJUGGLER4` to the
executable). Agent access is on by default; Preferences → Appearance → "Let AI agents control
PlotJuggler (MCP server)" turns it off.

Claude Code:

```bash
claude plugin marketplace add PlotJuggler/plotjuggler-agent
claude plugin install plotjuggler@plotjuggler
```

Codex:

```bash
codex plugin marketplace add PlotJuggler/plotjuggler-agent
codex plugin add plotjuggler@plotjuggler
```

Then ask, for example: *"Open ~/logs/run3.mcap in PlotJuggler and plot the wheel speeds."*

## How it works

`bin/plotjuggler-mcp` (`.cmd` on Windows) finds PlotJuggler and runs `plotjuggler4 --mcp-bridge`,
a stdio MCP server that forwards to a running PlotJuggler window: the one the user has open, a
choice among several (`list_instances`, `select_instance`), or a new one (`launch`). It serves this
computer only, behind a per-window secret token. Lookup order: `$PLOTJUGGLER4`, `PATH`, the path
PlotJuggler records at every start (`<app data>/PlotJuggler/PlotJuggler4/mcp-instances/executable`),
the default install folders.

Codex starts MCP servers with a stripped environment; `.codex-mcp.json` forwards the display and
per-user folder variables PlotJuggler needs, and pre-approves the tools except `export_series` (writes
a file) and `unload_file` (drops the user's data).

**Windows is untested.** Both MCP configs name the extension-less `bin/plotjuggler-mcp`; Codex is
expected to resolve its `.cmd` twin (OpenAI's own plugins rely on that), Claude Code may not. If the
plugin's `plotjuggler` server fails to connect in Claude Code on Windows, register the app directly
(the skill still applies):

```
claude mcp add --scope user plotjuggler -- "C:\Program Files\PlotJuggler4\bin\PlotJuggler4.exe" --mcp-bridge
```

## Browser mode

When no desktop PlotJuggler is found (or `PJ_BROWSER=1` is set), `bin/plotjuggler-mcp` starts
`relay/pj_mcp_relay.py` instead: a stdio MCP server, standard-library Python only, that listens on
`127.0.0.1` (first free port of 7381-7390, or `PJ_RELAY_PORT`). A web page can neither listen nor be
started by an agent, so the page connects out: the `launch` tool opens
`https://app.plotjuggler.io/?mcp=http://127.0.0.1:<port>/?key=<secret>` and the page long-polls the
relay for the agent's messages. `open_file` paths are served to the page by the relay, since a page
cannot read your disk. The secret is random per relay run and only `launch` hands it out: a page or
local process without it cannot join. Browsers are also held to the hosted app's origin (`PJ_WASM_URL`,
default `https://app.plotjuggler.io/`); add others, such as a local server, with `PJ_RELAY_ORIGINS`.

Use Chrome, Edge or Firefox; the first time, the browser may ask whether the page may access devices on
your local network: allow it. `export_series` and `about` are desktop only.

`relay/browser-tools.json` is the tool list served before a page connects (MCP clients read it once, at
startup); once a page connects the relay caches the page's own list for the next sessions. Regenerate it
from a PlotJuggler build:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | HOME=$(mktemp -d) plotjuggler4 --mcp-bridge |
  python3 -c 'import json,sys; t=json.load(sys.stdin)["result"]["tools"]; drop={"list_instances","select_instance","launch","about","export_series"}; json.dump([x for x in t if x["name"] not in drop], sys.stdout, indent=1)' > relay/browser-tools.json
```

Prefer to chat inside PlotJuggler? The **Assistant** toolbox (PlotJuggler's marketplace) drives the
same server from a panel in the app, with your Claude Code or Codex subscription.

## Develop

```bash
claude --plugin-dir .      # load this checkout for one session
claude plugin validate .
python3 -m unittest discover -s tests   # the browser relay
```
