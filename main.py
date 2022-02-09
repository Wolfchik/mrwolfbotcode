import config
import discord
from discord.ext import commands
from dislash import SlashClient, slash_command, Option, OptionType, ResponseType
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
from asyncio import sleep
from async_timeout import timeout
from datebase.datebase import datebase as dt

base = dt()
base.start()


# Silence useless bug reports messages
youtube_dl.utils.bug_reports_message = lambda: ''


class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass


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

        return embed

    def now_name(self):
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
            await self.current.source.channel.send(
                embed=self.current.create_embed())

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

        destination = ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='summon')
    async def _summon(self, ctx: commands.Context, *, channel: discord.VoiceChannel = None):
        """Вызывает бота на голосовой канал.
 Если канал не был указан, он присоединяется к вашему каналу.
        """

        if not channel and not ctx.author.voice:
            raise VoiceError('Может ты сначала подключишься к голосовому каналу?')

        destination = channel or ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name='leave', aliases=['disconnect'])
    async def _leave(self, ctx: commands.Context):
        """Очистить очередь и заставить бота уйти."""

        if not ctx.voice_state.voice:
            return await ctx.send('Бот и так не подключен. Зачем его кикать?')

        await ctx.voice_state.stop()
        await ctx.send('Пока!👋')
        del self.voice_states[ctx.guild.id]

    @commands.command(name='volume')
    async def _volume(self, ctx: commands.Context, *, volume: int = None):
        """Изменить громкость. Возможные значения(0-200)"""

        if not volume:
            return await ctx.reply('Использование команды:\n`mwb!volume <Значение от 5-200>`')

        if not ctx.voice_state.is_playing:
            return await ctx.send('Сейчас музыка не играет. Можете включить.')

        if 0 > volume > 100:
            return await ctx.send('Volume must be between 0 and 100')

        ctx.voice_state.volume = volume / 100
        await ctx.send('Громкость изменена на {}%'.format(volume))

    @commands.command(name='np', aliases=['now playing', 'playing'])
    async def _now(self, ctx: commands.Context):
        """Увидеть, какая песня играет прямо сейчас"""
        if not ctx.voice_state.is_playing:
            return await ctx.reply('Сейчас музыка просто не играет. Можешь включить.')

        await ctx.send(embed=ctx.voice_state.current.create_embed())

    @commands.command(name='skip', aliases=["next"])
    async def _skip(self, ctx: commands.Context):
        """Проголосуйте за то, чтобы пропустить песню. Запрашивающий может автоматически пропустить.
 Для пропуска песни необходимо 3 пропущенных голоса.Если вы админ то песня скипнетса сразу же.
        """

        if not ctx.voice_state.is_playing:
            return await ctx.send('Сейчас музыка не играет,зачем её пропускать? Можете включить.')

        if not ctx.voice_state.voice:
            await ctx.invoke(self._join)

        if (ctx.voice_state.current.requester):
            await ctx.message.add_reaction('⏭')
            ctx.voice_state.skip()

    @commands.command(name='queue')
    async def _queue(self, ctx: commands.Context, *, page: int = 1):
        """Показывает очередь песен.
 Вы можете дополнительно указать страницу для отображения. Каждая страница содержит 10 элементов.
        """

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

    @commands.command(name='shuffle')
    async def _shuffle(self, ctx: commands.Context):
        """Перетасовывает очередь."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('В очереди нет треков. Можете добавить.')

        ctx.voice_state.songs.shuffle()
        await ctx.message.add_reaction('✅')

    @commands.command(name='remove')
    async def _remove(self, ctx: commands.Context, index: int = None):
        """Удалить песни из очереди по номеру.Использование:.remove <Какая песня по очереди>"""

        if not index:
            return await ctx.reply('Использование команды:\n`mwb!remove <Индекс>`')

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('В очереди нет треков. Можете добавить.')

        ctx.voice_state.songs.remove(index - 1)
        await ctx.message.add_reaction('✅')

    @commands.command(name='loop')
    async def _loop(self, ctx: commands.Context):
        """Зацикливает воспроизводимую в данный момент песню.
 Вызовите эту команду еще раз, чтобы отключить песню."""

        if not ctx.voice_state.is_playing:
            return await ctx.send('Ничего не играет в данный момент.')

        # Inverse boolean value to loop and unloop.
        ctx.voice_state.loop = not ctx.voice_state.loop
        await ctx.message.add_reaction('✅')

    @commands.command(name='play', aliases=['p','add'])
    async def _play(self, ctx: commands.Context, *, search: str = None):

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
                # row_of_buttons = ActionRow(
                #     Button(style=ButtonStyle.red, label="Replay", custom_id="re"))
                await msg.edit(content=f'Добавлено {source}')
                # while True:
                #     await bot.wait_for('button_click')

                #     try:
                #         source2 = await YTDLSource.create_source(ctx,
                #                                                 self.current.now_name(),
                #                                                 loop=self.bot.loop)
                #     except YTDLError as e:
                #         await ctx.send('Ошибка: {}'.format(str(e)))
                #     else:
                #         song2 = Song(source2)

                #         await ctx.voice_state.songs.put(song2)
                #         ctx.voice_state.skip()

    @commands.command(name="re", aliases=["replay"])
    async def _re(self, ctx: commands.Context):
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

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_voice_state(self, ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('Может ты сначала подключишься к голосовому каналу?')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('Ты хочешь чтобы в голосовом канале было два одинаковых ботов?')

    @slash_command(name="join",description="Зайти к тебе в канал")
    async def _joininslash(self, ctx: commands.Context):
        await ctx.reply(..., type=5)
        ctx.voice_state = self.get_voice_state(ctx)

        destination = ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @slash_command(name="play",description="Начать воспроизведение.",options=[Option("search", "А что искать то?", required=True)])
    async def _playinslash(self, ctx: commands.Context, *, search: str = None):
            await ctx.reply(..., type=5)
            ctx.voice_state = self.get_voice_state(ctx)

            if not ctx.voice_state.voice:
                return await ctx.reply("Сперва, пожалуйста напиши /join")

            try:
                source = await YTDLSource.create_source(ctx,
                                                        search,
                                                        loop=self.bot.loop)
            except YTDLError as e:
                await ctx.send('Ошибка: {}'.format(str(e)))
            else:
                song = Song(source)

                await ctx.voice_state.songs.put(song)
                # row_of_buttons = ActionRow(
                #     Button(style=ButtonStyle.red, label="Replay", custom_id="re"))
                await ctx.reply(f'Добавлено {source}')

    @slash_command(name="replay",description="Начать сейчас играющее воспроизведение заново")
    async def _reinslash(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)
        await ctx.reply(..., type=5)
        
        if not ctx.voice_state.is_playing:
            return await ctx.reply('Сейчас музыка не играет,зачем её реплеить? Можете включить.')

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
            await ctx.reply(f'<:succes_title:925401308813471845>')

    @slash_command(name="remove", description="Удалить песню по индексу.",options=[Option("index", "Укажите Индекс", required=True)])
    async def _removeinslash(self, ctx: commands.Context, index: int = None):
        ctx.voice_state = self.get_voice_state(ctx)
        await ctx.reply(..., type = 5)

        if len(ctx.voice_state.songs) == 0:
            return await ctx.reply('В очереди нет треков. Можете добавить.')

        ctx.voice_state.songs.remove(index - 1)
        await ctx.reply('✅')

    @slash_command(name="queue", description="Очередь")
    async def _queueinslash(self, ctx: commands.Context, *, page: int = 1):
        ctx.voice_state = self.get_voice_state(ctx)
        await ctx.reply(..., type = 5)

        if len(ctx.voice_state.songs) == 0:
            return await ctx.reply('В очереди нет треков. Можете добавить.')

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
            queue += '`{0}.` [**{1.source.title}**]({1.source.url})\n'.format(i + 1, song)

        embed = (discord.Embed(description='**{} tracks:**\n\n{}'.format(len(ctx.voice_state.songs), queue))
                 .set_footer(text='Viewing page {}/{}'.format(page, pages)))
        await ctx.reply(embed=embed)

    @slash_command(name='leave')
    async def _leaveinslash(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)
        await ctx.reply(..., type=5)

        if not ctx.voice_state.voice:
            return await ctx.send('Бот и так не подключен. Зачем его кикать?')

        await ctx.voice_state.stop()
        await ctx.reply('Пока!👋')
        del self.voice_states[ctx.guild.id]

    @slash_command(name='volume',description="Изменить громкость",options=[Option("volume","Укажите значение",required=True)])
    async def _volumeinslash(self, ctx: commands.Context, *, volume: int = None):
        ctx.voice_state = self.get_voice_state(ctx)
        await ctx.reply(..., type=5)

        if not ctx.voice_state.is_playing:
            return await ctx.reply('Сейчас музыка не играет. Можете включить.')

        if 0 > volume > 100:
            return await ctx.reply('Volume must be between 0 and 100')

        ctx.voice_state.volume = volume / 100
        await ctx.reply('Громкость изменена на {}%'.format(volume))

    @slash_command(name='np',description="Что сейчас играет?")
    async def _nowinslash(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)
        if not ctx.voice_state.is_playing:
            return await ctx.reply('Сейчас музыка просто не играет. Можешь включить.')

        await ctx.reply(..., type=5)

        await ctx.reply(embed=ctx.voice_state.current.create_embed())

    @slash_command(name='skip',description="Включить следующее!")
    async def _skipinslash(self, ctx: commands.Context):
        ctx.voice_state = self.get_voice_state(ctx)
        await ctx.reply(..., type=5)

        if not ctx.voice_state.is_playing:
            return await ctx.reply('Сейчас музыка не играет,зачем её пропускать? Можете включить.')

        if not ctx.voice_state.voice:
            await ctx.reply("Пожалуйста, введи /join")

        if (ctx.voice_state.current.requester):
            await ctx.reply('⏭')
            ctx.voice_state.skip()

    @slash_command(name="test",description="Проверка кое чего ,_,")
    async def _testinslash(self, ctx: commands.Context):
        await ctx.reply(title="Я не знаю чё тут произойдёт", custom_id=2, components=None, type=9)


class Main(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help")
    async def _help(self, ctx: commands.Context):
        await ctx.reply(embed=discord.Embed(title="Список моих команд ", description="Всё разложено по полочкам :)\n**🎵Музыка**\n`mwb!play <search>` - Начать воспроизведение\n`mwb!skip` - Пропустить\n`mwb!queue` - Позырить очередь\n`mwb!leave` - Остановить музыку, и выйти\n`mwb!join` - Зайти к вам в канал\n`mwb!re` - Начать воспроизведение заново\n`mwb!remove <index>` - Удалить опред песню\n`mwb!np` - Посмотреть, что сейчас играет\n`mwb!volume <value>` - Изменить громкость, после измены не забудьте включить воспроизведение заново\n**🥇Модерация**\n`mwb!clear <amount>` - Очистить сообщения\n`mwb!mute` - Замьютить кого то\n`mwb!unmute` - Размьютить кого то\n**📍 Утилиты**\n`mwb!quote` - Бот выдаст рандомную цитату\n`mwb!card` - Увидеть свою карточку"))

class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.base = dt

    @commands.command(name="clear")
    @commands.bot_has_permissions(manage_messages=True)
    @commands.has_permissions(manage_messages=True)
    async def _clear(self, ctx: commands.Context, amount=None):
        if not amount:
            await ctx.reply("Использование команды:\n`mwb!clear <кол-во>`")
        else:
            await ctx.channel.purge(limit=int(amount))
            await ctx.reply("✅ Успешно")

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

    @commands.command(name="warn")
    async def _warn(self, ctx: commands.Context, member: discord.Member, guild: discord.Guild = None ):
        guild = ctx.guild if not guild else guild

        self.base.guild = guild.id
        self.base.user = member.id
        self.base.warn()

        await ctx.reply(f"Хорошо, варн успешно выдан пользователю {member.mention}:thumbsup: ")

    @commands.command(name="warns")
    async def _warns(self,ctx: commands.Context):
        self.base.warns_list()

        embd = discord.Embed(title = "Список варнов на этом сервере.")
        embd.add_field(name="Айди гильдии:", value = self.base.search1)
        embd.add_field(name="Айди юзера:",value=self.base.search2)
        embd.add_field(name="Варны:",value=self.base.search3)

        await ctx.reply(embed=embd)

class Utilits(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="quote")
    async def _quote(self, ctx: commands.Context):
        quotes = ["Все мы гении. Но если вы будете судить рыбу по её способности взбираться на дерево, она проживёт всю жизнь, считая себя дурой","Нужно иметь что-то общее, чтобы понимать друг друга, и чем-то отличаться, чтобы любить друг друга.","Несчастным или счастливым человека делают только его мысли, а не внешние обстоятельства. Управляя своими мыслями, он управляет своим счастьем.","Лучше молчать и показаться дураком, чем заговорить и развеять все сомнения.","Если тебе плюют в спину, значит ты впереди.","Уметь выносить одиночество и получать от него удовольствие — великий дар.","Жизнь как бумеранг..кинешь,не вернется...","Уважай себя настолько, чтобы не отдавать всех сил души и сердца тому, кому они не нужны...","Настоящий друг с тобой, когда ты не прав. Когда ты прав, всякий будет с тобой.","Проблема этого мира в том, что глупцы и фанатики слишком уверены в себе, а умные люди полны сомнений.","В шахматах это называется «цугцванг», когда оказывается, что самый полезный ход — никуда не двигаться.","Лучше быть одной, чем несчастной с кем-то.","Окружающим легко сказать: «Не принимай близко к сердцу». Откуда им знать, какова глубина твоего сердца? И где для него — близко?","— А где я могу найти кого-нибудь нормального?\n— Нигде, — ответил Кот, — нормальных не бывает. Ведь все такие разные и непохожие. И это, по-моему, нормально.","Мы не хозяева собственной жизни. Мы связаны с другими прошлым и настоящим. И каждый проступок, как и каждое доброе дело, рождают новое будущее.","Я не боюсь исчезнуть. Прежде, чем я родился, меня не было миллиарды и миллиарды лет, и я нисколько от этого не страдал.","Когда что-то понимаешь, то жить становится легче. А когда что-то почувствуешь — то тяжелее. Но почему-то всегда хочется почувствовать, а не понять!","Когда у тебя ничего нет, нечего и терять.","Хочешь, чтоб люди сочли тебя психом — скажи правду.","Когда ты одинок — это не значит, что ты слабый. Это значит, ты достаточно сильный, чтобы ждать то, что ты заслуживаешь.","Изначально тебя все бросают, если ты из-за этого захочешь повеситься, то ты всем покажешь что ты слабый. А если продолжишь это терпеть, то ты всем покажешь что ты сильный, и все захотят подружиться с тобой..."]
        embed = discord.Embed(title="Цитаты, от бота...",description=f"{random.choice(quotes)}")
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
    status_list = ["Я делаю дз, не отвлекайте :)","Спит","Пацан жениться на пацане ,_,","хз что","У МИНЯ ЕСТЬ АЙФАН"]
    while True:
        await bot.change_presence(status=discord.Status.idle, activity=discord.Activity(type = discord.ActivityType.listening, name=f'mwb!help | {random.choice(status_list)}'))
        await sleep(10)

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

bot.run(config.get_code_run())
