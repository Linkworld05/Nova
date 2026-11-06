# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistent local manuals, stable reference PDFs, and page-level retrieval."""
import base64,hashlib,json,math,re,shutil,sqlite3,tempfile,unicodedata,zipfile
from contextlib import contextmanager,closing
from datetime import datetime,timezone
from pathlib import Path
from xml.sax.saxutils import escape

MAX_BYTES=100*1024*1024
MAX_PAGES=2000
STOP=set('the and for with from this that what when which how does do can you your manual manuals please about tell me are should would could of in on to is it its at as by an be was were que como para del las los una unos este esta cual donde cuando favor manuales dime'.split())

def terms(text):
    plain=''.join(c for c in unicodedata.normalize('NFKD',text.lower()) if not unicodedata.combining(c))
    words=re.findall(r'[a-z0-9]+(?:[-.][a-z0-9]+)*',plain)
    words=[part for word in words for part in [word]+re.split('[-.]',word)]
    def singular(w):
        if w.endswith('ies') and len(w)>4:return w[:-3]+'y'
        if w.endswith('sses'):return w[:-2]
        return w[:-1] if w.endswith('s') and not w.endswith('ss') and len(w)>4 else w
    return {singular('gray' if w=='grey' else w) for w in words if len(w)>1 and w not in STOP}

def reference_pdf(blocks,title,target):
    """Paginate Word/text body into a saved reference copy, not original Word pages."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak
    from reportlab.lib.styles import ParagraphStyle
    font='Helvetica'
    if Path('C:/Windows/Fonts/arial.ttf').is_file():
        if 'NovaManual' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('NovaManual','C:/Windows/Fonts/arial.ttf'))
        font='NovaManual'
    style=ParagraphStyle('body',fontName=font,fontSize=10,leading=14,spaceAfter=7)
    story=[]
    for block in blocks:
        parts=block.split('\f')
        for i,part in enumerate(parts):
            if i:story.append(PageBreak())
            if part.strip():story.append(Paragraph(escape(part).replace('\n','<br/>'),style))
    def footer(c,doc):
        c.saveState();c.setFont(font,8)
        c.drawString(42,24,(title[:70]+' — reference copy'))
        c.drawRightString(A4[0]-42,24,'Page '+str(doc.page));c.restoreState()
    SimpleDocTemplate(str(target),pagesize=A4,leftMargin=42,rightMargin=42,topMargin=42,bottomMargin=42).build(story,onFirstPage=footer,onLaterPages=footer)

def create_reference(source,target,title=None):
    suffix=source.suffix.lower()
    if suffix=='.pdf':shutil.copy2(source,target);return 'PDF page positions (may differ from printed page numbers)'
    if suffix=='.docx':
        from docx import Document
        from docx.table import Table
        with zipfile.ZipFile(source) as z:
            if sum(i.file_size for i in z.infolist())>200*1024*1024:raise ValueError('The Word document expands beyond 200 MB.')
        blocks=[]
        for item in Document(source).iter_inner_content():
            if isinstance(item,Table):blocks.extend(' | '.join(cell.text for cell in row.cells) for row in item.rows)
            else:blocks.append(item.text)
    else:
        raw=source.read_bytes()
        if raw.startswith((b'\xff\xfe',b'\xfe\xff')):text=raw.decode('utf-16')
        else:
            try:text=raw.decode('utf-8-sig')
            except UnicodeDecodeError:text=raw.decode('cp1252')
        if '\0' in text:raise ValueError('This file does not contain readable text.')
        blocks=re.split(r'\n\s*\n',text)
    if not any(b.strip() for b in blocks):raise ValueError('No body text found. For an image-only Word manual, export it as PDF first.')
    reference_pdf(blocks,title or source.stem,target)
    return 'saved reference-copy pages (Word/text pagination; body text and tables, not embedded figures)'

class ManualLibrary:
    def __init__(self,root):
        from semantic_index import SemanticIndex
        self.semantic=SemanticIndex(root)
        self.root=Path(root);self.folder=self.root/'manuals';self.path=self.root/'manuals.sqlite3'
        self.folder.mkdir(parents=True,exist_ok=True)
        if self.path.exists():
            with self.connect() as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_page_sources'").fetchone():
                    backup=self.root/'backups'/'before-website-manuals';backup.mkdir(parents=True,exist_ok=True)
                    with closing(sqlite3.connect(backup/(datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.sqlite3'))) as target:db.backup(target)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS manuals(id INTEGER PRIMARY KEY,title TEXT,name TEXT,digest TEXT UNIQUE,version INTEGER,folder TEXT,pages INTEGER,imported TEXT,notes TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS pages(manual_id INTEGER,page INTEGER,text TEXT,ocr INTEGER,PRIMARY KEY(manual_id,page))')
            db.execute('CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY,manual_id INTEGER,page INTEGER,text TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS manual_chunks ON chunks(manual_id)')
            db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS web_page_sources(manual_id INTEGER,page INTEGER,url TEXT,title TEXT,kind TEXT,PRIMARY KEY(manual_id,page))')
            db.execute('CREATE TABLE IF NOT EXISTS web_images(manual_id INTEGER,page INTEGER,url TEXT,path TEXT,caption TEXT,ocr TEXT,analysis TEXT,sha TEXT,PRIMARY KEY(manual_id,page))')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def list(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM manuals ORDER BY id DESC')]
    def get(self,mid):
        with self.connect() as db:row=db.execute('SELECT * FROM manuals WHERE id=?',(int(mid),)).fetchone()
        return dict(row) if row else None
    def scope(self):
        with self.connect() as db:r=db.execute("SELECT value FROM settings WHERE key='scope'").fetchone()
        return r[0] if r else 'off'
    def set_scope(self,value):
        value=str(value)
        if value not in ('off','all') and not self.get(value):raise ValueError('Choose a saved manual.')
        with self.connect() as db:db.execute("INSERT INTO settings VALUES('scope',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(value,))
    def import_file(self,path,progress=lambda text:None,ocr=None):
        import pymupdf
        source=Path(path)
        if source.suffix.lower() not in ('.pdf','.docx','.txt','.md'):raise ValueError('Choose PDF, Word (.docx), or text (.txt/.md). Save old .doc files as .docx first.')
        if source.stat().st_size>MAX_BYTES:raise ValueError('Choose a manual smaller than 100 MB.')
        with tempfile.TemporaryDirectory(dir=self.folder,prefix='import-') as work:
            work=Path(work);snapshot=work/('source'+source.suffix.lower());digest=hashlib.sha256()
            total=0
            with source.open('rb') as src,snapshot.open('wb') as dst:
                while part:=src.read(1024*1024):
                    total+=len(part)
                    if total>MAX_BYTES:raise ValueError('Choose a manual smaller than 100 MB.')
                    digest.update(part);dst.write(part)
            sha=digest.hexdigest()
            with self.connect() as db:existing=db.execute('SELECT id FROM manuals WHERE digest=?',(sha,)).fetchone()
            if existing:
                self.set_scope(str(existing['id']));return dict(self.get(existing['id']),duplicate=True)
            progress('Preparing saved reference pages…')
            reference=work/'reference.pdf';location=create_reference(snapshot,reference,source.stem)
            pages=[];missing=[];ocr_pages=[];ocr_errors=[]
            with pymupdf.open(reference) as pdf:
                if pdf.is_encrypted:raise ValueError('This PDF is password protected. Import an unlocked copy.')
                if len(pdf)>MAX_PAGES:raise ValueError('Manuals are limited to 2,000 pages per file.')
                for i,page in enumerate(pdf):
                    progress(f'Reading manual page {i+1} of {len(pdf)}…')
                    text=page.get_text('text',sort=True).strip();used_ocr=False
                    if len(text)<20 and page.get_images() and ocr:
                        try:
                            scale=min(2,1800/max(page.rect.width,page.rect.height))
                            encoded=base64.b64encode(page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False).tobytes('png')).decode()
                            extracted=ocr(encoded).strip()
                            if extracted and 'NO_READABLE_TEXT' not in extracted:
                                text=extracted;used_ocr=True;ocr_pages.append(i+1)
                        except Exception as e:ocr_errors.append(f'page {i+1}: {e}')
                    if not text:missing.append(i+1)
                    pages.append((i+1,text,used_ocr))
            if not any(t.strip() for _,t,_ in pages):
                detail=(' OCR failed on '+ocr_errors[0]) if ocr_errors else ' Scanned pages need Windows OCR with an installed recognition language.'
                raise ValueError('No readable text was found.'+detail)
            notes=location
            if ocr_pages:notes+='; OCR pages: '+', '.join(map(str,ocr_pages[:20]))+('…' if len(ocr_pages)>20 else '')+' (check against the page image)'
            if missing:notes+=f'; no readable text on {len(missing)} pages: '+', '.join(map(str,missing[:20]))+('…' if len(missing)>20 else '')
            destination=self.folder/sha
            destination.mkdir(exist_ok=True)
            shutil.copy2(snapshot,destination/snapshot.name);shutil.copy2(reference,destination/'reference.pdf')
            with self.connect() as db:
                version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM manuals WHERE name=? COLLATE NOCASE',(source.name,)).fetchone()[0]
                now=datetime.now(timezone.utc).isoformat()
                mid=db.execute('INSERT INTO manuals(title,name,digest,version,folder,pages,imported,notes) VALUES(?,?,?,?,?,?,?,?)',
                    (source.stem,source.name,sha,version,sha,len(pages),now,notes)).lastrowid
                for page,text,used_ocr in pages:
                    db.execute('INSERT INTO pages VALUES(?,?,?,?)',(mid,page,text,int(used_ocr)))
                    for start in range(0,len(text),1100):
                        excerpt=text[start:start+1400]
                        if excerpt.strip():db.execute('INSERT INTO chunks(manual_id,page,text) VALUES(?,?,?)',(mid,page,excerpt))
                db.execute("INSERT INTO settings VALUES('scope',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(mid),))
            return dict(self.get(mid),duplicate=False)
    def import_website(self,url,include_links=True,progress=lambda text:None,ocr=None,vision=None):
        from website_manual import import_site
        return import_site(self,url,include_links,progress,ocr,vision)
    def figure_sources(self,sources,limit=2):
        result=[];seen=set()
        with self.connect() as db:
            for source in sources:
                rows=db.execute('SELECT i.*,m.folder,w.url AS page_url FROM web_images i JOIN manuals m ON m.id=i.manual_id JOIN web_page_sources w ON w.manual_id=i.manual_id AND w.page=i.page WHERE i.manual_id=? AND i.page=?',(source['manual_id'],source['page'])).fetchall()
                for row in rows:
                    key=(row['manual_id'],row['page'])
                    if key in seen:continue
                    seen.add(key);result.append(dict(row,file=self.folder/row['folder']/row['path'],label=f"[M{row['manual_id']} p.{row['page']}]"))
                    if len(result)>=limit:return result
        return result
    def retrieve(self,question,scope=None,history=None,limit=6):
        scope=self.scope() if scope is None else str(scope)
        if scope=='off':return []
        query=terms(question)
        previous=next((m.get('content','') for m in reversed(history or []) if m.get('role')=='user'),'')
        context=terms(previous)-query
        with self.connect() as db:
            sql='SELECT c.id,c.manual_id,c.page,c.text,m.title,m.version,m.name,m.notes,w.title AS web_title,w.kind AS web_kind FROM chunks c JOIN manuals m ON c.manual_id=m.id LEFT JOIN web_page_sources w ON w.manual_id=c.manual_id AND w.page=c.page'
            rows=db.execute(sql if scope=='all' else sql+' WHERE m.id=?',() if scope=='all' else (int(scope),)).fetchall()
        if not rows:return []
        wordsets=[terms(r['text']) for r in rows]
        frequency={t:sum(t in words for words in wordsets) for t in query|context}
        summary=bool(re.search(r'\b(summar|overview|resum|describe|manual contains)',question,re.I))
        ranked=[]
        visual=bool(re.search(r'image|picture|screenshot|graph|chart|diagram|shown|looks|gr[ae]y|color|menu|where|imagen|captura|gr[aá]fic|gris|men[uú]|d[oó]nde',question,re.I))
        for r,words in zip(rows,wordsets):
            hits=query&words
            score=sum(math.log(1+len(rows)/(1+frequency[t])) for t in hits)
            if hits or len(query)<3:score+=.25*sum(math.log(1+len(rows)/(1+frequency[t])) for t in context&words)
            score+=2*len(query&terms(r['title'])) if hits else 0
            if hits and r['web_title']:
                heading=terms(r['web_title'].split(' - ',1)[0].split(' — ',1)[0])
                score+=2*len(query&terms(r['web_title']))
                if heading and heading<=query:score+=12
                if r['web_kind']=='image' and not visual:score*=.65
            if score or summary:ranked.append((score,dict(r)))
        ranked.sort(key=lambda item:(-item[0],item[1]['page'],item[1]['id']))
        semantic=self.semantic.scores(question,rows)
        if semantic:
            # Reciprocal rank fusion keeps exact names/terms useful alongside
            # paraphrases. A semantic match is evidence to inspect, not truth.
            fused={r['id']:1/(60+i) for i,(_,r) in enumerate(ranked,1)}
            by_id={r['id']:dict(r) for r in rows}
            for i,(rid,_) in enumerate(sorted(semantic.items(),key=lambda x:-x[1]),1):
                fused[rid]=fused.get(rid,0)+1/(60+i)
            ranked=sorted(((score,by_id[rid]) for rid,score in fused.items()),key=lambda x:(-x[0],x[1]['page'],x[1]['id']))
        result=[];used=set();budget=9500
        for _,row in ranked:
            marker=(row['manual_id'],row['page'],row['id'])
            if marker in used:continue
            text=row['text'][:budget]
            if not text:break
            result.append(dict(row,text=text,label=f"[M{row['manual_id']} p.{row['page']}]"))
            used.add(marker);budget-=len(text)
            if len(result)>=limit or budget<300:break
        with self.connect() as db:
            for source in result:
                web=db.execute('SELECT url,title,kind FROM web_page_sources WHERE manual_id=? AND page=?',(source['manual_id'],source['page'])).fetchone()
                if web:source.update(dict(web))
        return result
