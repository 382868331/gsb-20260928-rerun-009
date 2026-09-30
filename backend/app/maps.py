from __future__ import annotations

import io

import numpy as np
from PIL import Image


def image_to_grid(data: bytes, threshold: int = 128) -> tuple[list[list[bool]], dict[str, str | int]]:
    image = Image.open(io.BytesIO(data)).convert("L")
    values = np.array(image)
    grid = (values <= threshold).tolist()
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    return grid, {
        "width": image.width,
        "height": image.height,
        "content_type": "image/png",
        "filename": "original.png",
        "data": encoded.getvalue(),
    }


def make_sample_map() -> tuple[bytes, list[list[bool]]]:
    width, height = 36, 24
    pixels = np.full((height, width, 3), 245, dtype=np.uint8)
    pixels[0, :] = 20
    pixels[-1, :] = 20
    pixels[:, 0] = 20
    pixels[:, -1] = 20

    wall_row = 12
    pixels[wall_row, 1:12] = 20
    pixels[wall_row, 16:25] = 20
    pixels[wall_row, 31:35] = 20

    # Edge-hugging blocks make a wide gap narrower in body/edge clearance,
    # while the center cells remain open. They expose point-only verifiers.
    pixels[9:12, 12:14] = 20
    pixels[12:15, 25:27] = 20
    pixels[12:15, 29:31] = 20
    scale = 20
    image = Image.fromarray(pixels, mode="RGB").resize(
        (width * scale, height * scale), Image.Resampling.NEAREST
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    grid = (pixels.min(axis=2) <= 128).tolist()
    return out.getvalue(), grid


def make_turn_sample_map(open_corner: bool = False) -> tuple[bytes, list[list[bool]]]:
    """L-shaped corridor: open straight sections, with a tight or roomy corner."""
    width = height = 36
    pixels = np.full((height, width, 3), 20, dtype=np.uint8)

    if open_corner:
        pixels[0:12, 0:24] = 245
        pixels[0:height - 2, 12:24] = 245
    else:
        pixels[5:13, 0:23] = 245
        pixels[5:height - 2, 15:23] = 245
        pixels[5:13, 23:height] = 20
        pixels[13:height - 2, 0:15] = 20
        # Keep both straight arms a full 0.8 m wide away from the corner,
        # while locally shaving the corner pocket needed by the swept body.
        pixels[12:16, 22:23] = 20
        pixels[12:13, 14:15] = 20

    scale = 20
    image = Image.fromarray(pixels, mode="RGB").resize(
        (width * scale, height * scale), Image.Resampling.NEAREST
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    grid = (pixels.min(axis=2) <= 128).tolist()
    return out.getvalue(), grid
