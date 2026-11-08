# PlotJuggler agent plugin

Lets Claude Code and Codex drive PlotJuggler 4 on your desktop: open logs, read and compute over
signals, create derived series and markers, plot in their own tabs, show 3D/2D scenes, take
screenshots. One plugin carries the MCP server declaration and a skill that teaches the agent the
workflow.

## Install

You need PlotJuggler 4.2 or newer, which has the MCP server (`plotjuggler4 --mcp-bridge`). Until 4.2
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

Prefer to chat inside PlotJuggler? The **Assistant** toolbox (PlotJuggler's marketplace) drives the
same server from a panel in the app, with your Claude Code or Codex subscription.

## Develop

```bash
claude --plugin-dir .      # load this checkout for one session
claude plugin validate .
```
