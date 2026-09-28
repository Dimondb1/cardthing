from .service import for_request


def site_content(request):
    return {"site_content": for_request(request)}
