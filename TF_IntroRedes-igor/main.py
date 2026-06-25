"""
main.py - Entry point for the ring network node.

Usage:
    python main.py [config_file]

Defaults to config.ini in the current directory.
"""

import sys
import logging
import argparse

from config import Config
from node   import Node


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Token-Ring Network Node")
    parser.add_argument("config", nargs="?", default="config.ini",
                        help="Path to configuration file (default: config.ini)")
    parser.add_argument("--log", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging level")
    args = parser.parse_args()

    setup_logging(args.log)

    try:
        cfg = Config(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f">>> Config lido de: {cfg.path}")
    print(f"Loaded config: {cfg}")

    node = Node(cfg)
    try:
        node.start()          # blocks until user types 'quit' or Ctrl-C
    except KeyboardInterrupt:
        print("\nInterrupted — shutting down.")
        node.stop()


if __name__ == "__main__":
    main()
