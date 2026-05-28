import os
from django.conf import settings
from django.templatetags.static import static


def static_files_preloader(request):
    """
    Preload only the static files needed for the NEXT page in the user flow.
    Previously this walked ALL static files and preloaded them on every page,
    causing ~2 MB of unnecessary fetches (including images never used on that page).
    """
    from django.urls import resolve
    try:
        current_url_name = resolve(request.path_info).url_name
    except Exception:
        return {'preload_static_urls': []}

    # Only preload test-interface assets when user is on the start_code page
    # (the page immediately before the test interface)
    if current_url_name == 'start_code':
        test_images = [
            'images/timer.svg',
            'images/battery.svg',
            'images/highlights.svg',
            'images/calculator.svg',
            'images/reference.svg',
            'images/more.svg',
            'images/mark.svg',
            'images/cross-out-abc.svg',
            'images/arrow.png',
            'images/dashed-border.png',
            'images/question-border.png',
            'images/current.png',
            'images/for-review.png',
        ]
        preload_urls = []
        for f in test_images:
            try:
                preload_urls.append(static(f))
            except Exception:
                pass
        return {'preload_static_urls': preload_urls}

    return {'preload_static_urls': []}
