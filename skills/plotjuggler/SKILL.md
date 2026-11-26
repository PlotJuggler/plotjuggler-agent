---
name: plotjuggler
description: Use when the user wants to look at, plot, analyze or debug time-series or robotics log data (MCAP, ROS bags, ULog, CSV, Parquet, live ROS/MQTT/ZMQ streams...) in PlotJuggler, or asks how to do something in PlotJuggler. Drives the running PlotJuggler 4 app through its `plotjuggler` MCP tools.
---

# PlotJuggler 4 through MCP

PlotJuggler runs on the user's desktop, or in their web browser when the desktop app is not installed; the
`plotjuggler` MCP tools act on that window or page. The user sees everything you create, so work in their app instead
of decoding files or plotting with your own scripts. Your training describes PlotJuggler 3; do not answer from it.

## Read the guide for the running PlotJuggler

Once PlotJuggler answers (see below), read the guide that matches it before working with the data: the workflow, the
scripting languages, which plugin reads which format, and the UI steps for each task.

- **PlotJuggler serves its own guide** (newer than 4.2.0), written for the version that is running. When your client
  lists MCP skills, it is the `plotjuggler` server's `plotjuggler` skill (in Claude Code
  `plugin_plotjuggler_plotjuggler:plotjuggler`): use it. Otherwise read the resource `skill://plotjuggler/SKILL.md`
  from the `plotjuggler` server, and the pages it links (same folder, e.g. `skill://plotjuggler/scripting.md`) when a
  task needs them.
- **It does not** (PlotJuggler 4.2.0 and older: the server lists no `skill://plotjuggler/SKILL.md` resource, or
  reading it fails): read [legacy-guide.md](legacy-guide.md), beside this file, instead. Decide by whether the
  resource is there, not by the version number: a development build can report a released version.

## Before PlotJuggler answers

0. **Browser or desktop?** When the server's instructions say PlotJuggler runs in the user's web browser, the
   desktop app was not found. If `list_instances` shows no page, ASK the user before anything else: open PlotJuggler
   in the browser now (nothing to install: call `launch`, which opens a tab on https://app.plotjuggler.io linked to
   this session), or install the desktop app from https://plotjuggler.io (then they restart this agent session to use
   it). In the browser:
   - Chrome, Edge or Firefox. The browser may ask to let the page reach this computer (local network access): tell
     the user to allow it, the page talks only to this session's relay on 127.0.0.1.
   - The guide is served by the page: look for it once `launch` has returned.
1. `list_instances`. None running: `launch` (it starts PlotJuggler and waits). Several: ask the user which one, then
   `select_instance`. A window the user started is listed unless they turned agent access off (Preferences →
   Appearance); then ask them to turn it on, or `launch` a separate one.

If the tools are missing or fail to start, neither the desktop app nor `python3` (which runs the browser relay) was
found: tell the user to install PlotJuggler from https://plotjuggler.io and start it once, to set `PLOTJUGGLER4` to
the executable, or to install python3 for the browser version. With Codex on Linux, `launch` needs the display
variables (the plugin forwards them; if `launch` says they are unset or the window fails to open, the user must start
PlotJuggler themselves).
