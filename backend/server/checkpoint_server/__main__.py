"""Run the shared server: python -m checkpoint_server [--host 0.0.0.0] [--port 8780] [--ssl-certfile ..] [--ssl-keyfile ..]"""

import argparse
import os

import uvicorn

from .app import create_app, run_migrations


def main() -> None:
    ap = argparse.ArgumentParser(description="Checkpoint shared server")
    ap.add_argument("--host", default=os.environ.get("CHECKPOINT_SERVER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CHECKPOINT_SERVER_PORT", "8780")))
    ap.add_argument("--ssl-certfile", default=os.environ.get("CHECKPOINT_SERVER_SSL_CERTFILE"))
    ap.add_argument("--ssl-keyfile", default=os.environ.get("CHECKPOINT_SERVER_SSL_KEYFILE"))
    ap.add_argument("--migrate-only", action="store_true", help="Apply database migrations and exit")
    args = ap.parse_args()
    if not os.environ.get("CHECKPOINT_SERVER_DATABASE_URL"):
        raise SystemExit("CHECKPOINT_SERVER_DATABASE_URL is not set. See docs/shared-server.md.")
    run_migrations(os.environ["CHECKPOINT_SERVER_DATABASE_URL"])
    if args.migrate_only:
        print("Migrations applied.")
        return
    if not args.ssl_certfile and args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: serving without TLS. Only do this on a trusted private network or behind an HTTPS reverse proxy.")
    uvicorn.run(create_app(), host=args.host, port=args.port, ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile,
                proxy_headers=True)


if __name__ == "__main__":
    main()
