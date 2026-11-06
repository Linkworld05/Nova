# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit optional downloads. No download runs merely by importing this file."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PROFILES={'Fast':('qwen3:8b','qwen3:14b'),'Balanced':('qwen3:14b',),'Deep':('qwen3:14b','qwen3-coder:30b')}


def planned(profile=None,vision=False,embeddings=False):
    models=list(PROFILES.get(profile,()))
    if vision:models.append('gemma3:4b')
    if embeddings:models.append('embeddinggemma')
    return list(dict.fromkeys(models))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=PROFILES)
    parser.add_argument('--vision',action='store_true');parser.add_argument('--embeddings',action='store_true')
    parser.add_argument('--whisper',action='store_true');parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args(argv);models=planned(args.profile,args.vision,args.embeddings)
    if not models and not args.whisper:parser.error('Select a profile, --vision, --embeddings or --whisper.')
    for model in models:print('Ollama model:',model)
    if args.whisper:print('Whisper cache: models/ (public Systran/faster-whisper-small repository)')
    if args.dry_run:return 0
    if models:
        ollama=shutil.which('ollama')
        if not ollama:parser.error('Install Ollama first and start its local service.')
        for model in models:subprocess.run([ollama,'pull',model],check=True)
    if args.whisper:
        try:from huggingface_hub import snapshot_download
        except ImportError:parser.error('Install requirements-voice.txt before --whisper.')
        snapshot_download(repo_id='Systran/faster-whisper-small',cache_dir=str(ROOT/'models'))
    return 0


if __name__=='__main__':raise SystemExit(main())
