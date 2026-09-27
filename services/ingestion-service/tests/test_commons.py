"""Deciding whether we are allowed to publish somebody's photograph.

This is the part of the backdrop worth testing. Getting the carousel wrong
makes a page look odd; getting the licence wrong puts someone else's work on a
public site without the right to do it, and the fix for that arrives as a
takedown notice.

The rule is a whitelist and an attribution. An unrecognised licence is not a
permissive one, and no image is kept without the credit its licence requires.
"""

import pytest

from app.services.commons import (
    MIN_WIDTH,
    CommonsUnavailable,
    _text,
    is_permissive,
    looks_like_a_venue,
    usable_images,
)


def _page(title, licence, width=1920, artist="A Photographer", url=None):
    return {
        "title": title,
        "descriptionurl": "https://commons.wikimedia.org/wiki/" + title,
        "imageinfo": [{
            "thumburl": url or "https://upload.wikimedia.org/x.jpg",
            "thumbwidth": width,
            "extmetadata": {
                "LicenseShortName": {"value": licence},
                "Artist": {"value": artist},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
            },
        }],
    }


# ── The licence ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("licence", [
    "CC0", "CC BY 3.0", "CC BY-SA 4.0", "Public domain", "PD-self",
])
def test_licences_that_permit_reuse_are_accepted(licence):
    assert is_permissive(licence)


@pytest.mark.parametrize("licence", [
    "Fair use", "All rights reserved", "Non-free logo", "CC BY-NC 4.0",
    "", "Copyrighted free use provided that",
])
def test_anything_else_is_refused(licence):
    """A whitelist, not a blacklist. Commons hosts non-free logos and fair-use
    material, and a blacklist admits whatever nobody thought to exclude.

    CC BY-NC is the one that looks safe and is not: no commercial use.
    """
    assert not is_permissive(licence)


def test_an_unusable_licence_keeps_the_image_out():
    kept = usable_images({"1": _page("File:Monza grandstand.jpg", "Fair use")})

    assert kept == []


def test_every_kept_image_carries_its_attribution():
    """The credit is the price of the photograph, not a nicety."""
    kept = usable_images({"1": _page("File:Monza.jpg", "CC BY-SA 4.0")})

    assert kept[0]["credit"] == "A Photographer"
    assert kept[0]["licence"] == "CC BY-SA 4.0"
    assert kept[0]["licence_url"]
    assert kept[0]["source"].startswith("https://commons.wikimedia.org/")


def test_an_image_with_no_named_author_still_credits_somebody():
    kept = usable_images({"1": _page("File:Monza.jpg", "Public domain", artist="")})

    assert kept[0]["credit"] == "Wikimedia Commons"


# ── What is not a photograph of a place ──────────────────────────────────────


@pytest.mark.parametrize("title", [
    "File:Sepang International Circuit logo.jpg",
    "File:Monza track map.svg",
    "File:Baku circuit layout.png",
    "File:Silverstone flag.jpg",
])
def test_logos_and_diagrams_are_not_venues(title):
    """Commons returns a circuit's logo and its track diagram for the same
    search as its grandstands. Neither makes a backdrop."""
    assert not looks_like_a_venue(title)


def test_a_grandstand_is_a_venue():
    assert looks_like_a_venue("File:Sepang tribune.jpg")


def test_a_thumbnail_is_not_a_backdrop():
    """A backdrop spans the viewport; a small image stretched across it looks
    like a mistake."""
    kept = usable_images({
        "1": _page("File:Monza.jpg", "CC BY 4.0", width=MIN_WIDTH - 1),
    })

    assert kept == []


# ── The author field is markup ───────────────────────────────────────────────


def test_the_author_is_rendered_as_text_not_markup():
    """Commons stores Artist as an HTML fragment, usually a link to a user
    page. Passing it through would put someone else's markup in our page."""
    html = '<a href="//commons.wikimedia.org/wiki/User:angys">*angys*</a>'

    assert _text(html) == "*angys*"
    assert "<" not in _text(html)


def test_nothing_usable_is_an_empty_result_not_a_half_filled_one():
    assert usable_images({}) == []
