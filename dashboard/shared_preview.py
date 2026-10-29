"""Run two isolated DATA previews against one local shared service."""
import argparse
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import threading

from community_server import create_server
from marketplace import Marketplace, ROOT
from marketplace_preview import make_handler


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7803)
    parser.add_argument("--second-port", type=int, default=7804)
    parser.add_argument("--service-port", type=int, default=7812)
    parser.add_argument("--data-dir", type=Path, default=ROOT.parent / "users" / "shared-preview")
    args = parser.parse_args()
    ports = (args.port, args.second_port, args.service_port)
    if len(set(ports)) != 3 or any(not 1 <= port <= 65535 for port in ports):
        parser.error("Choose three different ports between 1 and 65535.")
    os.environ["DATA_SERVICES_URL"] = f"http://127.0.0.1:{args.service_port}"
    os.environ.pop("DATA_COMMUNITY_URL", None)
    with ExitStack() as cleanup:
        hub = Marketplace(args.data_dir / "service", seed=True)
        servers = [create_server(("127.0.0.1", args.service_port), hub)]
        cleanup.callback(servers[0].server_close)
        for name, port in (("device-a", args.port), ("device-b", args.second_port)):
            store = Marketplace(args.data_dir / name, seed=False)
            server = PreviewServer(("127.0.0.1", port), make_handler(store))
            servers.append(server)
            cleanup.callback(server.server_close)
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            thread.start()
            cleanup.callback(thread.join, 2)
            cleanup.callback(server.shutdown)
        print(f"Device A: http://localhost:{args.port}/#marketplace", flush=True)
        print(f"Device B: http://localhost:{args.second_port}/#marketplace", flush=True)
        print("Both previews use one local service. No AI providers, schedules or payments run.", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
