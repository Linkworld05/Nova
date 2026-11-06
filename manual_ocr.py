# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline scanned-page OCR using Windows' installed recognition languages."""
import asyncio,base64
from concurrent.futures import ThreadPoolExecutor

async def _recognize(data):
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.graphics.imaging import BitmapDecoder,BitmapPixelFormat,BitmapAlphaMode
    from winrt.windows.storage.streams import InMemoryRandomAccessStream,DataWriter
    engine=OcrEngine.try_create_from_user_profile_languages()
    if engine is None:raise RuntimeError('No Windows OCR language is installed. Add a recognition language in Windows or import a searchable PDF.')
    stream=InMemoryRandomAccessStream();writer=DataWriter(stream);bitmap=None
    try:
        writer.write_bytes(data);await writer.store_async();writer.detach_stream();stream.seek(0)
        decoder=await BitmapDecoder.create_async(stream)
        bitmap=await decoder.get_software_bitmap_async(BitmapPixelFormat.BGRA8,BitmapAlphaMode.PREMULTIPLIED)
        result=await engine.recognize_async(bitmap)
        return '\n'.join(line.text for line in result.lines)
    finally:
        if bitmap is not None:bitmap.close()
        writer.close();stream.close()

def _worker(encoded_image):
    import winrt.runtime
    winrt.runtime.init_apartment(winrt.runtime.MTA)
    try:return asyncio.run(_recognize(base64.b64decode(encoded_image)))
    finally:winrt.runtime.uninit_apartment()

def transcribe(encoded_image):
    # Word/PDF/Tk callers may already own an STA apartment. Give WinRT OCR its
    # own MTA thread rather than altering the caller's COM configuration.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_worker,encoded_image).result()
