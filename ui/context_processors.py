import os
from django.conf import settings
from django.templatetags.static import static


# Small UI images to prefetch site-wide.  Large question images (q11-q15,
# scatter-plot) and the login background are excluded because they are
# hundreds of KB each and only used on specific pages.
_PREFETCH_IMAGES = [
    'images/active-tick.png',
    'images/arrow.png',
    'images/battery.svg',
    'images/bluebook-black.svg',
    'images/bluebook-blue.svg',
    'images/bluebook-white-logo.svg',
    'images/calculator.svg',
    'images/checklist.svg',
    'images/confetti.png',
    'images/congrats-laptop.svg',
    'images/cross-out-abc-blue.svg',
    'images/cross-out-abc.svg',
    'images/current.png',
    'images/dashed-border.png',
    'images/exam-overview.svg',
    'images/for-review.png',
    'images/full-length-practice.svg',
    'images/graphing.png',
    'images/help.svg',
    'images/highlights.svg',
    'images/home.png',
    'images/loaded.png',
    'images/loading-exam.png',
    'images/lock-device.png',
    'images/logo-blue.svg',
    'images/logo-white.svg',
    'images/mark-hover.svg',
    'images/mark.svg',
    'images/marked.svg',
    'images/minimize.svg',
    'images/more.svg',
    'images/move-hover.png',
    'images/move.png',
    'images/password-erase.png',
    'images/password-eye-crossed.png',
    'images/password-eye.png',
    'images/profile.png',
    'images/question-border.png',
    'images/reference.svg',
    'images/review-instructions.svg',
    'images/scientific.png',
    'images/separator.svg',
    'images/test-device.png',
    'images/test-preview.svg',
    'images/testing-tools-video.png',
    'images/ticket-blue.svg',
    'images/ticket.svg',
    'images/timer.svg',
]

# Resolve once at module level (static URLs don't change at runtime).
_PREFETCH_URLS = None


def _get_prefetch_urls():
    global _PREFETCH_URLS
    if _PREFETCH_URLS is None:
        urls = []
        for path in _PREFETCH_IMAGES:
            try:
                urls.append(static(path))
            except Exception:
                pass
        _PREFETCH_URLS = urls
    return _PREFETCH_URLS


def static_files_preloader(request):
    """Provide prefetch URLs for all small UI images on every page."""
    return {'prefetch_static_urls': _get_prefetch_urls()}
