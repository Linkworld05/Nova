# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded public-documentation import, including locally analyzed article images."""
import base64,hashlib,io,ipaddress,json,re,shutil,socket,tempfile,zipfile
from collections import deque
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urlunsplit
import httpx
from bs4 import BeautifulSoup
from PIL import Image
from picture_analysis import prepare_picture

MAX_PAGES=80
MAX_IMAGES=120
MAX_TOTAL=120*1024*1024

class SnapshotTooLarge(ValueError):pass

def normalize_url(url):
    p=urlsplit(url.strip())
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('Enter a public http/https manual URL without login credentials.')
    return urlunsplit((p.scheme,p.netloc,p.path or '/',p.query,''))

def check_public(url):
    host=urlsplit(normalize_url(url)).hostname
    for result in socket.getaddrinfo(host,None,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(result[4][0]).is_global:
            raise ValueError('Website imports require a public internet address.')

def fetch(client,url,limit):
    url=normalize_url(url)
    for _ in range(6):
        check_public(url)
        with client.stream('GET',url,follow_redirects=False) as response:
            if response.is_redirect:
                url=normalize_url(urljoin(url,response.headers['location']));continue
            response.raise_for_status();data=bytearray()
            for part in response.iter_bytes():
                data.extend(part)
                if len(data)>limit:raise ValueError('Website resource exceeds the import size limit.')
            return str(response.url),bytes(data),response.headers.get('content-type','').lower()
    raise ValueError('The website redirected too many times.')

def parse_page(html,url):
    soup=BeautifulSoup(html,'html.parser')
    links=[urljoin(url,a['href']) for a in soup.select('a[href]')]
    content=soup.select_one('article') or soup.select_one('main') or soup.body or soup
    for element in content.select('script,style,nav,header,footer,aside,form,button'):element.decompose()
    title=(soup.title.get_text(' ',strip=True) if soup.title else '')
    heading=content.find(['h1','h2'])
    title=(title or (heading.get_text(' ',strip=True) if heading else url))[:250]
    images=[]
    for i,img in enumerate(content.select('img'),1):
        src=img.get('src') or img.get('data-src')
        if not src:continue
        image_url=urljoin(url,src)
        caption=img.get('alt','').strip() or Path(urlsplit(image_url).path).stem.replace('-',' ').replace('_',' ')
        figure=img.find_parent('figure')
        if figure and figure.find('figcaption'):caption=figure.find('figcaption').get_text(' ',strip=True)
        images.append({'url':image_url,'caption':(caption or f'Figure {i}')[:500]})
    return {'url':url,'title':title,'text':content.get_text('\n',strip=True),'images':images,'links':links}

def in_scope(url,seed,prefix):
    try:
        p=urlsplit(normalize_url(url));root=urlsplit(seed)
        return (p.netloc.lower()==root.netloc.lower() and p.path.startswith(prefix)
                and not p.query and Path(p.path).suffix.lower() in ('','.html','.htm')
                and '/assets/' not in p.path)
    except ValueError:return False

def render_reference(documents,target):
    """Save real text/figure pages and return their exact PDF page positions."""
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader,simpleSplit
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font='Helvetica'
    if Path('C:/Windows/Fonts/arial.ttf').is_file():
        if 'NovaWebManual' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('NovaWebManual','C:/Windows/Fonts/arial.ttf'))
        font='NovaWebManual'
    c=Canvas(str(target),pagesize=A4);width,height=A4;pages=[]
    def begin(title,url):
        c.setFont(font,11);c.drawString(42,height-40,title[:85])
        c.setFont(font,7);c.drawString(42,height-54,url[:120])
        c.linkURL(url,(42,height-58,width-42,height-45),relative=0)
    def finish(text,url,title,kind='text',image=None):
        number=len(pages)+1;c.setFont(font,8)
        c.drawString(42,24,'Saved website reference');c.drawRightString(width-42,24,'Page '+str(number));c.showPage()
        pages.append({'page':number,'text':text,'url':url,'title':title,'kind':kind,'image':image})
    for doc in documents:
        begin(doc['title'],doc['url']);y=height-78;part=[];c.setFont(font,10)
        for original in doc['text'].splitlines():
            # Bound unbroken code/URL lines before applying proportional wrapping.
            chunks=[original[i:i+90] for i in range(0,len(original),90)] if ' ' not in original and len(original)>90 else [original]
            for chunk in chunks:
                for line in simpleSplit(chunk,font,10,width-84) or ['']:
                    if y<55:
                        finish('\n'.join(part),doc['url'],doc['title']);begin(doc['title'],doc['url']);y=height-78;part=[];c.setFont(font,10)
                    c.drawString(42,y,line);part.append(line);y-=14
        finish('\n'.join(part),doc['url'],doc['title'])
        for image in doc['images']:
            if not image.get('path'):continue
            title=doc['title']+' — '+image['caption'];begin(title,doc['url'])
            with Image.open(image['path']) as im:iw,ih=im.size
            scale=min((width-84)/iw,270/ih);draw_w,draw_h=iw*scale,ih*scale
            c.drawImage(ImageReader(str(image['path'])),42,height-75-draw_h,width=draw_w,height=draw_h,mask='auto')
            text=('Figure: '+image['caption']+'\nSource page: '+doc['url']+'\nImage URL: '+image['url']
                  +'\nOCR text from image:\n'+image.get('ocr','')+'\nLocal visual analysis (generated; verify against image):\n'+image.get('analysis',''))
            y=height-90-draw_h;c.setFont(font,9)
            for line in simpleSplit(text[:1800].replace('\n','  '),font,9,width-84):
                if y<55:break
                c.drawString(42,y,line);y-=12
            finish(text,doc['url'],title,'image',image)
    c.save();return pages

def import_site(library,url,include_links=True,progress=lambda text:None,ocr=None,vision=None):
    seed=normalize_url(url)
    p=urlsplit(seed)
    # MkDocs documentation shares a /docs/ root. Other sites stay in the seed's directory.
    prefix=p.path.split('/docs/',1)[0]+'/docs/' if '/docs/' in p.path else (p.path if p.path.endswith('/') else p.path.rsplit('/',1)[0]+'/')
    documents=[];visited=set();pending=deque([seed]);warnings=[];total=0;assets={}
    with tempfile.TemporaryDirectory(dir=library.folder,prefix='website-') as work:
        work=Path(work);(work/'images').mkdir();(work/'pages').mkdir()
        with httpx.Client(timeout=25,headers={'User-Agent':'NovaManualLibrary/1.0'},follow_redirects=False) as client:
            while pending and len(documents)<MAX_PAGES and len(visited)<MAX_PAGES*2:
                current=pending.popleft()
                if current in visited:continue
                visited.add(current);progress(f'Reading website page {len(documents)+1}: {current}')
                try:
                    final,html,mime=fetch(client,current,4*1024*1024)
                    if final in visited and final!=current:continue
                    visited.add(final)
                    if 'html' not in mime:raise ValueError('This URL is not an HTML manual page.')
                    total+=len(html)
                    if total>MAX_TOTAL:raise SnapshotTooLarge('Website snapshot exceeds 120 MB.')
                    doc=parse_page(html,final)
                    if len(doc['text'])<20:raise ValueError('No readable article text. This page may require browser rendering or login.')
                except SnapshotTooLarge:raise
                except Exception as e:
                    if not documents:raise
                    warnings.append(current+': '+str(e));continue
                (work/'pages'/f'{len(documents)+1:03d}.html').write_bytes(html)
                documents.append(doc)
                if include_links:
                    pending.extend(normalize_url(link) for link in doc['links'] if in_scope(link,seed,prefix) and normalize_url(link) not in visited)
                for image in doc['images']:
                    asset=image['url']
                    if asset in assets:image.update(assets[asset]);continue
                    if len(assets)>=MAX_IMAGES:warnings.append('Image limit reached: '+asset);continue
                    assets[asset]={}
                    try:
                        _,raw,_=fetch(client,asset,12*1024*1024);total+=len(raw)
                        if total>MAX_TOTAL:raise SnapshotTooLarge('Website snapshot exceeds 120 MB.')
                        sha=hashlib.sha256(raw).hexdigest()
                        with Image.open(io.BytesIO(raw)) as im:
                            if im.width*im.height>40_000_000:raise ValueError('Image exceeds 40 million pixels.')
                            ext={'JPEG':'jpg','PNG':'png','WEBP':'webp','GIF':'gif','BMP':'bmp'}.get(im.format)
                            if not ext:raise ValueError('Unsupported article image format.')
                        path=work/'images'/(sha+'.'+ext);path.write_bytes(raw)
                        info={'path':path,'sha':sha,'file':'images/'+path.name};assets[asset]=info;image.update(info)
                    except SnapshotTooLarge:raise
                    except Exception as e:warnings.append(asset+': '+str(e))
            if pending:warnings.append('Documentation page limit reached; remaining links were not imported.')
        identity=[{'url':d['url'],'text':d['text'],'images':[(im['url'],im.get('sha')) for im in d['images']]} for d in documents]
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with library.connect() as db:existing=db.execute('SELECT id FROM manuals WHERE digest=?',(digest,)).fetchone()
        if existing:library.set_scope(str(existing['id']));return dict(library.get(existing['id']),duplicate=True)
        analyzed={}
        for doc in documents:
            for image in doc['images']:
                if not image.get('path'):continue
                if image['sha'] in analyzed:image.update(analyzed[image['sha']]);continue
                progress(f"Reading saved image {len(analyzed)+1} of {len(assets)}: {image['caption']}")
                picture=prepare_picture(image['path']);info={'ocr':'','analysis':''}
                if ocr:
                    try:info['ocr']=ocr(picture.encoded)
                    except Exception as e:warnings.append('Image OCR unavailable: '+str(e))
                if vision:
                    try:info['analysis']=vision(picture.encoded,'Describe this documentation image: its visible controls, layout, labels, and any diagram relationships. Do not invent tiny text. Caption: '+image['caption'])
                    except Exception as e:warnings.append('Image visual analysis unavailable: '+str(e))
                image.update(info);analyzed[image['sha']]=info
        pages=render_reference(documents,work/'reference.pdf')
        manifest={'seed':seed,'include_links':include_links,'fetched':datetime.now(timezone.utc).isoformat(),'pages':[{'url':d['url'],'title':d['title'],'text':d['text'],'images':[{k:str(v) for k,v in im.items() if k!='path'} for im in d['images']]} for d in documents],'warnings':warnings}
        (work/'website.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        with zipfile.ZipFile(work/'source.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for folder in ('pages','images'):
                for file in (work/folder).iterdir():archive.write(file,file.relative_to(work))
            archive.write(work/'website.json','website.json')
        destination=library.folder/digest;destination.mkdir(exist_ok=True)
        for file in ('reference.pdf','website.json','source.zip'):shutil.copy2(work/file,destination/file)
        shutil.copytree(work/'images',destination/'images',dirs_exist_ok=True)
        notes=f'Website reference-copy pages; {len(documents)} web pages, {len(analyzed)} article images; source: {seed}'
        if warnings:notes+=f'; {len(warnings)} import warnings (see website.json)'
        title=documents[0]['title']
        if include_links and ' - ' in title:title=title.split(' - ',1)[1]
        with library.connect() as db:
            name='website:'+seed;version=db.execute('SELECT COALESCE(MAX(version),0)+1 FROM manuals WHERE name=?',(name,)).fetchone()[0]
            mid=db.execute('INSERT INTO manuals(title,name,digest,version,folder,pages,imported,notes) VALUES(?,?,?,?,?,?,?,?)',(title,name,digest,version,digest,len(pages),manifest['fetched'],notes)).lastrowid
            for page in pages:
                number=page['page'];text=page['text'];im=page['image']
                db.execute('INSERT INTO pages VALUES(?,?,?,0)',(mid,number,text))
                db.execute('INSERT INTO web_page_sources VALUES(?,?,?,?,?)',(mid,number,page['url'],page['title'],page['kind']))
                for start in range(0,len(text),1100):db.execute('INSERT INTO chunks(manual_id,page,text) VALUES(?,?,?)',(mid,number,text[start:start+1400]))
                if im:db.execute('INSERT INTO web_images VALUES(?,?,?,?,?,?,?,?)',(mid,number,im['url'],im['file'],im['caption'],im.get('ocr',''),im.get('analysis',''),im['sha']))
            db.execute("INSERT INTO settings VALUES('scope',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(mid),))
        return dict(library.get(mid),duplicate=False)
