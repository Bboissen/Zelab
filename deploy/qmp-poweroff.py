#!/usr/bin/env python3
"""Request ordinary ACPI shutdown through QEMU's native control socket."""
import json
import socket
import sys

with socket.socket(socket.AF_UNIX) as connection:
    connection.settimeout(110)
    connection.connect(sys.argv[1])
    stream = connection.makefile("rwb")
    stream.readline()
    for command in ["qmp_capabilities", "system_powerdown"]:
        stream.write((json.dumps({"execute": command}) + "\n").encode())
        stream.flush()
        while True:
            response = json.loads(stream.readline())
            if "error" in response:
                raise SystemExit("QEMU refused graceful shutdown")
            if "return" in response:
                break
    # Keep ExecStop alive until QEMU exits after the guest flushes its filesystems.
    while stream.readline():
        pass
