from __future__ import annotations

import argparse

from window_frame_monitor.server import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the window frame monitor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--target-fps", default=30, type=int)
    parser.add_argument("--test-backend", action="store_true", help="Use synthetic frames instead of real capture backends.")
    args = parser.parse_args()

    server = create_server(host=args.host, port=args.port, use_test_backend=args.test_backend, target_fps=args.target_fps)
    print(f"Window Frame Monitor listening at http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
