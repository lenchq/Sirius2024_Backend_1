import asyncio
from datetime import timedelta
import logging
import os
import re
from uuid import uuid4

import redis as redislib
import yt_dlp

import ytdl_helper

from py_linq import Enumerable
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import PicklePersistence
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

from download_worker import DownloadWorker

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# https://regex101.com/r/Plm2NN/1
link_regex = re.compile(
    r"^https?://(?:www\.)?(?P<domain>[-a-zA-Z0-9@:%._+~#=]{1,256})\.[a-zA-Z0-9()]{1,6}\b"
    r"(?:[-a-zA-Z0-9()@:%_+.~#?&/=]*)$",
    re.I)
allowed_services = ["vk",
                    "youtube",
                    "dzen"]
allowed_services_alias = ["youtu"]
redis = redislib.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=os.getenv("REDIS_PORT", 6379),
                       db=os.getenv("REDIS_DB", 0), protocol=3)
ytdl = ytdl_helper.YtdlHelper(redis)
download_worker: DownloadWorker


def services_str():
    return ', '.join(list(serv.capitalize() for serv in allowed_services))


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["id"] = update.effective_user.id
    context.chat_data["id"] = update.effective_chat.id
    await update.message.reply_text(
        f'Привет {update.effective_user.first_name}👋!\nЯ помогу тебе скачать видео с {services_str()}!'
        f'\nПросто вышли мне ссылку на видео и я помогу тебе его скачать')


# noinspection PyAsyncCall
async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["id"] = update.effective_user.id
    context.chat_data["id"] = update.effective_chat.id
    match = link_regex.fullmatch(update.message.text)
    if (match is None):
        await update.message.reply_text(
            f"Отправь мне ссылку на видео из этих сервисов: {services_str()}"
            f"\n\nИли напиши /help чтобы получить помощь!")
        return
    domain = match.groupdict()["domain"]
    if (domain in allowed_services or domain in allowed_services_alias):
        msg = await update.message.reply_text("🔎 Ищу видео...")
        vk_domain = domain == "vk"
        try:
            video = await ytdl.extract_info(update.message.text, domain)
        except Exception as e:
            logging.error("Error when parsing video data: ", exc_info=e)
            logging.info(e.__repr__())
            await msg.delete()
            if isinstance(e, yt_dlp.DownloadError):
                e.__class__ = yt_dlp.DownloadError
                if "vk" in e.msg:
                    await update.message.reply_text(f"😓Не удалось найти это видео\nПричина:{e.msg}")
                    return
            await update.message.reply_text("😓Не удалось найти это видео")
            return

        video_duration = str(timedelta(seconds=video["duration"]))

        keyboard = []

        result_video = Enumerable(video["formats"]) \
            .where(lambda x: (x["ext"] == "mp4" and "width" in x) or vk_domain) \
            .where(lambda x: x.get("height", 0) in [360, 480, 720, 1080]
                             or x.get("width", 0) in [360, 480, 720, 1080])

        # vk does not provides audio information
        if (not vk_domain):
            result_video = result_video.where(lambda x: x.get("audio_channels", None) is not None)

        result_video = result_video \
            .distinct(lambda x: x["resolution"])

        # result_audio_only = Enumerable(video["formats"]) \
        #     .where(lambda x: x["ext"] in ["webm", "m4a"]) \
        #     .where(lambda x: x["audio_channels"] is not None
        #                      and x["audio_ext"] != "none"
        #                      and x["video_ext"] == "none") \
        #     .order_by_descending(lambda x: x["abr"]) \
        #     .take(1)

        result_video = result_video \
            .order_by_descending(lambda x: x["height"])

        result = result_video.to_list()

        # if (len(result_audio_only) > 0):
        #     result.prepend(*result_audio_only)

        for fmt in result:
            btn_text = str(fmt["height"]) + "p" if fmt["video_ext"] != "none" else "Audio only"

            clb_data = str(uuid4())
            redis.set(clb_data, fmt["url"], ex=timedelta(hours=1))
            callback_data = clb_data
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.delete_message(chat_id=context.chat_data["id"], message_id=msg.id)
        await update.message.reply_photo(video["thumbnail"],
                                         caption=f'[{video["uploader"]} -- {video["title"]}]({video["original_url"]}) ({video_duration})',
                                         parse_mode="markdown",
                                         reply_markup=reply_markup)
    else:
        await update.message.reply_text("Not supported")


async def queryHandler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    await query.answer()

    download_worker.task_queue.put((query.data, query.message.id, context))
    await query.message.edit_caption(f"Загрузка в очереди")


async def main():
    if (not os.path.exists('./down/data')):
        os.mkdir('./down/data/')
    persistence = PicklePersistence(filepath='./down/data/persitencebot', update_interval=1)  # (1)
    app = ApplicationBuilder() \
        .token(os.getenv('BOT_TOKEN')) \
        .local_mode(local_mode=True) \
        .base_url(os.getenv("TELEGRAM_API_URL", "http://localhost:8081/bot")) \
        .persistence(persistence) \
        .build()
    app.add_handler(CommandHandler(['start', 'help'], start_command))
    app.add_handler(CallbackQueryHandler(queryHandler))
    app.add_handler(MessageHandler(filters.CHAT, download_command))

    await app.initialize()
    await app.updater.start_polling()
    await app.start()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    download_worker = DownloadWorker(loop, redis, ytdl, os.getenv("NUM_WORKERS", 1))
    loop.run_until_complete(main())
    loop.run_forever()
