"""Minimal NETCONF hello stub — advertises capabilities then closes.

Satisfies NEBULA liveness checks that expect port 830 to respond
with a NETCONF hello. No actual NETCONF operations are supported.
"""

import logging
import socket
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("netconf-stub")

HELLO = """<?xml version="1.0" encoding="UTF-8"?>
<hello xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <capabilities>
    <capability>urn:ietf:params:netconf:base:1.0</capability>
  </capabilities>
  <session-id>1</session-id>
</hello>
]]>]]>"""

PORT = 830


def _handle_client(conn: socket.socket, addr: tuple) -> None:
    try:
        conn.sendall(HELLO.encode("utf-8"))
    except Exception as exc:
        log.debug("Client %s error: %s", addr, exc)
    finally:
        conn.close()


def main() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PORT))
    server.listen(5)
    log.info("NETCONF stub listening on port %d", PORT)

    while True:
        conn, addr = server.accept()
        t = threading.Thread(target=_handle_client, args=(conn, addr), daemon=True)
        t.start()


if __name__ == "__main__":
    main()
