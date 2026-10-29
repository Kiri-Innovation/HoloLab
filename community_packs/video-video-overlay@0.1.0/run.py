"""Reuse the image pack's compositing/encoding code; install both sibling packs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "video-image-overlay@0.1.0"))

from _media import main
from _overlay import run as overlay


def run(i, p, out):
    overlay(i, p, out, video_layer=True)


if __name__ == "__main__":
    main(run)
