import re, json, os
from urllib.parse import urljoin, quote
from flask import Flask, request, jsonify, render_template
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.5',
}

BASE_BF = 'https://new.bollyflix.gd'
BASE_AF = 'https://animeflix.dad'
TMDB_KEY = os.environ.get('TMDB_API_KEY', '')
TMDB_BASE = 'https://api.themoviedb.org/3'
TMDB_IMG  = 'https://image.tmdb.org/t/p'

session = requests.Session()
session.headers.update(HEADERS)


def get(url, timeout=15):
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def soup(url):
    return BeautifulSoup(get(url), 'html.parser')


def quality_label(text):
    t = str(text).lower()
    if '2160p' in t or '4k' in t: return '2160p 4K'
    if '1080p' in t: return '1080p'
    if '720p' in t: return '720p'
    if '480p' in t: return '480p'
    return 'N/A'


def tmdb_search(query):
    if not TMDB_KEY or not query: return None
    try:
        r = session.get(f'{TMDB_BASE}/search/movie', params={'api_key': TMDB_KEY, 'query': query}, timeout=10)
        if not r.ok: return None
        data = r.json()
        results = data.get('results', [])
        if results:
            m = results[0]
            return {
                'id': m['id'],
                'title': m.get('title', ''),
                'overview': m.get('overview', ''),
                'poster': f"{TMDB_IMG}/w500{m['poster_path']}" if m.get('poster_path') else '',
                'year': (m.get('release_date') or '').split('-')[0],
                'rating': m.get('vote_average', 0),
                'genres': [g['name'] for g in m.get('genres', [])],
            }
    except Exception: pass
    return None


def tmdb_details(mid):
    if not TMDB_KEY or not mid: return None
    try:
        r = session.get(f"{TMDB_BASE}/movie/{mid}", params={'api_key': TMDB_KEY, 'append_to_response': 'credits'}, timeout=10)
        if not r.ok: return None
        d = r.json()
        genres = [g['name'] for g in d.get('genres', [])]
        cast = [c['name'] for c in d.get('credits', {}).get('cast', [])[:5]]
        return {'overview': d.get('overview', ''), 'rating': d.get('vote_average', 0), 'genres': genres, 'cast': cast}
    except Exception: pass
    return None


# ── BollyFlix ──

@app.route('/search', methods=['GET'])
def search():
    q = request.args.get('q', '').strip()
    t  = request.args.get('type', 'movie')
    if not q: return jsonify([])
    results = []
    try:
        pg = soup(f"{BASE_BF}/?s={quote(q)}")
        for art in pg.select('article')[:30]:
            a = art.select_one('h2 a, h3 a')
            if not a: continue
            href, title = a['href'], a.get_text(strip=True)
            img = art.select_one('img')['src'] if art.select_one('img') else ''
            meta = art.select_one('.entry-meta')
            results.append({
                'title': title, 'href': href,
                'img': img, 'meta': meta.get_text(strip=True) if meta else '',
                'source': 'BollyFlix'
            })
    except Exception: pass
    try:
        if t == 'anime':
            pg = soup(f"{BASE_AF}/?s={quote(q)}")
            for art in pg.select('article')[:30]:
                a = art.select_one('h2 a, h3 a')
                if not a or 'animeflix.dad' not in a.get('href', ''): continue
                href, title = a['href'], a.get_text(strip=True)
                img = art.select_one('img')['src'] if art.select_one('img') else ''
                results.append({
                    'title': title, 'href': href, 'img': img, 'meta': '',
                    'source': 'AnimeFlix'
                })
    except Exception: pass
    return jsonify(results)


@app.route('/tmdb', methods=['GET'])
def tmdb():
    q = request.args.get('q', '').strip()
    if not q or not TMDB_KEY: return jsonify(None)
    return jsonify(tmdb_search(q))


@app.route('/tmdb-details', methods=['GET'])
def tmdb_details_api():
    mid = request.args.get('id')
    if not mid or not TMDB_KEY: return jsonify(None)
    return jsonify(tmdb_details(int(mid)))


@app.route('/details', methods=['GET'])
def details():
    src = request.args.get('source')
    url  = request.args.get('url')
    if not src or not url: return jsonify({'error': 'missing params'}), 400
    downloads = []
    try:
        pg = soup(url)
        if src == 'BollyFlix':
            title_el = pg.select_one('h1.entry-title')
            title = title_el.get_text(strip=True) if title_el else ''
            for a in pg.find_all('a', href=True):
                href = a['href']
                txt  = a.get_text(strip=True)
                if ('fastdlserver.site' in href or 'drive.google' in href or 'download' in txt.lower()):
                    dl = {'name': txt, 'size': 'N/A', 'quality': quality_label(txt), 'url': href}
                    parent = a.parent
                    if parent:
                        nxt = parent.next_sibling
                        if nxt:
                            dl['size'] = nxt.get_text(strip=True).strip('[]') if hasattr(nxt,'get_text') else str(nxt).strip('[]')
                    downloads.append(dl)
        elif src == 'AnimeFlix':
            title_el = pg.select_one('h1.entry-title')
            title = title_el.get_text(strip=True) if title_el else ''
            # Step 1: Find archives links on article page
            archives = []
            for a in pg.find_all('a', href=True):
                if 'episodes.animeflix.dad/archives/' in a['href']:
                    archives.append({'url': a['href'], 'name': a.get_text(strip=True)})
            # Step 2: Follow each archive → getlinks → driveseed
            for arc in archives:
                try:
                    arc_html = session.get(arc['url'], timeout=15).text
                    arc_soup = BeautifulSoup(arc_html, 'html.parser')
                    for ga in arc_soup.find_all('a', href=True):
                        ghref = ga['href']
                        if 'getlink' not in ghref: continue
                        try:
                            # Step 3: Getlink → HTTP 200 with JS redirect
                            dr_resp = session.get(ghref, timeout=20, allow_redirects=True)
                            dr_text = dr_resp.text
                            js_match = re.search(r'window\.location\.replace\(["\']/([^"\']+)["\']', dr_text)
                            if not js_match: continue
                            file_path = js_match.group(1)  # e.g. "file/8K0FdnJ88Vnj9Tmd2oN9"
                            file_url = 'https://driveseed.org/' + file_path
                            file_resp = session.get(file_url, timeout=15)
                            file_html = file_resp.text
                            # Step 4: Find CDN download links
                            final_url = ''
                            for fa in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', file_html, re.I):
                                fh, ft = fa.group(1), fa.group(2)
                                if 'instant' in ft.lower():
                                    final_url = fh
                                    break
                            if not final_url:
                                cdn_match = re.search(r'https://cdn\.video[-a-z]+\.xyz[^"\'<>\s]+', file_html)
                                if cdn_match:
                                    final_url = cdn_match.group(0).strip().rstrip('"\'')
                            if final_url:
                                ep = re.search(r'[\d]+', arc['name'])
                                ep_num = int(ep.group(0)) if ep else 0
                                downloads.append({
                                    'name': arc['name'] or f'Episode {ep_num}' if ep_num else 'Episode',
                                    'quality': quality_label(arc['name'] + ' ' + title),
                                    'url': final_url,
                                    'size': 'N/A',
                                    'episode': ep_num,
                                })
                        except Exception:
                            continue
                except Exception:
                    continue
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'title': title, 'downloads': downloads})


@app.route('/')
def home():
    return render_template('index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
