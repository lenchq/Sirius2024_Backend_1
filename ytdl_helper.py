import datetime
from typing import Awaitable, Any

import redis as redislib
import yt_dlp
import json

from py_linq import Enumerable


# noinspection PyAsyncCall,PyTypeChecker
class YtdlHelper:
    _redis: redislib.StrictRedis

    def __init__(self, redis: redislib.Redis):
        self._redis = redis

    async def extract_info(self, url: str, service: str | None = None, force: bool = False) -> dict:
        cached = self._redis.get(f"info:{url}")
        if cached is not None and not force:
            return json.loads(cached)

        format = None
        if (service is None and service != "vk"):
            format = "[vcodec!=none][acodec!=none]"
        ytdl = yt_dlp.YoutubeDL({
            "format": format
        })
        info: dict = ytdl.extract_info(url, download=False)

        # dont need
        if ("thumbnails" in info):
            del info["thumbnails"]
        if ("automatic_captions" in info):
            del info["automatic_captions"]
        if ("chapters" in info):
            del info["chapters"]

        info["formats"] = Enumerable(info["formats"]) \
            .where(lambda x: x.get("protocol", "https") in ["https", "m3u"]) \
            .to_list()

        self._redis.set(f"info:{url}", json.dumps(ytdl.sanitize_info(info)), ex=datetime.timedelta(hours=1))
        return info
