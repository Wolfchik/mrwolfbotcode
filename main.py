# coding=UTF-8
import config
import discord
from discord.ext import commands
from dislash import SlashClient, slash_command, Option, OptionType, ResponseType, ActionRow, Button, ButtonStyle
from typing_extensions import Required
from PIL import Image, ImageFont, ImageDraw
import youtube_dl
import dislash
import asyncio
import functools
import itertools
import math
import random
import requests
import io
import json
import datetime
from asyncio import sleep
from async_timeout import timeout

premium_guilds = [939453973235134526, 943223794972127272, 893838318037499974]
# Silence useless bug reports messages
youtube_dl.utils.bug_reports_message = lambda: ''


class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass

# ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
    }

    FFMPEG_OPTIONS = {
        'before_options':
        '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)


    def __init__(self,
                 ctx: commands.Context,
                 source: discord.FFmpegPCMAudio,
                 *,
                 data: dict,
                 volume: float = 0.5):
        super().__init__(source, volume)

        self.requester = ctx.author
        self.channel = ctx.channel
        self.data = data

        self.uploader = data.get('uploader')
        self.uploader_url = data.get('uploader_url')
        date = data.get('upload_date')
        self.upload_date = date[6:8] + '.' + date[4:6] + '.' + date[0:4]
        self.title = data.get('title')
        self.thumbnail = data.get('thumbnail')
        self.description = data.get('description')
        self.duration = self.parse_duration(int(data.get('duration')))
        self.tags = data.get('tags')
        self.url = data.get('webpage_url')
        self.views = data.get('view_count')
        self.likes = data.get('like_count')
        self.dislikes = data.get('dislike_count')
        self.stream_url = data.get('url')

    def __str__(self):
        return '**{0.title}**'.format(self)

    @classmethod
    async def create_source(cls,
                            ctx: commands.Context,
                            search: str,
                            *,
                            loop: asyncio.BaseEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        partial = functools.partial(cls.ytdl.extract_info,
                                    search,
                                    download=False,
                                    process=False)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise YTDLError('Ошибка поиска: `{}`'.format(search))

        if 'entries' not in data:
            process_info = data
        else:
            process_info = None
            for entry in data['entries']:
                if entry:
                    process_info = entry
                    break

            if process_info is None:
                raise YTDLError('Ошибка поиска: `{}`'.format(search))

        webpage_url = process_info['webpage_url']
        partial = functools.partial(cls.ytdl.extract_info,
                                    webpage_url,
                                    download=False)
        processed_info = await loop.run_in_executor(None, partial)

        if processed_info is None:
            raise YTDLError('Ошибка: `{}`'.format(webpage_url))

        if 'entries' not in processed_info:
            info = processed_info
        else:
            info = None
            while info is None:
                try:
                    info = processed_info['entries'].pop(0)
                except IndexError:
                    raise YTDLError('Ошибка поиска: `{}`'.format(webpage_url))

        return cls(ctx,
                   discord.FFmpegPCMAudio(info['url'], **cls.FFMPEG_OPTIONS),
                   data=info)

    @staticmethod
    def parse_duration(duration: int):
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)

        duration = []
        if days > 0:
            duration.append('{} days'.format(days))
        if hours > 0:
            duration.append('{} hours'.format(hours))
        if minutes > 0:
            duration.append('{} minutes'.format(minutes))
        if seconds > 0:
            duration.append('{} seconds'.format(seconds))

        return ', '.join(duration)

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)


class Song:
    __slots__ = ('source', 'requester')

    def __init__(self, source: YTDLSource):
        self.source = source
        self.requester = source.requester

    def create_embed(self):
        embed = (discord.Embed(
            title='Сейчас играет',
            description='[**{0.source.title}**]({0.source.url})'.format(self),
            color=discord.Color.blurple()).set_thumbnail(
                url=self.source.thumbnail))
        embed.set_footer(text=f"Произошло это в {datetime.datetime.now()}")

        return embed

    def now_name(self):
            # title='Сейчас играет',
            # description='[**{0.source.title}**]({0.source.url})'.format(self),
            # color=discord.Color.blurple().set_thumbnail(
            #     url=self.source.thumbnail)
        now = self.source.url
        return now


class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(
                itertools.islice(self._queue, item.start, item.stop,
                                 item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self._queue)

    def remove(self, index: int):
        del self._queue[index]


class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = SongQueue()

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def loop(self):
        return self._loop

    @loop.setter
    def loop(self, value: bool):
        self._loop = value

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        return self.voice and self.current

    async def audio_player_task(self):
        while True:
            self.next.clear()

            if not self.loop:
                # Try to get the next song within 3 minutes.
                # If no song will be added to the queue in time,
                # the player will disconnect due to performance
                # reasons.
                try:
                    async with timeout(180):  # 3 minutes
                        self.current = await self.songs.get()
                except asyncio.TimeoutError:
                    self.bot.loop.create_task(self.stop())
                    return

            self.current.source.volume = self._volume
            self.voice.play(self.current.source, after=self.play_next_song)
            emojico = ["<:2625applemusic:944531620617142292>","<:2870music:944531617882468402>","<:3567pigstep:944531620206084118>","<:7469noteblock:944531621858643999>","<:8066youtubemusic:944531622085140500>","<:6834radio:944531618482253835>"]
            msg = await self.current.source.channel.send(
                embed=self.current.create_embed())

            await msg.add_reaction(random.choice(emojico))

            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None

    async def queue_clear(self):
        self.songs.clear()


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_states = {}
        self.now_names = {}

    def get_voice_state(self, ctx: commands.Context):
        state = self.voice_states.get(ctx.guild.id)
        if not state:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state

        return state

    def cog_unload(self):
        for state in self.voice_states.values():
            self.bot.loop.create_task(state.stop())

    def cog_check(self, ctx: commands.Context):
        if not ctx.guild:
            raise commands.NoPrivateMessage(
                'Эта команда не используется в ЛС (Личные сообщения)')


        return True

    async def cog_before_invoke(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        await ctx.send('Ошибка: {}'.format(str(error)))

    @commands.command(name='join', invoke_without_subcommand=True)
    async def _join(self, ctx: commands.Context):
        """Подключается к голосовому каналу."""
        if ctx.guild.id in premium_guilds:

            destination = ctx.author.voice.channel
            if ctx.voice_state.voice:
                await ctx.voice_state.voice.move_to(destination)
                return

            ctx.voice_state.voice = await destination.connect()

        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))


    @commands.command(name='leave', aliases=['disconnect'])
    async def _leave(self, ctx: commands.Context):
        """Очистить очередь и заставить бота уйти."""
        if ctx.guild.id in premium_guilds:

            if not ctx.voice_state.voice:
                return await ctx.send('Бот и так не подключен. Зачем его кикать?')

            await ctx.voice_state.stop()
            await ctx.send('Пока!👋')
            del self.voice_states[ctx.guild.id]

        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @commands.command(name='volume')
    async def _volume(self, ctx: commands.Context, *, volume = None):
        """Изменить громкость. Возможные значения(0-200)"""

        if ctx.guild.id in premium_guilds:
            if not volume:
                return await ctx.reply('Использование команды:\n`mwb!volume <Значение от 5-200>`')

            try:
            	volume = int(volume)

            except ValueError:
            	return await ctx.reply(f'Эта команда вызвала исключение/ошибку: {volume} - Это не число!')

            if not ctx.voice_state.is_playing:
                return await ctx.send('Сейчас музыка не играет. Можете включить.')

            if 0 > volume > 100:
                return await ctx.send('Volume must be between 0 and 100')

            ctx.voice_state.volume = volume / 100
            await ctx.send('Громкость изменена на {}%'.format(volume))

        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @commands.command(name='np', aliases=['now playing', 'playing'])
    async def _now(self, ctx: commands.Context):
        """Увидеть, какая песня играет прямо сейчас"""
        if ctx.guild.id in premium_guilds:

            if not ctx.voice_state.is_playing:
                return await ctx.reply('Сейчас музыка просто не играет. Можешь включить.')

            await ctx.send(embed=ctx.voice_state.current.create_embed())
        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @commands.command(name='skip', aliases=["next"])
    async def _skip(self, ctx: commands.Context):
        """Проголосуйте за то, чтобы пропустить песню. Запрашивающий может автоматически пропустить.
 Для пропуска песни необходимо 3 пропущенных голоса.Если вы админ то песня скипнетса сразу же.
        """
        if ctx.guild.id in premium_guilds:

            if not ctx.voice_state.is_playing:
                return await ctx.send('Сейчас музыка не играет,зачем её пропускать? Можете включить.')

            if not ctx.voice_state.voice:
                await ctx.invoke(self._join)

            if (ctx.voice_state.current.requester):
                await ctx.message.add_reaction('⏭')
                ctx.voice_state.skip()

        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @commands.command(name='queue')
    async def _queue(self, ctx: commands.Context, *, page: int = 1):
        """Показывает очередь песен.
 Вы можете дополнительно указать страницу для отображения. Каждая страница содержит 10 элементов.
        """
        if ctx.guild.id in premium_guilds:
            if len(ctx.voice_state.songs) == 0:
                return await ctx.send('В очереди нет треков. Можете добавить.')

            items_per_page = 10
            pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)

            start = (page - 1) * items_per_page
            end = start + items_per_page

            queue = ''
            for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
                queue += '`{0}.` [**{1.source.title}**]({1.source.url})\n'.format(i + 1, song)

            embed = (discord.Embed(description='**{} tracks:**\n\n{}'.format(len(ctx.voice_state.songs), queue))
                     .set_footer(text='Viewing page {}/{}'.format(page, pages)))
            await ctx.send(embed=embed)
        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @commands.command(name='shuffle')
    async def _shuffle(self, ctx: commands.Context):
        """Перетасовывает очередь."""
        if ctx.guild.id in premium_guilds:

            if len(ctx.voice_state.songs) == 0:
                return await ctx.send('В очереди нет треков. Можете добавить.')

            ctx.voice_state.songs.shuffle()
            await ctx.message.add_reaction('✅')
        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @commands.command(name='remove')
    async def _remove(self, ctx: commands.Context, index = None):
        """Удалить песни из очереди по номеру.Использование:.remove <Какая песня по очереди>"""
        if ctx.guild.id in premium_guilds:

            try:
            	index = int(index)

            except ValueError:
            	return await ctx.reply(f'Эта команда вызвала исключение/ошибку: {index} - Это не индекс!')

            if not index:
                return await ctx.reply('Использование команды:\n`mwb!remove <Индекс>`')

            if len(ctx.voice_state.songs) == 0:
                return await ctx.send('В очереди нет треков. Можете добавить.')

            ctx.voice_state.songs.remove(index - 1)
            await ctx.message.add_reaction('✅')

        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @commands.command(name='play', aliases=['p','add'])
    async def _play(self, ctx: commands.Context, *, search: str = None):
            if ctx.guild.id in premium_guilds:

                if not search:
                    return await ctx.reply('Использование команды:\n`mwb!play <Ссылка, или название>`')

                if not ctx.voice_state.voice:
                    await ctx.invoke(self._join)

                msg = await ctx.reply(f'<a:ee98:921363226061598780> **{self.bot.user.name}** думает...')
                try:
                    source = await YTDLSource.create_source(ctx,
                                                            search,
                                                            loop=self.bot.loop)
                except YTDLError as e:
                    await ctx.send('Ошибка: {}'.format(str(e)))
                else:
                    song = Song(source)

                    await ctx.voice_state.songs.put(song)
                    await msg.edit(content=f'Добавлено {source}')

            else:
                await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @commands.command(name="re", aliases=["replay"])
    async def _re(self, ctx: commands.Context):
        if ctx.guild.id in premium_guilds:
            if not ctx.voice_state.is_playing:
                return await ctx.reply('Сейчас музыка не играет,зачем её реплеить? Можете включить.')

            msg = await ctx.reply(f'<a:ee98:921363226061598780> **{self.bot.user.name}** думает...')
            try:
                source2 = await YTDLSource.create_source(ctx,
                                                        ctx.voice_state.current.now_name(),
                                                        loop=self.bot.loop)
            except YTDLError as e:
                await ctx.send('Ошибка: {}'.format(str(e)))
            else:
                song2 = Song(source2)

                await ctx.voice_state.songs.put(song2)
                ctx.voice_state.skip()
                await msg.edit(content=f'<:succes_title:925401308813471845>')
        else:
            await ctx.reply(embed=discord.Embed(title="Извините, произошла ошибка!",description="Эта команда доступна только PREMIUM гильдиям или пользователям\nОбратитесь на [**сервер поддержки**](https://discord.gg/4MBEyFBj) чтобы получить это!"))

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_voice_state(self, ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('Может ты сначала подключишься к голосовому каналу?')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('Ты хочешь чтобы в голосовом канале было два одинаковых ботов?')

#embed.set_footer(text=f"Запросил пользователь {ctx.author.username}#{ctx.author.discriminator}")
class Main(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help")
    async def _help(self, ctx: commands.Context):
        row = ActionRow(
            Button(
                style=ButtonStyle.green,
                label="Модерация",
                custom_id="mod"
            ),
            Button(
                style=ButtonStyle.green,
                label="Музыка",
                custom_id="mus"
            ),
            Button(
                style=ButtonStyle.green,
                label="Утилиты",
                custom_id="uti"
            )
        )
        embedstart = discord.Embed(title="Список команд!",description="Жмякайте кнопочки, пока не пройдёт 60 сек")
        embeduti = discord.Embed(title="Список команд утилит!",description="`mwb!quote` - Бот выдаст рандомную цитату\n`mwb!card` - Увидеть свою карточку\n`mwb!logo` - Игра логотип!")
        embedmod = discord.Embed(title="Список команд модераций!",description="`mwb!clear <amount>` - Очистить сообщения, используя указ. лимит\n`mwb!mute <member> <reason>` - Замьютить участника\n`mwb!unmute <member>` - Размьютить участника")
        embedmus = discord.Embed(title="Список команд музыки!", description="`mwb!play <search>` - Начать воспроизведение\n`mwb!skip` - Пропустить\n`mwb!queue` - Посмотреть очередь\n`mwb!leave` - Бот выйдет из канала и удалит voice state вашего сервера\n`mwb!join` - Зайти к вам в канал\n`mwb!re` - Начать играющее сейчас воспроизведение заново\n`mwb!remove <index>` - Удалить песню по её индексу\n`mwb!np` - Что сейчас играет?\n`mwb!volume <value>` - Изменить громкость, после успеха введите mwb!re")
        embedtime = discord.Embed(title="Время(60 секунд) вышло!",description="Для повторного использования, пожалуйста снова введите `mwb!help`!")
        #embed=discord.Embed(title="Список моих команд ", description="Всё разложено по полочкам :)\n**🎵Музыка**\n`mwb!play <search>` - Начать воспроизведение\n`mwb!skip` - Пропустить\n`mwb!queue` - Позырить очередь\n`mwb!leave` - Остановить музыку, и выйти\n`mwb!join` - Зайти к вам в канал\n`mwb!re` - Начать воспроизведение заново\n`mwb!remove <index>` - Удалить опред песню\n`mwb!np` - Посмотреть, что сейчас играет\n`mwb!volume <value>` - Изменить громкость, после измены не забудьте включить воспроизведение заново\n**🥇Модерация**\n`mwb!clear <amount>` - Очистить сообщения\n`mwb!mute` - Замьютить кого то\n`mwb!unmute` - Размьютить кого то\n**📍 Утилиты**\n`mwb!quote` - Бот выдаст рандомную цитату\n`mwb!card` - Увидеть свою карточку")
        msg = await ctx.reply(embed=embedstart, components=[row])
        on_click = msg.create_click_listener(timeout=60)

        @on_click.not_from_user(ctx.author, cancel_others=True, reset_timeout=True)
        async def on_wrong_user(inter):
            # This function is called in case a button was clicked not by the author
            # cancel_others=True prevents all on_click-functions under this function from working
            # regardless of their checks
            # reset_timeout=False makes the timer keep going after this function is called
            await inter.reply("Вы должны быть автором для использования кнопок.\nВведите mwb!help и заспавньте свой собств. хелп!", ephemeral=True)

        @on_click.matching_id("mod")
        async def on_test_button(inter):
            # This function only works if the author presses the button
            # Becase otherwise the previous decorator cancels this one
            await inter.reply(embed=embedmod, ephemeral=True)

        @on_click.matching_id("mus")
        async def on_test_button(inter):
            # This function only works if the author presses the button
            # Becase otherwise the previous decorator cancels this one
            await inter.reply(embed=embedmus, ephemeral=True)

        @on_click.matching_id("uti")
        async def on_test_button(inter):
            # This function only works if the author presses the button
            # Becase otherwise the previous decorator cancels this one
            await inter.reply(embed=embeduti, ephemeral=True)

        @on_click.timeout
        async def on_timeout():
            await msg.edit(embed=embedtime, components=[])

class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="clear")
    @commands.bot_has_permissions(manage_messages=True)
    @commands.has_permissions(manage_messages=True)
    async def _clear(self, ctx: commands.Context, amount=None):
        if not amount:
            await ctx.reply("Использование команды:\n`mwb!clear <кол-во>`")
        else:
            await ctx.channel.purge(limit=int(amount))
            await ctx.send("✅ Успешно")

    @commands.command(name="mute")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def _mute(self, ctx: commands.Context, member: discord.Member = None, reason="Не указана."):
        if member == None:
            await ctx.reply('Использование команды:\n`mwb!mute <@member>`')
            return
        guild = ctx.guild
        mutedRole = discord.utils.get(guild.roles, name="MrWolfBot Mute")

        if not mutedRole:
            mutedRole = await guild.create_role(name="MrWolfBot Mute")

            for channel in guild.channels:
                await channel.set_permissions(mutedRole,
                                            speak=False,
                                            send_messages=False,
                                            read_message_history=True,
                                            read_messages=True)
        embed = discord.Embed(
            title=f"Успешно!",
            description=f"{member.mention} теперь находиться в мьюте! ")
        embed.add_field(name="Причина", value=reason)
        await member.add_roles(mutedRole)
        await ctx.send(embed=embed)
        await member.send(f"Вы получили мьют на сервере **{guild.name}** по причине **{reason}**\n Press <:f3472ce706ad9f4bed0da39a7f21b55a:939828686767681627> тебе!")


    @commands.command(name="unmute")
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def _unmute(self, ctx, member: discord.Member = None):
        if member == None:
            await ctx.reply('Использование команды:\n`mwb!unmute <@Member>`')
            return
        guild = ctx.guild
        mutedRole = discord.utils.get(guild.roles, name="MrWolfBot Mute")
        embed = discord.Embed(
            description=f'С участника {member.mention} успешно сняты ограничения!')

        await member.remove_roles(mutedRole)
        await ctx.reply(embed=embed)
        await member.send(f"Хочешь крутую новость?\nТебя размьютили на сервере {guild.name}! <:b654094678543279e1ff53713c1d65e7:939828694577471538>")

class Utilits(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="quote")
    async def _quote(self, ctx: commands.Context):
        quotes = ["Все мы гении. Но если вы будете судить рыбу по её способности взбираться на дерево, она проживёт всю жизнь, считая себя дурой", "Нужно иметь что-то общее, чтобы понимать друг друга, и чем-то отличаться, чтобы любить друг друга.", "Несчастным или счастливым человека делают только его мысли, а не внешние обстоятельства. Управляя своими мыслями, он управляет своим счастьем.", "Лучше молчать и показаться дураком, чем заговорить и развеять все сомнения.", "Если тебе плюют в спину, значит ты впереди.", "Уметь выносить одиночество и получать от него удовольствие — великий дар.", "Жизнь как бумеранг..кинешь,не вернется...", "Уважай себя настолько, чтобы не отдавать всех сил души и сердца тому, кому они не нужны...", "Настоящий друг с тобой, когда ты не прав. Когда ты прав, всякий будет с тобой.", "Проблема этого мира в том, что глупцы и фанатики слишком уверены в себе, а умные люди полны сомнений.", "В шахматах это называется «цугцванг», когда оказывается, что самый полезный ход — никуда не двигаться.", "Лучше быть одной, чем несчастной с кем-то.", "Окружающим легко сказать: «Не принимай близко к сердцу». Откуда им знать, какова глубина твоего сердца? И где для него — близко?", "— А где я могу найти кого-нибудь нормального?\n— Нигде, — ответил Кот, — нормальных не бывает. Ведь все такие разные и непохожие. И это, по-моему, нормально.", "Мы не хозяева собственной жизни. Мы связаны с другими прошлым и настоящим. И каждый проступок, как и каждое доброе дело, рождают новое будущее.", "Я не боюсь исчезнуть. Прежде, чем я родился, меня не было миллиарды и миллиарды лет, и я нисколько от этого не страдал.", "Когда что-то понимаешь, то жить становится легче. А когда что-то почувствуешь — то тяжелее. Но почему-то всегда хочется почувствовать, а не понять!", "Когда у тебя ничего нет, нечего и терять.", "Хочешь, чтоб люди сочли тебя психом — скажи правду.", "Когда ты одинок — это не значит, что ты слабый. Это значит, ты достаточно сильный, чтобы ждать то, что ты заслуживаешь.", "Изначально тебя все бросают, если ты из-за этого захочешь повеситься, то ты всем покажешь что ты слабый. А если продолжишь это терпеть, то ты всем покажешь что ты сильный, и все захотят подружиться с тобой..."]
        embed=discord.Embed(title="Цитаты, от бота...", description=f" {random.choice(quotes)} ")
        await ctx.reply(embed=embed)

    @commands.command(name="card")
    async def _card(self, ctx: commands.Context):
        t = ctx.message.author.status
        if t == discord.Status.online:
            d = "🟢 В сети"

        t = ctx.message.author.status
        if t == discord.Status.offline:
            d = "⚪ Не в сети"

        t = ctx.message.author.status
        if t == discord.Status.idle:
            d = "🟠 Не активен"

        t = ctx.message.author.status
        if t == discord.Status.dnd:
            d = "🔴 Не беспокоить"
        async with ctx.typing():
            img = Image.new('RGBA', (300, 150), '#232529')
            url = str(ctx.author.avatar_url)[:-10]
            r = requests.get(url, stream = True)
            r = Image.open(io.BytesIO(r.content))
            r = r.convert('RGBA')
            r = r.resize((100, 100), Image.ANTIALIAS)
            img.paste(r, (15, 15, 115, 115))
            idraw = ImageDraw.Draw(img)
            name = ctx.author.name
            headline = ImageFont.truetype('arial.ttf', size = 20)
            undertext = ImageFont.truetype('arial.ttf', size = 12)
            idraw.text((145, 15), f'{name}', font=headline)
            idraw.text((145, 50), f'#{ctx.author.discriminator}', font=undertext)
            idraw.text((145, 70), f'ID: {ctx.author.id}', font = undertext)
            idraw.text((145, 90), f'Статус: {d}', font = undertext)
            idraw.text((220, 135), f'MrWolfBot', font=undertext)
            img.save('user_card.png')
            await ctx.reply(file = discord.File(fp = 'user_card.png'))

    @commands.command(name="send")
    async def _send(self, ctx: commands.Context, *, say):
        await ctx.reply("Окей <:b654094678543279e1ff53713c1d65e7:939828694577471538>")
        await ctx.send(say)

    @commands.command(name="queston")
    async def _queston(self, ctx: commands.Context, queston=None):
        if not queston:
            await ctx.add_reaction("<:a77baa2e6b8f48cb49fce1b1a90d779a:939828696129355826>")
            return await ctx.reply("Использование команды:\n`mwb!queston <Вопрос боту>`")
        else:
            replies = ["Да","Нет","Скорее всего","Я не знаю...","Частично","50 на 50","Где то на 20%","Где то на 10%"]
            await ctx.reply(f"{random.choice(replies)}")

    @commands.command(name="riddle")
    async def _riddle(self, ctx: commands.Context):
        select = random.randint(1, 4)
        wait_status = True

        if select == 1:
            embed = discord.Embed(title="Загадка... С подвохом 🤫",description="Вперёд, назад\nТебе и мне приятно...")
            embed.set_footer(text=f"Это нормальная вещь, не будь пошлым..")
            await ctx.reply(embed=embed)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "качели":
                    wait_status = False
                    await msg.reply(f"{msg.author.mention} не был пошлятиной, берите пример :last_quarter_moon_with_face:")
        if select == 2:
            embed = discord.Embed(title="Загадка... С подвохом 🤫",description="Засовываешь в рот...\nНачинаешь двигать вперёд и назад...")
            embed.set_footer(text=f"Это нормальная вещь, не будь пошлым..")
            await ctx.reply(embed=embed)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "зубная щётка":
                    wait_status = False
                    await msg.reply(f"{msg.author.mention} не был пошлятиной, берите пример :last_quarter_moon_with_face:")
        if select == 3:
            embed = discord.Embed(title="Загадка... С подвохом 🤫",description="Сначала оно маленькое...\nКогда мы растём, оно увеличивается...")
            embed.set_footer(text=f"Это нормальная вещь, не будь пошлым..")
            await ctx.reply(embed=embed)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "рост":
                    wait_status = False
                    await msg.reply(f"{msg.author.mention} не был пошлятиной, берите пример :last_quarter_moon_with_face:")

        if select == 4:
            embed = discord.Embed(title="Загадка... С подвохом 🤫",description="Оно не влезет в дырку, если не встанет.Оно не встанет если не оближешь")
            embed.set_footer(text=f"Это нормальная вещь, не будь пошлым..")
            await ctx.reply(embed=embed)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "нитка и иголка":
                    wait_status = False
                    await msg.reply(f"{msg.author.mention} не был пошлятиной, берите пример :last_quarter_moon_with_face:")

    @commands.command()
    async def logo(self, ctx: commands.Context):
        value = random.randint(1, 13)
        wait_status = True

        if value == 1:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://pngicon.ru/file/uploads/youtube-1.png")
            await ctx.reply(embed = emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "youtube":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 2:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://free-png.ru/wp-content/uploads/2020/07/tik_tok_logo.png")
            await ctx.reply(embed = emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "tiktok":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 3:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://xn--80aed5aobb1a.xn--p1ai/wp-content/uploads/audi-emblem-2016-black-1920x1080.png")
            await ctx.reply(embed = emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "audi":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 4:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://papik.pro/uploads/posts/2021-11/1636090353_1-papik-pro-p-tesla-logotip-foto-1.jpg")
            await ctx.reply(embed = emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "tesla":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 5:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/Facebook_Logo_%282019%29.png/800px-Facebook_Logo_%282019%29.png")
            await ctx.reply(embed = emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "facebook":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 6:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://media.lpgenerator.ru/uploads/2019/07/11/1_thumb600x460.jpg")
            await ctx.reply(embed = emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "google":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 7:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://w7.pngwing.com/pngs/254/992/png-transparent-apple-worldwide-developers-conference-logo-apple-iphone-7-plus-business-apple-logo-computer-wallpaper-monochrome.png")
            await ctx.reply(embed = emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "iphone":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 8:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://w7.pngwing.com/pngs/979/359/png-transparent-bugatti-veyron-car-bugatti-eb-110-logo-bugatti-angle-text-trademark.png")
            await ctx.reply(embed = emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "buggatie":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 9:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://kimuracars.com/ifiles/articles/046/nissan-2.jpg")
            await ctx.reply(embed=emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "nissan":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 10:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://cdn1.ozone.ru/s3/multimedia-d/c1200/6067294705.jpg")
            await ctx.reply(embed=emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "porsche":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 11:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://papik.pro/uploads/posts/2021-11/thumbs/1636185955_4-papik-pro-p-logotip-diskorda-foto-4.jpg")
            await ctx.reply(embed=emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "discord":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 12:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://cdn.icon-icons.com/icons2/2699/PNG/512/minecraft_logo_icon_168974.png")
            await ctx.reply(embed=emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "minecraft":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

        if value == 13:
            emb = discord.Embed(title="Какой это логотип?",description="Пишите оригиинал на английском")
            emb.set_image(url="https://upload.wikimedia.org/wikipedia/ru/4/41/Geometry_Dash_logo.webp")
            await ctx.reply(embed=emb)
            while wait_status:
                msg = await bot.wait_for("message")
                if msg.content.lower() == "geometrydash":
                    wait_status = False
                    await ctx.reply(f"{msg.author} сказал правильный ответ!")

bot = commands.Bot(command_prefix=config.get_prefix(), intents=discord.Intents.all())
slash = SlashClient(bot)
bot.remove_command('help')
bot.add_cog(Main(bot))
bot.add_cog(Music(bot))
bot.add_cog(Utilits(bot))
bot.add_cog(Moderation(bot))

def bot_guild_count():
    return bot.guilds()

def bot_name():
    return bot.user.name

def bot_prefix():
    return config.get_prefix()

@bot.event
async def on_ready():
    print(f"{bot.user.name}: Ready")
    while True:
        await bot.change_presence(status=discord.Status.idle, activity=discord.Activity(type = discord.ActivityType.watching, name=f'на тебя <3 [{len(bot.guilds)}]'))
        await sleep(60)

@bot.event
async def on_command_error(ctx, error):
    emoji = discord.utils.get(bot.emojis, name='symbol_error')
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply(
            embed=discord.Embed(title=f'{str(emoji)} Ошибка',
                                description=f'Извини, но у тебя нет прав!',
                                colour=discord.Color.red()))
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.reply(
            embed=discord.Embed(title=f'{str(emoji)} Ошибка',
                                description=f'Извини, но у меня нет прав!',
                                colour=discord.Color.red()))

@bot.event
async def on_message(message):
    if message.guild.id == 939453973235134526: #MrWolfBot New Community
        content = message.content.lower().split()
        if "блять" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "сука" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "даун" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "долбоёб" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "похуй" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "чмо" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "говно" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "гавно" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "пизды" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        if "пизда" in content:
            await message.delete()
            await message.channel.send("Был удалён мат.")
        else:
            await bot.process_commands(message)  # This line makes your other commands work.
    else:
        await bot.process_commands(message)  # This line makes your other commands work.

bot.run(config.get_code_run())
