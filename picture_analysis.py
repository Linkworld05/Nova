# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prepare local pictures for vision without modifying or retaining originals."""
import base64
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageGrab, ImageOps, UnidentifiedImageError

MAX_FILE_BYTES=20*1024*1024
MAX_PIXELS=40_000_000
FORMATS={"JPEG","PNG","WEBP","BMP","GIF"}


@dataclass
class Picture:
    name: str
    encoded: str
    thumbnail: Image.Image


def prepare_image(source,name="Clipboard picture"):
    """Normalize a bitmap without modifying it (clipboard images have no file)."""
    if source.width*source.height>MAX_PIXELS:
        raise ValueError("Choose a picture with at most 40 million pixels.")
    image=ImageOps.exif_transpose(source).convert("RGBA")
    image.thumbnail((2048,2048),Image.Resampling.LANCZOS)
    canvas=Image.new("RGB",image.size,"white")
    canvas.paste(image,mask=image.getchannel("A"))
    buffer=io.BytesIO();canvas.save(buffer,format="JPEG",quality=90)
    thumbnail=canvas.copy();thumbnail.thumbnail((96,72),Image.Resampling.LANCZOS)
    return Picture(name,base64.b64encode(buffer.getvalue()).decode("ascii"),thumbnail)


def prepare_picture(path):
    path=Path(path)
    if path.stat().st_size>MAX_FILE_BYTES:
        raise ValueError("Choose a picture smaller than 20 MB.")
    try:
        with Image.open(path) as source:
            if source.format not in FORMATS:
                raise ValueError("Choose a JPG, PNG, WebP, BMP, or GIF picture.")
            source.seek(0)  # Animated files use their first frame.
            return prepare_image(source,path.name)
    except (UnidentifiedImageError,Image.DecompressionBombError) as e:
        raise ValueError("This file is not a supported, readable picture.") from e


def picture_from_clipboard():
    """Read images or copied image files only; text is left for normal Tk paste."""
    data=ImageGrab.grabclipboard()
    if isinstance(data,Image.Image):
        try:return prepare_image(data)
        finally:data.close()
    if isinstance(data,list):
        extensions={".jpg",".jpeg",".png",".webp",".bmp",".gif"}
        for path in data:
            if Path(path).suffix.lower() in extensions:
                return prepare_picture(path)  # One picture at a time, as with Attach.
    return None
