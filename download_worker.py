import asyncio
import logging
import os
import pathlib
import queue
import threading
from asyncio import Future
from typing import Tuple
from datetime import datetime, timedelta

import redis.client
import telegram
import yt_dlp
from telegram.ext import ContextTypes, CallbackContext
from telegram import CallbackQuery

import ytdl_helper


class DownloadWorker:
    task_queue = queue.Queue()
    _redis: redis.StrictRedis
    _ytdl: ytdl_helper.YtdlHelper
    _bot_event_loop: asyncio.AbstractEventLoop

    def __init__(self, bot_event_loop: asyncio.AbstractEventLoop, redis: redis.StrictRedis,
                 ytdl: ytdl_helper.YtdlHelper, num_workers=1):
        self._redis = redis
        self._ytdl = ytdl
        self._bot_event_loop = bot_event_loop
        for _ in range(num_workers):
            threading.Thread(target=self.start_download_worker, daemon=True).start()

    async def worker(self):
        while True:
            tuple: Tuple[str, int, ContextTypes.DEFAULT_TYPE] = self.task_queue.get(block=True)
            data, msg_id, context = tuple
            try:
                await self.download_video(context, data, msg_id)
            except Exception as e:
                logging.error("Error while downloading video: ", exc_info=e)
                self._edit_message(context.bot, "Произошла ошибка во время загрузки видео.\nПопробуйте позже",
                                   message_id=msg_id, chat_id=context.chat_data["id"])
            self.task_queue.task_done()

    def _send_media(self, video_url: str, bot: telegram.Bot, chat_id: int, text: str | None = None,
                    reply_to_message_id: int | None = None):
        def func():
            return bot.send_video(video=video_url, chat_id=chat_id, caption=text,
                                  reply_to_message_id=reply_to_message_id)

        asyncio.ensure_future(func(), loop=self._bot_event_loop)

    def _edit_message(self, bot: telegram.Bot, text: str, message_id: int, chat_id: int):
        def func():
            return bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=text)

        asyncio.ensure_future(func(), loop=self._bot_event_loop)

    def _schedule_video_deletion(self, context: CallbackContext, video_id: str):
        async def delete_task():
            async def delete_video(_):
                os.remove(f"./down/{video_id}")

            context.job_queue.run_once(delete_video, timedelta(minutes=15))

        asyncio.ensure_future(delete_task(), loop=self._bot_event_loop)

    async def download_video(self, context: CallbackContext, video_id: str, message_id: int,
                             next_report: datetime | None = None):
        # query: CallbackQuery = context.user_data["callback_query"]
        # with yt_dlp.YoutubeDL({
        #     "format_id": ""
        # }) as ytdl:
        # info = ytdl.extract_info(video_url, download=False)

        # info = ytdl.extract_info(url, download=False)
        # u = info["formats"][0]["url"]
        # ytdl.download(url)

        chat_id = context.chat_data["id"]
        url = self._redis.get(video_id)
        url = url.decode('utf-8')

        def progress_hook(status: dict):
            # report every 5th second
            elapsed: float = status["elapsed"]
            try:
                # if int(status["elapsed"]) % 5 == 0:
                if int(elapsed) % 5 == 0:
                    self._edit_message(context.bot,
                                       f"Идет загрузка ({status['_percent_str'].strip()}) -- Осталось примерно {status['_eta_str']}",
                                       message_id, chat_id)
            except Exception as e:
                pass

        with yt_dlp.YoutubeDL({
            "outtmpl": pathlib.Path(f"./down/{video_id}").resolve().__str__(),
            "quiet": True,
        }) as yt:
            yt.add_progress_hook(progress_hook)
            yt.download(url)

        self._edit_message(context.bot,
                           f"Загрузка завершена",
                           message_id, chat_id)
        self._send_media(f"file:///var/py-bot/{video_id}", context.bot, chat_id=chat_id,
                         reply_to_message_id=message_id)

        self._schedule_video_deletion(context, video_id)

    def start_download_worker(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        loop.run_until_complete(self.worker())
        loop.close()
