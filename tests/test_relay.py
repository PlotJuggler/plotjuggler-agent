# SPDX-License-Identifier: MPL-2.0
"""The browser relay end to end: an agent on its stdin, a fake PlotJuggler page on its HTTP side.

Run: python3 -m unittest discover -s tests
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

RELAY = Path(__file__).resolve().parent.parent / "relay" / "pj_mcp_relay.py"
APP = "https://app.plotjuggler.io"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.cache = tempfile.TemporaryDirectory()
        env = {**os.environ, "PJ_RELAY_PORT": str(self.port), "XDG_CACHE_HOME": self.cache.name}
        self.relay = subprocess.Popen(
            [sys.executable, str(RELAY)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True,
        )
        self.relay.stderr.readline()  # "listening ..."
        self.next_id = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "test-client"}})

    def tearDown(self):
        self.relay.stdin.close()
        self.relay.wait(5)
        self.relay.stdout.close()
        self.relay.stderr.close()
        self.cache.cleanup()

    # --- the agent ---

    def rpc(self, method, params=None):
        self.next_id += 1
        self.relay.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}}) + "\n")
        self.relay.stdin.flush()
        reply = json.loads(self.relay.stdout.readline())
        self.assertEqual(reply["id"], self.next_id)
        return reply

    def call(self, name, args=None):
        return self.rpc("tools/call", {"name": name, "arguments": args or {}})["result"]

    def call_async(self, name, args=None):
        box = {}
        thread = threading.Thread(target=lambda: box.update(result=self.call(name, args)))
        thread.start()
        return thread, box

    # --- the page ---

    def http(self, method, path, body=None, origin=APP, link=True):
        request = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode())
        if origin:
            request.add_header("Origin", origin)
        if link:
            request.add_header("X-PJ-Link", "1")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
                return response.status, (json.loads(data) if data and response.headers.get("Content-Type") == "application/json" else data), response.headers
        except urllib.error.HTTPError as e:
            e.close()
            return e.code, None, e.headers

    def hello(self):
        status, body, _ = self.http("POST", "/link/hello", {"title": "PlotJuggler", "protocol": 1})
        self.assertEqual(status, 200)
        return body["id"]

    def serve_once(self, tab, answer):
        """Polls until a message arrives, replies with answer(message) as its result, returns the message."""
        while True:
            status, item, _ = self.http("GET", f"/link/next?id={tab}")
            if status == 200:
                break
            self.assertEqual(status, 204)
        response = {"jsonrpc": "2.0", "id": item["message"]["id"], "result": answer(item["message"])}
        self.assertEqual(self.http("POST", f"/link/reply?id={tab}", {"seq": item["seq"], "response": response})[0], 204)
        return item

    # --- tests ---

    def test_without_a_page_the_full_tool_list_is_served_and_calls_ask_for_launch(self):
        names = {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}
        self.assertTrue({"get_status", "open_file", "read_series", "launch", "list_instances"} <= names)
        self.assertNotIn("export_series", names)
        result = self.call("get_status")
        self.assertTrue(result["isError"])
        self.assertIn("launch", result["content"][0]["text"])

    def test_a_call_reaches_the_only_page_and_its_answer_comes_back(self):
        tab = self.hello()
        thread, box = self.call_async("get_status")
        item = self.serve_once(tab, lambda m: {"content": [{"type": "text", "text": "status!"}]})
        thread.join(10)
        self.assertEqual(item["client"], "test-client")
        self.assertEqual(item["message"]["params"]["name"], "get_status")
        self.assertEqual(box["result"]["content"][0]["text"], "status!")
        listed = json.loads(self.call("list_instances")["content"][0]["text"])
        self.assertEqual(listed, [{"id": tab, "title": "PlotJuggler", "selected": True}])

    def test_open_file_hands_the_page_a_url_it_can_download(self):
        tab = self.hello()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"time,a\n0,1\n")
        try:
            thread, box = self.call_async("open_file", {"path": f.name})
            item = self.serve_once(tab, lambda m: {"content": [{"type": "text", "text": "{}"}]})
            thread.join(10)
            args = item["message"]["params"]["arguments"]
            self.assertEqual(args["path"], f.name)
            self.assertEqual(args["name"], os.path.basename(f.name))
            self.assertTrue(args["url"].startswith(self.base + "/file/"))
            status, data, headers = self.http("GET", args["url"][len(self.base):], link=False)
            self.assertEqual((status, data), (200, b"time,a\n0,1\n"))
            self.assertEqual(headers["Access-Control-Allow-Origin"], APP)
            self.assertEqual(self.http("GET", "/file/guessed/x.csv", link=False)[0], 404)
        finally:
            os.unlink(f.name)

    def test_only_allowed_origins_with_the_link_header_get_in(self):
        self.assertEqual(self.http("POST", "/link/hello", {}, origin="https://evil.example")[0], 403)
        self.assertEqual(self.http("POST", "/link/hello", {}, link=False)[0], 403)
        self.assertEqual(self.http("OPTIONS", "/link/hello", origin="https://evil.example")[0], 403)
        status, _, headers = self.http("OPTIONS", "/link/hello")
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        self.assertIn("X-PJ-Link", headers["Access-Control-Allow-Headers"])
        self.assertEqual(self.http("POST", "/link/hello", {}, origin="http://localhost:8080")[0], 200)

    def test_a_page_the_relay_does_not_know_is_told_to_say_hello(self):
        self.assertEqual(self.http("GET", "/link/next?id=nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
