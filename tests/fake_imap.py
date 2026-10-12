#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal in-process fake IMAP server for deterministic client tests.

Speaks enough IMAP4rev1 for imaplib: CAPABILITY/LOGIN/LIST/SELECT/EXAMINE/
UID SEARCH/FETCH (BODY.PEEK[] and UID+INTERNALDATE)/LOGOUT. Behaviours can
simulate the real-world failure modes seen in the yunyou experiment:

  * folders whose names are modified-UTF-7 (Chinese "已发送")
  * SEARCH silently dropping results (search_drop_first)
  * connection drop mid-FETCH (drop_on_fetch_uid)
"""
from __future__ import annotations

import socket
import threading


class FakeIMAPServer:
    def __init__(self, folders: dict[str, list[dict]], *, search_drop_first: int = 0,
                 drop_on_fetch_uid: int | None = None):
        """folders: {raw_server_name: [{"uid", "internaldate", "raw"}, ...]}
        (sorted by uid; seq = index+1)."""
        self.folders = folders
        self.search_drop_first = search_drop_first
        self.drop_on_fetch_uid = drop_on_fetch_uid
        self.selected: list[str] = []
        self.commands: list[str] = []
        self._current_folder: str | None = None
        self._sock: socket.socket | None = None
        self.port = 0
        self._thread = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> "FakeIMAPServer":
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        self.port = srv.getsockname()[1]
        srv.listen(1)
        self._thread = threading.Thread(target=self._serve, args=(srv,), daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    # -- protocol ----------------------------------------------------------
    def _serve(self, srv: socket.socket) -> None:
        conn, _ = srv.accept()
        self._sock = conn
        f = conn.makefile("rb")
        conn.sendall(b"* OK Fake IMAP4rev1 ready\r\n")
        while True:
            line = f.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if not text:
                continue
            tag, _, rest = text.partition(" ")
            self.commands.append(rest)
            upper = rest.upper()
            try:
                if upper.startswith("CAPABILITY"):
                    conn.sendall(b"* CAPABILITY IMAP4rev1\r\n"
                                 + tag.encode() + b" OK CAPABILITY completed\r\n")
                elif upper.startswith("LOGIN"):
                    conn.sendall(tag.encode() + b" OK LOGIN completed\r\n")
                elif upper.startswith("LIST"):
                    for raw in self.folders:
                        q = raw if raw.startswith('"') else f'"{raw}"'
                        conn.sendall(f'* LIST (\\HasNoChildren) "/" {q}\r\n'.encode("utf-8", "replace"))
                    conn.sendall(tag.encode() + b" OK LIST completed\r\n")
                elif upper.startswith("SELECT") or upper.startswith("EXAMINE"):
                    arg = rest.split(" ", 1)[1].strip().strip('"')
                    self.selected.append(arg)
                    self._current_folder = arg
                    if arg not in self.folders:
                        conn.sendall(tag.encode() + f" NO no such folder: {arg}\r\n".encode("utf-8", "replace"))
                        continue
                    count = len(self.folders[arg])
                    conn.sendall((f"* {count} EXISTS\r\n* 0 RECENT\r\n"
                                  "* OK [READ-ONLY] folder is read-only\r\n").encode("utf-8", "replace")
                                 + tag.encode() + b" OK [READ-ONLY] SELECT completed\r\n")
                elif upper.startswith("UID SEARCH"):
                    uids = [str(m["uid"]) for m in self.folders[self._current_folder]]
                    if self.search_drop_first:
                        uids = uids[self.search_drop_first:]
                    conn.sendall((" ".join(["* SEARCH"] + uids) + "\r\n").encode()
                                 + tag.encode() + b" OK SEARCH completed\r\n")
                elif upper.startswith("UID FETCH"):
                    uid = rest.split(" ", 3)[2]
                    mail = self._by_uid(uid)
                    if mail is None:
                        conn.sendall(tag.encode() + b" OK no such mail\r\n")
                        continue
                    if self.drop_on_fetch_uid is not None and int(uid) == self.drop_on_fetch_uid:
                        conn.close()  # simulate server dying mid-transfer
                        return
                    raw = mail["raw"]
                    conn.sendall(
                        f"* {self._seq_of(uid)} FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode()
                        + raw + b")\r\n" + tag.encode() + b" OK FETCH completed\r\n")
                elif upper.startswith("FETCH"):
                    seq = int(rest.split(" ", 2)[1])
                    mail = self.folders[self._current_folder][seq - 1]
                    conn.sendall(
                        f"* {seq} FETCH (UID {mail['uid']} INTERNALDATE \"{mail['internaldate']}\")\r\n".encode()
                        + tag.encode() + b" OK FETCH completed\r\n")
                elif upper.startswith("LOGOUT"):
                    conn.sendall(b"* BYE Fake server logging out\r\n"
                                 + tag.encode() + b" OK LOGOUT completed\r\n")
                    break
                else:
                    conn.sendall(tag.encode() + b" OK noop\r\n")
            except (OSError, ConnectionError):
                break
        try:
            conn.close()
        except OSError:
            pass

    # -- helpers -----------------------------------------------------------
    def _by_uid(self, uid: str):
        for m in self.folders.get(self._current_folder, []):
            if str(m["uid"]) == uid:
                return m
        return None

    def _seq_of(self, uid: str) -> int:
        for i, m in enumerate(self.folders.get(self._current_folder, []), 1):
            if str(m["uid"]) == uid:
                return i
        return 0


def start_fake_imap(folders: dict[str, list[dict]], **behaviors) -> FakeIMAPServer:
    """Start and return the server (server.port ready)."""
    return FakeIMAPServer(folders, **behaviors).start()
