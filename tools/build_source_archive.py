# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build a reviewed source-only archive beside the checkout, never inside it."""
import argparse
import hashlib
import os
from pathlib import Path
import sys
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.check_public_tree import inspect


def build(root,output):
    root=Path(root).resolve();output=Path(output).resolve()
    if output.is_relative_to(root):raise ValueError('Archive must be outside the checkout to avoid self-inclusion.')
    if output.exists():raise FileExistsError('Choose a new archive filename; existing files are preserved.')
    paths,issues=inspect(root)
    if issues:raise ValueError('Publication checks failed. Run tools/check_public_tree.py for locations.')
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    payload={p.relative_to(root).as_posix():p.read_bytes() for p in paths}
    if any(hashlib.sha256(payload[p.relative_to(root).as_posix()]).hexdigest()!=digest for p,digest in before.items()):raise ValueError('Source changed while building; rerun checks.')
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name(output.name+'.tmp')
    if temporary.exists():raise FileExistsError('A temporary archive already exists; inspect it before retrying.')
    try:
        with zipfile.ZipFile(temporary,'x',zipfile.ZIP_DEFLATED) as archive:
            for name,content in sorted(payload.items()):archive.writestr('Nova/'+name,content)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:raise ValueError('Archive integrity check failed.')
            if len(archive.namelist())!=len(payload):raise ValueError('Archive member count mismatch.')
        os.replace(temporary,output)
    except BaseException:
        if temporary.is_file():temporary.unlink()
        raise
    return {'files':len(payload),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'bytes':output.stat().st_size}


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=root.parent/'Nova-source.zip')
    args=parser.parse_args();result=build(root,args.output)
    print('Source archive:',args.output);print('Files:',result['files']);print('SHA256:',result['sha256'])


if __name__=='__main__':main()
