"""慧鉴后端应用包初始化。"""

from .image_formats import register_heif_opener


register_heif_opener()
