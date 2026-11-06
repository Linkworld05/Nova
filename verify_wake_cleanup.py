# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wake audio folder lifetime checks; no microphone, models, or user database."""
import queue
import tempfile
import threading
import unittest
import wave
from pathlib import Path

from voice_ui import WakeRecorder


class WakeCleanupTests(unittest.TestCase):
    def test_processed_chunks_do_not_remove_active_writer_folder(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            chunks=queue.Queue();errors=[]
            folder=Path(tmp)/"wake-session"
            folder.mkdir()
            rec=WakeRecorder(folder,None,lambda p,s,e:chunks.put(p),errors.append,chunk_seconds=1)
            rec.rate=8000
            rec.writer=threading.Thread(target=rec._write,daemon=True)
            rec.writer.start()
            try:
                for index in range(1,4):
                    rec.blocks.put((b"\0\0"*8000,float(index)))
                    path=chunks.get(timeout=3)
                    with wave.open(str(path),"rb") as wav:
                        self.assertEqual(wav.getnframes(),8000)
                    rec.discard_chunk(path)
                    self.assertTrue(folder.is_dir())
                    self.assertFalse(path.exists())
                    self.assertFalse(path.with_suffix(".json").exists())
            finally:
                rec.stop()
            self.assertEqual(errors,[])
            self.assertFalse(folder.exists())

    def test_final_chunk_cleanup_after_stop(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            chunks=queue.Queue();errors=[]
            folder=Path(tmp)/"wake-session"
            folder.mkdir()
            rec=WakeRecorder(folder,None,lambda p,s,e:chunks.put(p),errors.append)
            rec.rate=8000
            rec.writer=threading.Thread(target=rec._write,daemon=True)
            rec.writer.start()
            rec.blocks.put((b"\0\0"*4000,.5))
            rec.stop()
            path=chunks.get(timeout=3)
            self.assertTrue(path.exists())
            self.assertTrue(folder.exists())
            rec.discard_chunk(path)
            rec.discard_chunk(path)  # Repeated cleanup is harmless.
            self.assertFalse(folder.exists())
            self.assertEqual(errors,[])


if __name__=="__main__":
    unittest.main()
