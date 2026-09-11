from aiohttp import web

routes = web.RouteTableDef()

from .movie_portal import home, detail, login, logout, admin, edit_page, save, delete, poster_upload, poster_file

@routes.get("/health", allow_head=True)
async def health_route(request):
    return web.json_response({"status": "ok", "service": "Elsa Movie Portal"})

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return await home(request)

routes.get("/movie/{id}")(detail)
routes.get("/poster/{id}")(poster_file)
routes.get("/admin/login")(login)
routes.post("/admin/login")(login)
routes.get("/admin/logout")(logout)
routes.get("/admin")(admin)
routes.post("/admin/poster/upload")(poster_upload)
routes.get("/admin/movie/{id}")(edit_page)
routes.post("/admin/movie/save")(save)
routes.post("/admin/movie/{id}/save")(save)
routes.post("/admin/movie/{id}/delete")(delete)
