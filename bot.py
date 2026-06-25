import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import json
import os
import sys
import math
from collections import deque
import urllib.request
import urllib.parse

# ==================== 設定 ====================
OWNER_ID = 764697572643307543
HF_TOKEN = os.environ.get("HF_TOKEN")
HF_DATASET_REPO = os.environ.get("HF_DATASET_REPO")  # 例: "yourname/bot-data"
DJ_ROLES_FILE = "dj_roles.json"

# ==================== DJロール管理（HF Dataset永続化） ====================
def load_dj_roles():
    if not HF_TOKEN or not HF_DATASET_REPO:
        # フォールバック: ローカルファイル
        if os.path.exists(DJ_ROLES_FILE):
            with open(DJ_ROLES_FILE, "r") as f:
                return json.load(f)
        return {}
    try:
        url = f"https://huggingface.co/datasets/{HF_DATASET_REPO}/resolve/main/dj_roles.json"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {HF_TOKEN}"})
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode())
    except Exception:
        return {}

def save_dj_roles(data):
    if not HF_TOKEN or not HF_DATASET_REPO:
        with open(DJ_ROLES_FILE, "w") as f:
            json.dump(data, f)
        return
    try:
        content = json.dumps(data, ensure_ascii=False)
        import base64
        # まず既存ファイルのSHAを取得
        sha = None
        try:
            url = f"https://huggingface.co/api/datasets/{HF_DATASET_REPO}/raw/main/dj_roles.json"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {HF_TOKEN}"})
            with urllib.request.urlopen(req, timeout=10) as res:
                info = json.loads(res.read().decode())
                sha = info.get("sha")
        except Exception:
            pass

        payload = {
            "message": "update dj_roles",
            "content": base64.b64encode(content.encode()).decode(),
        }
        if sha:
            payload["sha"] = sha

        url = f"https://huggingface.co/api/datasets/{HF_DATASET_REPO}/upload/main/dj_roles.json"
        req_data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={
                "Authorization": f"Bearer {HF_TOKEN}",
                "Content-Type": "application/json",
            },
            method="PUT",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"HF保存エラー: {e}")
        # フォールバック保存
        with open(DJ_ROLES_FILE, "w") as f:
            json.dump(data, f)

dj_roles: dict[str, int] = load_dj_roles()

def get_dj_role(guild_id: int):
    return dj_roles.get(str(guild_id))

def has_dj_role(member: discord.Member):
    role_id = get_dj_role(member.guild.id)
    if role_id is None:
        return False
    return any(r.id == role_id for r in member.roles)

# ==================== yt-dlp設定 ====================
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": "in_playlist",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

class Track:
    def __init__(self, url, title, webpage_url, duration=None, requester=None):
        self.url = url          # 実際の音声URL
        self.title = title
        self.webpage_url = webpage_url
        self.duration = duration
        self.requester = requester

    @classmethod
    async def from_url(cls, query, *, loop=None, requester=None):
        loop = loop or asyncio.get_event_loop()
        opts = {**YTDL_OPTIONS, "extract_flat": False}
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
        if "entries" in data:
            data = data["entries"][0]
        url = data.get("url")
        title = data.get("title", "不明なタイトル")
        webpage_url = data.get("webpage_url", query)
        duration = data.get("duration")
        return cls(url, title, webpage_url, duration, requester)

    @classmethod
    async def search(cls, query, *, loop=None, requester=None, max_results=5):
        loop = loop or asyncio.get_event_loop()
        opts = {**YTDL_OPTIONS, "extract_flat": False, "default_search": "ytsearch5"}
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = await loop.run_in_executor(None, lambda: ydl.extract_info(f"ytsearch5:{query}", download=False))
        entries = data.get("entries", [])[:max_results]
        results = []
        for e in entries:
            results.append({
                "title": e.get("title", "不明"),
                "url": e.get("webpage_url") or e.get("url"),
                "duration": e.get("duration"),
            })
        return results

# ==================== キュー管理 ====================
class GuildPlayer:
    def __init__(self):
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.skip_votes: set[int] = set()
        self.loop = False

players: dict[int, GuildPlayer] = {}

def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in players:
        players[guild_id] = GuildPlayer()
    return players[guild_id]

# ==================== Bot設定 ====================
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== 再生ロジック ====================
async def play_next(guild: discord.Guild, channel: discord.TextChannel = None):
    player = get_player(guild.id)
    vc = guild.voice_client

    if not vc or not vc.is_connected():
        return

    if not player.queue:
        player.current = None
        return

    track = player.queue.popleft()
    player.current = track
    player.skip_votes.clear()

    source = discord.FFmpegPCMAudio(track.url, **FFMPEG_OPTIONS)
    source = discord.PCMVolumeTransformer(source, volume=0.5)

    def after_play(error):
        if error:
            print(f"再生エラー: {error}")
        asyncio.run_coroutine_threadsafe(play_next(guild, channel), bot.loop)

    vc.play(source, after=after_play)

    if channel:
        dur = f"{int(track.duration//60)}:{int(track.duration%60):02d}" if track.duration else "不明"
        embed = discord.Embed(title="▶️ 再生中", description=f"[{track.title}]({track.webpage_url})", color=0x1db954)
        embed.add_field(name="長さ", value=dur)
        if track.requester:
            embed.set_footer(text=f"リクエスト: {track.requester.display_name}")
        await channel.send(embed=embed)

# ==================== スラッシュコマンド ====================

@bot.tree.command(name="j", description="あなたのVCに参加します")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ VCに入ってから使って", ephemeral=True)
        return
    channel = interaction.user.voice.channel
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.move_to(channel)
    else:
        await channel.connect()
    await interaction.response.send_message(f"✅ `{channel.name}` に参加したよ")

@bot.tree.command(name="kill", description="VCから離脱します")
async def kill(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("❌ VCにいないよ", ephemeral=True)
        return
    player = get_player(interaction.guild.id)
    player.queue.clear()
    player.current = None
    await vc.disconnect()
    await interaction.response.send_message("👋 切断したよ")

@bot.tree.command(name="play", description="曲を再生します（URL or 検索ワード）")
@app_commands.describe(query="URLまたは曲名")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ VCに入ってから使って", ephemeral=True)
        return

    await interaction.response.defer()

    # VCに参加
    vc = interaction.guild.voice_client
    if not vc:
        vc = await interaction.user.voice.channel.connect()
    elif vc.channel != interaction.user.voice.channel:
        await vc.move_to(interaction.user.voice.channel)

    try:
        track = await Track.from_url(query, requester=interaction.user)
    except Exception as e:
        await interaction.followup.send(f"❌ 取得失敗: {e}")
        return

    player = get_player(interaction.guild.id)
    player.queue.append(track)

    if not vc.is_playing():
        await play_next(interaction.guild, interaction.channel)
        # play_nextで送信するのでここでは何も送らない
    else:
        dur = f"{int(track.duration//60)}:{int(track.duration%60):02d}" if track.duration else "不明"
        embed = discord.Embed(title="📋 キューに追加", description=f"[{track.title}]({track.webpage_url})", color=0x5865f2)
        embed.add_field(name="長さ", value=dur)
        embed.add_field(name="キュー位置", value=f"{len(player.queue)}番目")
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="search", description="曲を検索して選択します")
@app_commands.describe(query="検索ワード")
async def search(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ VCに入ってから使って", ephemeral=True)
        return

    await interaction.response.defer()

    try:
        results = await Track.search(query, requester=interaction.user)
    except Exception as e:
        await interaction.followup.send(f"❌ 検索失敗: {e}")
        return

    if not results:
        await interaction.followup.send("❌ 検索結果が見つからなかったよ")
        return

    # 選択肢を表示
    view = SearchView(results, interaction.user, interaction.guild, interaction.channel)
    embed = discord.Embed(title=f"🔍 「{query}」の検索結果", color=0xffa500)
    for i, r in enumerate(results, 1):
        dur = f"{int(r['duration']//60)}:{int(r['duration']%60):02d}" if r.get('duration') else "不明"
        embed.add_field(name=f"{i}. {r['title']}", value=f"長さ: {dur}", inline=False)
    await interaction.followup.send(embed=embed, view=view)

class SearchView(discord.ui.View):
    def __init__(self, results, user, guild, channel):
        super().__init__(timeout=30)
        self.results = results
        self.user = user
        self.guild = guild
        self.channel = channel
        for i, r in enumerate(results, 1):
            btn = discord.ui.Button(label=str(i), style=discord.ButtonStyle.primary)
            btn.callback = self.make_callback(r)
            self.add_item(btn)

    def make_callback(self, result):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user.id:
                await interaction.response.send_message("❌ コマンドを実行した人だけ選べるよ", ephemeral=True)
                return
            await interaction.response.defer()
            self.stop()
            try:
                track = await Track.from_url(result["url"], requester=self.user)
            except Exception as e:
                await interaction.followup.send(f"❌ 取得失敗: {e}")
                return

            vc = self.guild.voice_client
            if not vc:
                if self.user.voice:
                    vc = await self.user.voice.channel.connect()
                else:
                    await interaction.followup.send("❌ VCに入ってから使って")
                    return

            player = get_player(self.guild.id)
            player.queue.append(track)

            if not vc.is_playing():
                await play_next(self.guild, self.channel)
            else:
                embed = discord.Embed(title="📋 キューに追加", description=f"[{track.title}]({track.webpage_url})", color=0x5865f2)
                await interaction.followup.send(embed=embed)
        return callback

@bot.tree.command(name="pause", description="一時停止/再開")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("❌ 再生中じゃないよ", ephemeral=True)
        return
    if vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ 再開したよ")
    elif vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ 一時停止したよ")
    else:
        await interaction.response.send_message("❌ 再生中じゃないよ", ephemeral=True)

@bot.tree.command(name="stop", description="再生を停止してキューをリセットします")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("❌ 再生中じゃないよ", ephemeral=True)
        return
    player = get_player(interaction.guild.id)
    player.queue.clear()
    player.current = None
    vc.stop()
    await interaction.response.send_message("⏹️ 停止してキューをリセットしたよ")

@bot.tree.command(name="skip", description="投票スキップ（VC参加者の過半数が必要）")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("❌ 再生中じゃないよ", ephemeral=True)
        return
    if not interaction.user.voice or interaction.user.voice.channel != vc.channel:
        await interaction.response.send_message("❌ 同じVCに入ってから使って", ephemeral=True)
        return

    player = get_player(interaction.guild.id)
    player.skip_votes.add(interaction.user.id)

    # Botを除いたVC人数
    members = [m for m in vc.channel.members if not m.bot]
    required = math.ceil(len(members) / 2)
    votes = len(player.skip_votes)

    if votes >= required:
        player.skip_votes.clear()
        vc.stop()
        await interaction.response.send_message(f"⏭️ スキップしたよ（{votes}/{required}票）")
    else:
        await interaction.response.send_message(f"🗳️ スキップ投票: {votes}/{required}票（あと{required - votes}票必要）")

@bot.tree.command(name="fs", description="強制スキップ（DJロール必要）")
async def force_skip(interaction: discord.Interaction):
    if not has_dj_role(interaction.user) and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ DJロールがないと使えないよ", ephemeral=True)
        return
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("❌ 再生中じゃないよ", ephemeral=True)
        return
    get_player(interaction.guild.id).skip_votes.clear()
    vc.stop()
    await interaction.response.send_message("⏭️ 強制スキップしたよ")

@bot.tree.command(name="djset", description="DJロールをセットします")
@app_commands.describe(role="DJロール")
@app_commands.checks.has_permissions(manage_roles=True)
async def djset(interaction: discord.Interaction, role: discord.Role):
    dj_roles[str(interaction.guild.id)] = role.id
    save_dj_roles(dj_roles)
    await interaction.response.send_message(f"✅ DJロールを {role.mention} にセットしたよ")

@djset.error
async def djset_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ ロール管理権限がないと使えないよ", ephemeral=True)

@bot.tree.command(name="restart", description="Botを再起動します（オーナーのみ）")
async def restart(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ お前じゃないと使えないよ", ephemeral=True)
        return
    await interaction.response.send_message("🔄 再起動するよ...")
    await bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)

# ==================== 起動 ====================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ {bot.user} 起動完了")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="/play"))

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN が設定されていないよ")

bot.run(TOKEN)
