from aiohttp import web

routes = web.RouteTableDef()

@routes.get("/health", allow_head=True)
async def health_route(request):
    return web.json_response({"status": "ok", "service": "Elsa Movie Portal"})

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    from .movie_portal import portal_home
    return await portal_home(request)

# Register the complete movie portal/admin routes.
from .movie_portal import (
    movie_detail, admin_login, admin_logout, admin_page, admin_movie,
    admin_add, admin_edit_save, admin_delete,
)

routes.get("/movie/{id}")(movie_detail)
routes.get("/admin/login")(admin_login)
routes.post("/admin/login")(admin_login)
routes.get("/admin/logout")(admin_logout)
routes.get("/admin")(admin_page)
routes.get("/admin/movie/{id}")(admin_movie)
routes.post("/admin/movie/save")(admin_add)
routes.post("/admin/movie/{id}/save")(admin_edit_save)
routes.post("/admin/movie/{id}/delete")(admin_delete)
