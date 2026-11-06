"""Exact quotation positions for reading and exporting historical cards."""


def quote_range(unit, selection):
    start = -1
    for _ in range(selection.get("occurrence", 0) + 1):
        start = unit["text"].find(selection["quote"], start + 1)
        if start < 0:
            raise ValueError("Quotation is absent from the reviewed source.")
    end = start + len(selection["quote"])
    return start, end


def quote_pages(unit, selection):
    start, end = quote_range(unit, selection)
    pages = sorted(
        {
            page
            for source in unit["sources"]
            if source["unit_start"] < end and source["unit_end"] > start
            for page in source["pages"]
        }
    )
    if not pages:
        raise ValueError("Quotation has no original page provenance.")
    return pages
