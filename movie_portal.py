import html
import hashlib
import hmac
import secrets
import time
from datetime import datetime

import motor.motor_asyncio
from aiohttp import web
from bson import ObjectId

from info import DATABASE_NAME, DATABASE_URI, MOVIE_ADMIN_PASSWORD

_client = motor.motor_asyncio.AsyncIOMotorClient(DATABASE_URI)
_movies = _client[DATABASE_NAME].movie_portal

_SESSION_TTL = 86400
_COOKIE_NAME = "elsa_admin"


def esc(value, quote=False):
    return html.escape(str(value or ""), quote=quote)


def _session_token():
    now = str(int(time.time()))
    nonce = secrets.token_urlsafe(16)
    payload = f"{now}.{nonce}"
    secret = hashlib.sha256(MOVIE_ADMIN_PASSWORD.encode("utf-8")).digest()
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def admin_ok(request):
    token = request.cookies.get(_COOKIE_NAME, "")
    if not MOVIE_ADMIN_PASSWORD or not token:
        return False
    try:
        issued_at, nonce, signature = token.split(".", 2)
        issued_at = int(issued_at)
        if issued_at <= 0 or time.time() - issued_at > _SESSION_TTL:
            return False
        payload = f"{issued_at}.{nonce}"
        secret = hashlib.sha256(MOVIE_ADMIN_PASSWORD.encode("utf-8")).digest()
        expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


def page(title, body):
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} • Elsa Movies</title><style>
:root{{--bg:#07090f;--card:#111522;--muted:#9aa3b2;--text:#f5f7fb;--accent:#ff3158;--line:#252b3a;--green:#31d17c}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at top,#181c30,#07090f 48%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,sans-serif}}a{{color:inherit;text-decoration:none}}.wrap{{max-width:1150px;margin:auto;padding:18px}}nav{{display:flex;justify-content:space-between;align-items:center;padding:8px 0 24px}}.brand{{font-size:21px;font-weight:900}}.brand b{{color:var(--accent)}}.links{{display:flex;gap:18px;color:#cbd2df;font-size:14px}}h1{{font-size:clamp(36px,6vw,64px);line-height:1;margin:25px 0 12px}}h2{{margin-top:30px}}p{{color:var(--muted);line-height:1.6}}.search{{display:flex;gap:10px;margin:25px 0}}input,select,textarea{{width:100%;background:#0d111b;color:var(--text);border:1px solid var(--line);border-radius:11px;padding:11px;font:inherit}}textarea{{min-height:100px}}button,.btn{{border:0;border-radius:11px;padding:11px 15px;background:var(--accent);color:white;font-weight:800;cursor:pointer;display:inline-block}}.secondary{{background:#202635}}.danger{{background:#b82747}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:15px}}.card{{background:rgba(17,21,34,.92);border:1px solid var(--line);border-radius:15px;overflow:hidden}}.poster{{aspect-ratio:2/3;background:#0c1018}}.poster img{{width:100%;height:100%;object-fit:cover}}.info{{padding:11px}}.title{{font-weight:800;line-height:1.25}}.meta{{font-size:12px;color:var(--muted);margin-top:7px}}.badge{{display:inline-block;margin-top:8px;padding:5px 8px;border-radius:999px;background:#252b39;font-size:10px;font-weight:900}}.released{{background:#103122;color:var(--green)}}.soon{{background:#351520;color:#ff7890}}.detail,.adminbox{{background:rgba(17,21,34,.94);border:1px solid var(--line);border-radius:17px;padding:20px;margin-top:25px}}.detail{{display:grid;grid-template-columns:260px 1fr;gap:25px}}.detail .poster{{border-radius:14px;overflow:hidden}}.formgrid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}label{{font-size:12px;color:#adb5c3}}label input,label select,label textarea{{margin-top:5px}}.full{{grid-column:1/-1}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left}}.tablewrap{{overflow:auto}}@media(max-width:650px){{.grid{{grid-template-columns:repeat(2,1fr);gap:9px}}.detail{{grid-template-columns:1fr}}.formgrid{{grid-template-columns:1fr}}.full{{grid-column:auto}}.search{{flex-direction:column}}}}
</style></head><body><div class="wrap"><nav><a class="brand" href="/">🎬 <b>ELSA</b> MOVIES</a><div class="links"><a href="/">Home</a><a href="/admin">Admin</a></div></nav>{body}</div></body></html>'''


def card(m):
    poster = esc(m.get("poster"), True)
    img = f'<img src="{poster}" alt="{esc(m.get("title"), True)}" loading="lazy">' if poster else ''
    status = m.get("status", "released")
    badge = "NOW RELEASED" if status == "released" else "COMING SOON"
    cls = "released" if status == "released" else "soon"
    return f'<a class="card" href="/movie/{m["_id"]}"><div class="poster">{img}</div><div class="info"><div class="title">{esc(m.get("title"))}</div><div class="meta">{esc(m.get("language"))} • {esc(m.get("release_date"))}</div><span class="badge {cls}">{badge}</span></div></a>'


async def home(request):
    q = request.query.get("q", "").strip()
    base = {"title": {"$regex": q, "$options": "i"}} if q else {}
    released = await _movies.find({**base, "status": "released"}).sort("release_date", -1).to_list(60)
    upcoming = await _movies.find({**base, "status": "coming_soon"}).sort("release_date", 1).to_list(60)
    body = f'<section><h1>Movie updates,<br><span style="color:var(--accent)">without the noise.</span></h1><p>Newly released and upcoming movies, maintained directly from Elsa1.</p><form class="search"><input name="q" value="{esc(q, True)}" placeholder="Search movies..."><button>Search</button></form></section>'
    body += '<h2>🔥 Now Released</h2><div class="grid">' + ''.join(card(x) for x in released) + '</div>' if released else '<h2>🔥 Now Released</h2><p>No released movies yet.</p>'
    body += '<h2>🚀 Coming Soon</h2><div class="grid">' + ''.join(card(x) for x in upcoming) + '</div>' if upcoming else '<h2>🚀 Coming Soon</h2><p>No upcoming movies yet.</p>'
    return web.Response(text=page("Home", body), content_type="text/html")


async def detail(request):
    try:
        movie = await _movies.find_one({"_id": ObjectId(request.match_info["id"])})
    except Exception:
        movie = None
    if not movie:
        raise web.HTTPNotFound(text="Movie not found")
    poster = esc(movie.get("poster"), True)
    poster_html = f'<img src="{poster}" alt="{esc(movie.get("title"), True)}">' if poster else ''
    body = f'<div class="detail"><div class="poster">{poster_html}</div><div><h1>{esc(movie.get("title"))}</h1><p>{esc(movie.get("language"))} • {esc(movie.get("genre"))} • {esc(movie.get("release_date"))}</p><p>{esc(movie.get("description"))}</p></div></div>'
    return web.Response(text=page(movie.get("title", "Movie"), body), content_type="text/html")


def form(m=None):
    m = m or {}
    mid = str(m.get("_id", ""))
    action = f"/admin/movie/{mid}/save" if mid else "/admin/movie/save"
    r = m.get("status", "coming_soon")
    return f'''<form method="post" action="{action}" class="formgrid"><label>Title<input name="title" required value="{esc(m.get("title"), True)}"></label><label>Poster URL<input name="poster" value="{esc(m.get("poster"), True)}"></label><label>Release date<input type="date" name="release_date" value="{esc(m.get("release_date"), True)}"></label><label>Language<input name="language" value="{esc(m.get("language"), True)}"></label><label>Genre<input name="genre" value="{esc(m.get("genre"), True)}"></label><label>Status<select name="status"><option value="released" {"selected" if r=="released" else ""}>Now Released</option><option value="coming_soon" {"selected" if r!="released" else ""}>Coming Soon</option></select></label><label class="full">Description<textarea name="description">{esc(m.get("description"))}</textarea></label><div class="full"><button>💾 Save Movie</button> <a class="btn secondary" href="/admin">Cancel</a></div></form>'''


async def login(request):
    if request.method == "GET":
        return web.Response(text=page("Admin Login", '<div class="adminbox"><h1>🔐 Admin Login</h1><form method="post"><input type="password" name="password" placeholder="Admin password" required><br><br><button>Login</button></form></div>'), content_type="text/html")
    data = await request.post()
    if not MOVIE_ADMIN_PASSWORD or data.get("password") != MOVIE_ADMIN_PASSWORD:
        raise web.HTTPUnauthorized(text="Invalid password")
    response = web.HTTPFound("/admin")
    response.set_cookie(_COOKIE_NAME, _session_token(), httponly=True, samesite="Lax", max_age=_SESSION_TTL, secure=True)
    raise response


async def logout(request):
    response = web.HTTPFound("/admin/login")
    response.del_cookie(_COOKIE_NAME)
    raise response


async def admin(request):
    if not admin_ok(request):
        raise web.HTTPFound("/admin/login")
    movies = await _movies.find({}).sort("created_at", -1).to_list(200)
    rows = ''.join(f'<tr><td><b>{esc(m.get("title"))}</b><br><small>{esc(m.get("status"))} • {esc(m.get("release_date"))}</small></td><td><a class="btn secondary" href="/admin/movie/{m["_id"]}">Edit</a> <form style="display:inline" method="post" action="/admin/movie/{m["_id"]}/delete"><button class="danger" onclick="return confirm(\'Delete this movie?\')">Delete</button></form></td></tr>' for m in movies)
    body = f'<div class="adminbox"><h1>🎬 Movie Admin</h1><p>Add, edit or delete movies shown publicly.</p>{form()}</div><div class="adminbox"><h2>Movies ({len(movies)})</h2><div class="tablewrap"><table><tr><th>Movie</th><th>Actions</th></tr>{rows}</table></div><br><a class="btn secondary" href="/admin/logout">Logout</a></div>'
    return web.Response(text=page("Admin", body), content_type="text/html")


async def edit_page(request):
    if not admin_ok(request):
        raise web.HTTPFound("/admin/login")
    try:
        m = await _movies.find_one({"_id": ObjectId(request.match_info["id"])})
    except Exception:
        m = None
    if not m:
        raise web.HTTPNotFound(text="Movie not found")
    return web.Response(text=page("Edit Movie", f'<div class="adminbox"><h1>✏️ Edit Movie</h1>{form(m)}</div>'), content_type="text/html")


async def save(request, movie_id=None):
    if not admin_ok(request):
        raise web.HTTPFound("/admin/login")
    d = await request.post()
    movie = {"title": str(d.get("title", "")).strip(), "poster": str(d.get("poster", "")).strip(), "release_date": str(d.get("release_date", "")).strip(), "language": str(d.get("language", "")).strip(), "genre": str(d.get("genre", "")).strip(), "status": "released" if d.get("status") == "released" else "coming_soon", "description": str(d.get("description", "")).strip(), "updated_at": datetime.utcnow()}
    if not movie["title"]:
        raise web.HTTPBadRequest(text="Title is required")
    if movie_id:
        await _movies.update_one({"_id": ObjectId(movie_id)}, {"$set": movie})
    else:
        movie["created_at"] = datetime.utcnow()
        await _movies.insert_one(movie)
    raise web.HTTPFound("/admin")


async def delete(request):
    if not admin_ok(request):
        raise web.HTTPFound("/admin/login")
    try:
        await _movies.delete_one({"_id": ObjectId(request.match_info["id"])})
    except Exception:
        pass
    raise web.HTTPFound("/admin")
