import html
import secrets
from datetime import datetime
from urllib.parse import quote_plus

from aiohttp import web

from info import DATABASE_NAME, DATABASE_URI, MOVIE_ADMIN_PASSWORD, MOVIE_WEB_URL
import motor.motor_asyncio


_client = motor.motor_asyncio.AsyncIOMotorClient(DATABASE_URI)
_db = _client[DATABASE_NAME]
_movies = _db.movie_portal
_sessions = set()


async def _ensure_indexes():
    try:
        await _movies.create_index([("status", 1), ("release_date", 1)])
        await _movies.create_index([("created_at", -1)])
    except Exception:
        pass


def _is_admin(request):
    token = request.cookies.get("elsa_admin")
    return bool(token and token in _sessions and MOVIE_ADMIN_PASSWORD)


def _layout(title, body, admin=False):
    nav = (
        '<a href="/">Home</a><a href="/admin">Admin</a>' if admin
        else '<a href="/">Home</a>'
    )
    return f'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} • Elsa Movie Portal</title>
<style>
:root{{--bg:#07090f;--card:#111522;--muted:#9aa3b2;--text:#f5f7fb;--accent:#ff3158;--line:#252b3a;--green:#31d17c}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at top,#171b2d 0,#07090f 45%);color:var(--text);font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
a{{color:inherit;text-decoration:none}}.wrap{{max-width:1180px;margin:auto;padding:20px}}
nav{{display:flex;align-items:center;justify-content:space-between;padding:8px 0 24px}}nav .brand{{font-weight:900;font-size:22px}}nav .brand span{{color:var(--accent)}}nav .links{{display:flex;gap:18px;color:#cbd2df;font-size:14px}}
.hero{{padding:38px 0 25px}}.hero h1{{font-size:clamp(34px,6vw,64px);line-height:1;margin:0 0 12px}}.hero p{{color:var(--muted);max-width:650px;font-size:16px}}
.search{{display:flex;gap:10px;margin:25px 0}}input,select,textarea{{width:100%;background:#0d111b;color:var(--text);border:1px solid var(--line);border-radius:12px;padding:12px;font:inherit}}textarea{{min-height:100px;resize:vertical}}button,.btn{{border:0;border-radius:12px;padding:11px 16px;background:var(--accent);color:white;font-weight:800;cursor:pointer;display:inline-block}}.btn.secondary{{background:#1c2230}}.btn.danger{{background:#c62848}}
.section{{margin:28px 0}}.section h2{{font-size:22px;margin:0 0 14px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px}}.card{{background:rgba(17,21,34,.9);border:1px solid var(--line);border-radius:16px;overflow:hidden;transition:.18s}}.card:hover{{transform:translateY(-3px);border-color:#3b4357}}.poster{{aspect-ratio:2/3;background:#0c1018;overflow:hidden}}.poster img{{width:100%;height:100%;object-fit:cover}}.info{{padding:12px}}.title{{font-weight:800;line-height:1.25}}.meta{{font-size:12px;color:var(--muted);margin-top:7px}}.badge{{display:inline-block;margin-top:9px;padding:5px 8px;border-radius:999px;background:#252b39;font-size:11px;font-weight:800}}.badge.live{{background:rgba(49,209,124,.12);color:var(--green)}}.badge.soon{{background:rgba(255,49,88,.12);color:#ff6b86}}
.detail{{display:grid;grid-template-columns:260px 1fr;gap:28px;margin-top:25px}}.detail .poster{{border-radius:16px}}.detail h1{{font-size:38px;margin-top:0}}.muted{{color:var(--muted)}}.adminbox{{max-width:850px;margin:30px auto;background:rgba(17,21,34,.95);border:1px solid var(--line);border-radius:18px;padding:20px}}.formgrid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}label{{display:block;font-size:12px;color:#aeb6c5;margin-bottom:6px}}.full{{grid-column:1/-1}}.row{{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}}.tablewrap{{overflow:auto;margin-top:22px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;font-size:13px;vertical-align:top}}.flash{{background:#181e2b;border:1px solid var(--line);padding:12px;border-radius:12px;margin-bottom:15px}}.empty{{padding:35px;text-align:center;color:var(--muted);border:1px dashed var(--line);border-radius:16px}}
@media(max-width:650px){{.wrap{{padding:14px}}.detail{{grid-template-columns:1fr}}.detail .poster{{max-width:240px}}.formgrid{{grid-template-columns:1fr}}.full{{grid-column:auto}}.search{{flex-direction:column}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}}}
</style></head><body><div class="wrap"><nav><a class="brand" href="/">🎬 <span>ELSA</span> MOVIES</a><div class="links">{nav}</div></nav>{body}</div></body></html>'''


def _movie_card(m):
    mid = str(m.get("_id"))
    title = html.escape(m.get("title", "Untitled"))
    poster = html.escape(m.get("poster", ""), quote=True)
    lang = html.escape(m.get("language", ""))
    date = html.escape(m.get("release_date", ""))
    status = m.get("status", "released")
    badge = "NOW RELEASED" if status == "released" else "COMING SOON"
    cls = "live" if status == "released" else "soon"
    img = f'<img src="{poster}" alt="{title}" loading="lazy">' if poster else '<div></div>'
    return f'''<a class="card" href="/movie/{quote_plus(mid)}"><div class="poster">{img}</div><div class="info"><div class="title">{title}</div><div class="meta">{lang} {"•" if lang and date else ""} {date}</div><span class="badge {cls}">{badge}</span></div></a>'''


async def portal_home(request):
    await _ensure_indexes()
    q = request.query.get("q", "").strip()
    query = {"title": {"$regex": q, "$options": "i"}} if q else {}
    released = await _movies.find({**query, "status": "released"}).sort("release_date", -1).to_list(length=50)
    upcoming = await _movies.find({**query, "status": "coming_soon"}).sort("release_date", 1).to_list(length=50)
    body = f'''<section class="hero"><h1>Movie updates,<br><span style="color:var(--accent)">without the noise.</span></h1><p>Newly released and upcoming movies from Elsa1 — updated from the admin panel.</p><form class="search" method="get"><input name="q" value="{html.escape(q, quote=True)}" placeholder="Search movies..."><button>Search</button></form></section>'''
    body += '<section class="section"><h2>🔥 Now Released</h2>'
    body += '<div class="grid">' + ''.join(_movie_card(m) for m in released) + '</div>' if released else '<div class="empty">No released movies found.</div>'
    body += '</section><section class="section"><h2>🚀 Coming Soon</h2>'
    body += '<div class="grid">' + ''.join(_movie_card(m) for m in upcoming) + '</div>' if upcoming else '<div class="empty">No upcoming movies found.</div>'
    body += '</section>'
    return web.Response(text=_layout("Home", body), content_type="text/html")


async def movie_detail(request):
    mid = request.match_info["id"]
    try:
        from bson import ObjectId
        movie = await _movies.find_one({"_id": ObjectId(mid)})
    except Exception:
        movie = None
    if not movie:
        raise web.HTTPNotFound(text="Movie not found")
    title = html.escape(movie.get("title", "Untitled"))
    poster = html.escape(movie.get("poster", ""), quote=True)
    description = html.escape(movie.get("description", ""))
    status = "NOW RELEASED" if movie.get("status") == "released" else "COMING SOON"
    body = f'''<div class="detail"><div class="poster">{'<img src="'+poster+'" alt="'+title+'">' if poster else ''}</div><div><h1>{title}</h1><span class="badge">{status}</span><p class="muted">{html.escape(movie.get("language", ""))} • {html.escape(movie.get("genre", ""))} • {html.escape(movie.get("release_date", ""))}</p><p style="line-height:1.7">{description or "No description added yet."}</p></div></div>'''
    return web.Response(text=_layout(title, body), content_type="text/html")


async def admin_login(request):
    if request.method == "GET":
        body = '''<div class="adminbox"><h1>🔐 Admin Login</h1><p class="muted">Movie Portal administration</p><form method="post"><label>Password</label><input type="password" name="password" required><br><br><button>Login</button></form></div>'''
        return web.Response(text=_layout("Admin Login", body), content_type="text/html")
    data = await request.post()
    if not MOVIE_ADMIN_PASSWORD or data.get("password") != MOVIE_ADMIN_PASSWORD:
        body = '<div class="adminbox"><div class="flash">Invalid password.</div><a class="btn secondary" href="/admin/login">Try again</a></div>'
        return web.Response(text=_layout("Login failed", body), content_type="text/html", status=401)
    token = secrets.token_urlsafe(32)
    _sessions.add(token)
    response = web.HTTPFound("/admin")
    response.set_cookie("elsa_admin", token, httponly=True, samesite="Lax", secure=False, max_age=86400)
    raise response


async def admin_logout(request):
    token = request.cookies.get("elsa_admin")
    _sessions.discard(token)
    response = web.HTTPFound("/admin/login")
    response.del_cookie("elsa_admin")
    raise response


def _admin_form(movie=None):
    m = movie or {}
    mid = str(m.get("_id", ""))
    action = f"/admin/movie/{mid}/save" if mid else "/admin/movie/save"
    return f'''<form method="post" action="{action}" class="formgrid">
<label>Title<input name="title" required value="{html.escape(m.get("title", ""), quote=True)}"></label>
<label>Poster URL<input name="poster" value="{html.escape(m.get("poster", ""), quote=True)}" placeholder="https://..."></label>
<label>Release date<input type="date" name="release_date" value="{html.escape(m.get("release_date", ""), quote=True)}"></label>
<label>Language<input name="language" value="{html.escape(m.get("language", ""), quote=True)}" placeholder="Malayalam / English / Hindi"></label>
<label>Genre<input name="genre" value="{html.escape(m.get("genre", ""), quote=True)}"></label>
<label>Status<select name="status"><option value="released" {"selected" if m.get("status")=="released" else ""}>Now Released</option><option value="coming_soon" {"selected" if m.get("status")=="coming_soon" else ""}>Coming Soon</option></select></label>
<label class="full">Description<textarea name="description">{html.escape(m.get("description", ""))}</textarea></label>
<div class="full"><button>💾 Save Movie</button> <a class="btn secondary" href="/admin">Cancel</a></div></form>'''


async def admin_page(request):
    if not _is_admin(request):
        raise web.HTTPFound("/admin/login")
    movies = await _movies.find({}).sort("created_at", -1).to_list(length=200)
    rows = []
    for m in movies:
        mid = str(m["_id"])
        rows.append(f'''<tr><td><b>{html.escape(m.get("title", ""))}</b><br><span class="muted">{html.escape(m.get("status", ""))} • {html.escape(m.get("release_date", ""))}</span></td><td><a class="btn secondary" href="/admin/movie/{mid}">Edit</a> <form style="display:inline" method="post" action="/admin/movie/{mid}/delete" onsubmit="return confirm('Delete this movie?')"><button class="danger">Delete</button></form></td></tr>''')
    body = f'''<div class="row"><div><h1>🎬 Movie Admin</h1><p class="muted">Add and update what users see on the public portal.</p></div><a class="btn secondary" href="/admin/logout">Logout</a></div><div class="adminbox"><h2>Add movie</h2>{_admin_form()}</div><div class="adminbox"><h2>Movies ({len(movies)})</h2><div class="tablewrap"><table><tr><th>Movie</th><th>Actions</th></tr>{''.join(rows) or '<tr><td colspan="2">No movies yet.</td></tr>'}</table></div></div>'''
    return web.Response(text=_layout("Admin", body, admin=True), content_type="text/html")


async def admin_movie(request):
    if not _is_admin(request):
        raise web.HTTPFound("/admin/login")
    from bson import ObjectId
    try:
        movie = await _movies.find_one({"_id": ObjectId(request.match_info["id"])})
    except Exception:
        movie = None
    if not movie:
        raise web.HTTPNotFound(text="Movie not found")
    body = f'<div class="adminbox"><h1>✏️ Edit Movie</h1>{_admin_form(movie)}</div>'
    return web.Response(text=_layout("Edit Movie", body, admin=True), content_type="text/html")


async def _save_movie(request, movie_id=None):
    if not _is_admin(request):
        raise web.HTTPFound("/admin/login")
    data = await request.post()
    movie = {
        "title": str(data.get("title", "")).strip(),
        "poster": str(data.get("poster", "")).strip(),
        "release_date": str(data.get("release_date", "")).strip(),
        "language": str(data.get("language", "")).strip(),
        "genre": str(data.get("genre", "")).strip(),
        "status": "released" if data.get("status") == "released" else "coming_soon",
        "description": str(data.get("description", "")).strip(),
        "updated_at": datetime.utcnow(),
    }
    if not movie["title"]:
        raise web.HTTPBadRequest(text="Title is required")
    from bson import ObjectId
    if movie_id:
        await _movies.update_one({"_id": ObjectId(movie_id)}, {"$set": movie})
    else:
        movie["created_at"] = datetime.utcnow()
        await _movies.insert_one(movie)
    raise web.HTTPFound("/admin")


async def admin_add(request):
    return await _save_movie(request)


async def admin_edit_save(request):
    return await _save_movie(request, request.match_info["id"])


async def admin_delete(request):
    if not _is_admin(request):
        raise web.HTTPFound("/admin/login")
    from bson import ObjectId
    try:
        await _movies.delete_one({"_id": ObjectId(request.match_info["id"])})
    except Exception:
        pass
    raise web.HTTPFound("/admin")


def register_movie_portal(routes):
    routes.get("/", allow_head=True)(portal_home)
    routes.get("/movie/{id}")(movie_detail)
    routes.get("/admin/login")(admin_login)
    routes.post("/admin/login")(admin_login)
    routes.get("/admin/logout")(admin_logout)
    routes.get("/admin")(admin_page)
    routes.get("/admin/movie/{id}")(admin_movie)
    routes.post("/admin/movie/save")(admin_add)
    routes.post("/admin/movie/{id}/save")(admin_edit_save)
    routes.post("/admin/movie/{id}/delete")(admin_delete)
