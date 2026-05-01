import os
from django.conf import settings
from django.templatetags.static import static

def static_files_preloader(request):
    """
    Returns a list of all static files to be preloaded.
    """
    static_dir = os.path.join(settings.BASE_DIR, 'ui', 'static')
    preload_urls = []
    
    if os.path.exists(static_dir):
        for root, dirs, files in os.walk(static_dir):
            for file in files:
                if file.endswith(('.png', '.jpg', '.jpeg', '.svg', '.css')):
                    # Get the relative path for static tag
                    rel_path = os.path.relpath(os.path.join(root, file), static_dir)
                    # Convert backslashes to forward slashes for URLs
                    rel_path = rel_path.replace('\\', '/')
                    try:
                        preload_urls.append(static(rel_path))
                    except Exception:
                        pass
                
    return {'preload_static_urls': preload_urls}
