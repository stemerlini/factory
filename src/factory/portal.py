"""Folder choosing through xdg-desktop-portal, as Omarchy apps do.

The portal shows the desktop's own chooser (GTK on Omarchy) and answers on a request
object with a ``Response`` signal, so the call blocks: run it off the UI thread.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import unquote, urlparse

PORTAL = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"


class PortalError(Exception):
    """The chooser could not be shown (no session bus, no portal)."""


def choose_folder(title: str, start: Path | None = None) -> Path | None:
    """Ask the user for a folder. Returns None when they cancel."""
    try:
        from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
        from jeepney.io.blocking import Proxy, open_dbus_connection
    except ImportError as e:
        raise PortalError(f"jeepney is missing: {e}") from e

    try:
        conn = open_dbus_connection(bus="SESSION")
    except Exception as e:  # no bus address, socket refused…
        raise PortalError(f"no D-Bus session: {e}") from e
    with conn:
        token = "factory" + secrets.token_hex(4)
        sender = conn.unique_name.lstrip(":").replace(".", "_")
        handle = f"{PATH}/request/{sender}/{token}"
        # Subscribe before calling, or a fast answer can arrive before we listen.
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request",
                         member="Response", path=handle)
        Proxy(message_bus, conn).AddMatch(rule)
        options = {"handle_token": ("s", token), "directory": ("b", True),
                   "modal": ("b", True)}
        if start and start.is_dir():
            options["current_folder"] = ("ay", str(start).encode() + b"\0")
        chooser = DBusAddress(PATH, bus_name=PORTAL,
                              interface="org.freedesktop.portal.FileChooser")
        with conn.filter(rule) as queue:
            reply = conn.send_and_get_reply(
                new_method_call(chooser, "OpenFile", "ssa{sv}", ("", title, options)))
            if reply.header.message_type.name == "error":
                raise PortalError(f"the file-chooser portal refused: {reply.body}")
            code, results = conn.recv_until_filtered(queue).body
    if code != 0:
        return None
    uris = results.get("uris", ("as", []))[1]
    return Path(unquote(urlparse(uris[0]).path)) if uris else None
