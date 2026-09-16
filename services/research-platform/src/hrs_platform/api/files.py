"""HTTP download headers; source selection remains in application services."""

from urllib.parse import quote

from hrs_platform.services.storage import object_response


def download(objects, value):
    title, reference = value
    return object_response(
        objects,
        reference,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(title + ".md", safe=""),
        },
    )
