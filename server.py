#!/usr/bin/env python3

import argparse
import html
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = "127.0.0.1"
PORT = 8080

ROOT_DIR = None

TOTAL_DOWNLOADED = 0
TOTAL_LOCK = threading.Lock()

SHUTDOWN_REQUESTED = False
SHUTDOWN_LOCK = threading.Lock()


HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>File Browser</title>

<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #0b0f19;
    color: #fff;
    font-family: Arial, sans-serif;
}

.container {
    width: min(1000px, calc(100% - 30px));
    margin: 40px auto;
}

.header {
    background: #151a26;
    border: 1px solid #293044;
    border-radius: 16px;
    padding: 22px;
    margin-bottom: 15px;
}

h1 {
    margin: 0 0 8px;
}

.path {
    color: #8792a8;
    word-break: break-all;
}

.toolbar {
    display: flex;
    gap: 10px;
    margin-top: 15px;
}

button {
    border: 0;
    border-radius: 9px;
    padding: 11px 16px;
    cursor: pointer;
    font-weight: bold;
}

.home {
    background: #5865f2;
    color: white;
}

.close {
    background: #3a2530;
    color: #ff7b7b;
}

.files {
    background: #151a26;
    border: 1px solid #293044;
    border-radius: 16px;
    overflow: hidden;
}

.item {
    display: flex;
    align-items: center;
    padding: 15px 18px;
    border-bottom: 1px solid #252b3a;
    text-decoration: none;
    color: white;
}

.item:last-child {
    border-bottom: 0;
}

.item:hover {
    background: #1d2332;
}

.icon {
    width: 38px;
    font-size: 22px;
}

.name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
}

.size {
    color: #7f8ba3;
    font-size: 13px;
    margin-left: 15px;
}

.empty {
    padding: 30px;
    text-align: center;
    color: #7f8ba3;
}

.status {
    margin-top: 15px;
    color: #7f8ba3;
    font-size: 13px;
}
</style>
</head>

<body>

<div class="container">

<div class="header">

<h1>📁 File Browser</h1>

<div class="path">
{{PATH}}
</div>

<div class="toolbar">

<a href="/">
<button class="home">HOME</button>
</a>

<button class="close" onclick="closeWorkflow()">
CLOSE WORKFLOW
</button>

</div>

</div>

<div class="files">

{{FILES}}

</div>

<div class="status">
{{COUNT}} items
</div>

</div>

<script>

async function closeWorkflow() {

    if (!confirm(
        "Close GitHub workflow?\\n\\n" +
        "The file server will stop."
    )) {
        return;
    }

    document.querySelector(".close").disabled = true;

    try {
        await fetch("/close", {
            method: "POST"
        });
    } catch (e) {
        console.log(e);
    }

    document.querySelector(".status").textContent =
        "Workflow is shutting down...";
}

</script>

</body>
</html>
"""


def human_size(size):
    units = ["B", "KB", "MB", "GB", "TB"]

    size = float(size)

    for unit in units:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024

    return f"{size:.2f} PB"


def safe_path(url_path):
    """
    Convert URL path -> real filesystem path.

    Prevent:
        ../../etc/passwd
        absolute paths
        path traversal
    """

    decoded = urllib.parse.unquote(url_path)

    if decoded.startswith("/"):
        decoded = decoded[1:]

    candidate = os.path.realpath(
        os.path.join(ROOT_DIR, decoded)
    )

    root = os.path.realpath(ROOT_DIR)

    if candidate != root and not candidate.startswith(root + os.sep):
        raise PermissionError("Path outside root")

    return candidate


def relative_url(path):
    rel = os.path.relpath(path, ROOT_DIR)

    if rel == ".":
        return "/"

    parts = rel.split(os.sep)

    return "/" + "/".join(
        urllib.parse.quote(x)
        for x in parts
    )


def render_directory(directory, url_path):

    items = []

    try:
        entries = list(os.scandir(directory))
    except PermissionError:
        return "<div class='empty'>Permission denied</div>"

    # Folder trước
    entries.sort(
        key=lambda x: (
            not x.is_dir(),
            x.name.lower()
        )
    )

    # Parent
    if directory != ROOT_DIR:

        parent = os.path.dirname(directory)

        items.append(
            f"""
            <a class="item"
               href="{relative_url(parent)}">

                <div class="icon">⬆️</div>

                <div class="name">
                    ..
                </div>

            </a>
            """
        )

    for entry in entries:

        # Ẩn file/folder bắt đầu bằng .
        if entry.name.startswith("."):
            continue

        href = relative_url(entry.path)

        safe_name = html.escape(entry.name)

        if entry.is_dir():

            items.append(
                f"""
                <a class="item" href="{href}">
                    <div class="icon">📁</div>
                    <div class="name">{safe_name}</div>
                    <div class="size">Folder</div>
                </a>
                """
            )

        elif entry.is_file():

            try:
                size = entry.stat().st_size
            except OSError:
                size = 0

            items.append(
                f"""
                <a class="item" href="{href}">
                    <div class="icon">📄</div>
                    <div class="name">{safe_name}</div>
                    <div class="size">
                        {human_size(size)}
                    </div>
                </a>
                """
            )

    if not items:
        return "<div class='empty'>Empty folder</div>"

    return "\n".join(items)


class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send_bytes(
        self,
        status,
        content_type,
        body
    ):

        self.send_response(status)

        self.send_header(
            "Content-Type",
            content_type
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):

        try:
            path = safe_path(self.path.split("?", 1)[0])
        except PermissionError:

            self.send_bytes(
                403,
                "text/plain",
                b"Forbidden"
            )

            return

        if os.path.isdir(path):

            self.show_directory(path)
            return

        if os.path.isfile(path):

            self.download_file(path)
            return

        self.send_bytes(
            404,
            "text/plain",
            b"Not found"
        )

    def show_directory(self, directory):

        url_path = relative_url(directory)

        files = render_directory(
            directory,
            url_path
        )

        title = (
            "/"
            if directory == ROOT_DIR
            else os.path.relpath(
                directory,
                ROOT_DIR
            )
        )

        title = html.escape(title)

        body = HTML_TEMPLATE

        body = body.replace(
            "{{PATH}}",
            title
        )

        body = body.replace(
            "{{FILES}}",
            files
        )

        body = body.replace(
            "{{COUNT}}",
            str(len(os.listdir(directory)))
        )

        self.send_bytes(
            200,
            "text/html; charset=utf-8",
            body.encode("utf-8")
        )

    def download_file(self, file_path):

        global TOTAL_DOWNLOADED

        try:
            file_size = os.path.getsize(file_path)
        except OSError:

            self.send_bytes(
                404,
                "text/plain",
                b"File not found"
            )

            return

        filename = os.path.basename(file_path)

        encoded_name = urllib.parse.quote(
            filename
        )

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/octet-stream"
        )

        self.send_header(
            "Content-Length",
            str(file_size)
        )

        self.send_header(
            "Content-Disposition",
            "attachment; filename*=UTF-8''"
            + encoded_name
        )

        self.send_header(
            "Accept-Ranges",
            "bytes"
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.end_headers()

        try:

            with open(file_path, "rb") as f:

                while True:

                    chunk = f.read(
                        16 * 1024 * 1024
                    )

                    if not chunk:
                        break

                    self.wfile.write(chunk)

                    with TOTAL_LOCK:
                        TOTAL_DOWNLOADED += len(chunk)

        except (
            BrokenPipeError,
            ConnectionResetError
        ):
            pass

    def do_POST(self):

        if self.path != "/close":

            self.send_bytes(
                404,
                "text/plain",
                b"Not found"
            )

            return

        self.send_bytes(
            200,
            "application/json",
            b'{"ok":true}'
        )

        threading.Thread(
            target=shutdown_workflow,
            daemon=True
        ).start()


def shutdown_workflow():

    global SHUTDOWN_REQUESTED

    with SHUTDOWN_LOCK:

        if SHUTDOWN_REQUESTED:
            return

        SHUTDOWN_REQUESTED = True

    print("Close requested from Web UI.")

    token = os.environ.get(
        "GITHUB_TOKEN"
    )

    repository = os.environ.get(
        "GITHUB_REPOSITORY"
    )

    run_id = os.environ.get(
        "GITHUB_RUN_ID"
    )

    if not token or not repository or not run_id:

        print(
            "Missing GitHub environment variables."
        )

        os._exit(0)

    url = (
        f"https://api.github.com/repos/"
        f"{repository}/actions/runs/"
        f"{run_id}/cancel"
    )

    request = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Accept":
                "application/vnd.github+json",

            "Authorization":
                f"Bearer {token}",

            "X-GitHub-Api-Version":
                "2026-03-10",

            "User-Agent":
                "github-download-server",
        }
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=10
        ) as response:

            print(
                "Cancel workflow:",
                response.status
            )

    except Exception as e:

        print(
            "Cancel failed:",
            e
        )

    # Đảm bảo server thoát.
    time.sleep(1)

    os._exit(0)


def main():

    global ROOT_DIR

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "folder",
        help="Folder to expose"
    )

    args = parser.parse_args()

    ROOT_DIR = os.path.realpath(
        args.folder
    )

    if not os.path.isdir(ROOT_DIR):

        print(
            f"ERROR: folder not found: "
            f"{ROOT_DIR}"
        )

        raise SystemExit(1)

    print()
    print("=" * 60)
    print("FILE SERVER")
    print("=" * 60)
    print(f"Root : {ROOT_DIR}")
    print(f"URL  : http://{HOST}:{PORT}/")
    print("=" * 60)
    print()

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler
    )

    try:
        server.serve_forever()

    finally:
        server.server_close()


if __name__ == "__main__":
    main()
      
