# SPDX-License-Identifier: AGPL-3.0-or-later
"""Supplementary publication checks. Reports locations/categories, never values."""
import argparse
import ast
import os
from pathlib import Path
import re
import subprocess
import sys

SKIP={'.git','.venv','venv','__pycache__','.pytest_cache','.ruff_cache','.test-tmp','.test-data'}
PRIVATE_DIRS={'models','manuals','recordings','voice-questions','wake-listen','backups','codex-work','.nova-backups','.nova-qa'}
PRIVATE_NAMES={'memory.sqlite3','manuals.sqlite3','nova-settings.json','voice-settings.json','capture-settings.json','nova-runtime.json','pending-voice.json','pc-actions.json','NOVA.md','.assistant.lock'}
FORBIDDEN_SUFFIXES={'.db','.log','.wav','.mp3','.mp4','.onnx','.gguf','.safetensors','.bin','.pt','.pth','.pickle','.pkl','.exe','.dll','.zip'}
PATTERNS={
    'private-key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github-token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})'),
    'aws-access-key':re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'api-token':re.compile(r'\bsk-[A-Za-z0-9_-]{32,}\b'),
    'credential-in-url':re.compile(r'\b[a-z]+://[^\s/:]+:[^\s@/]+@',re.I),
    'personal-windows-path':re.compile(r'[A-Z]:[\\/]+Users[\\/]+(?!Public[\\/]|Default[\\/]|USER[\\/])[A-Za-z0-9_. -]+[\\/]',re.I),
}
LITERAL=re.compile(r'''(?i)["']?(?:password|passwd|api_key|access_token|secret_key|vps_pass)["']?\s*[:=]\s*["']([^"'\n]{6,})["']''')


def candidates(root,all_files=False):
    if (root/'.git').exists() and not all_files:
        proc=subprocess.run(['git','ls-files','-z'],cwd=root,capture_output=True,check=True)
        return [root/name for name in proc.stdout.decode('utf-8').split('\0') if name]
    found=[]
    for base,dirs,files in os.walk(root,followlinks=False):
        found.extend(Path(base)/d for d in dirs if (Path(base)/d).is_symlink())
        dirs[:]=[d for d in dirs if d not in SKIP and not (Path(base)/d).is_symlink()]
        found.extend(Path(base)/name for name in files)
    return found


def inspect(root,all_files=False):
    root=Path(root).resolve();issues=[];paths=candidates(root,all_files)
    for path in paths:
        name=path.relative_to(root).as_posix();parts=Path(name).parts
        def fail(kind,line=None):issues.append({'path':name,'kind':kind,**({'line':line} if line else {})})
        if path.is_symlink():fail('symlink');continue
        if not path.resolve().is_relative_to(root):fail('outside-root');continue
        if set(parts)&PRIVATE_DIRS or path.name in PRIVATE_NAMES or '.sqlite' in path.name or path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith('.env') and path.name!='.env.example':
            fail('private-runtime-or-binary');continue
        if path.stat().st_size>20*1024*1024:fail('oversized-file');continue
        if path.suffix.lower() in ('.png','.ico'):continue
        try:text=path.read_text(encoding='utf-8-sig')
        except UnicodeError:fail('unexpected-binary');continue
        if path.suffix=='.py':
            try:ast.parse(text,filename=name)
            except SyntaxError:fail('python-syntax')
        for lineno,line in enumerate(text.splitlines(),1):
            for kind,pattern in PATTERNS.items():
                if pattern.search(line):
                    # Explicit synthetic URL in the redaction regression only.
                    if kind=='credential-in-url' and path.name=='test_agent_upgrade.py' and 'localhost/db' in line:continue
                    if kind=='credential-in-url' and path.name=='verify_website_manuals.py' and 'example.org' in line:continue
                    fail(kind,lineno)
            for match in LITERAL.finditer(line):
                # The known redaction fixture tests a literal dummy value.
                if path.name=='test_agent_upgrade.py' and match.group(1)=='abc123':continue
                if match.group(1) not in ('[redacted]','[REDACTED]'):fail('literal-credential',lineno)
    return paths,issues


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--all-files',action='store_true')
    args=parser.parse_args();paths,issues=inspect(args.root,args.all_files)
    for issue in issues:print(f"{issue['path']}:{issue.get('line','-')}: {issue['kind']}")
    print(f'Publication checks: {len(paths)} files; {len(issues)} issue(s). Values are never printed.')
    return bool(issues)


if __name__=='__main__':raise SystemExit(main())
