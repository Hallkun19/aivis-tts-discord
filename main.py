import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
import aiohttp
import io
import re
import json
from dotenv import load_dotenv
from typing import Dict, Optional

# --- 定数定義 ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
AIVIS_API_KEY = os.getenv("AIVIS_API_KEY")
DEFAULT_MODEL_UUID = os.getenv(
    "AIVIS_MODEL_UUID", "a59cb814-0083-4369-8542-f51a29e72af7"
)

DATA_DIR = "data"
DICT_FILE = f"{DATA_DIR}/dictionaries.json"
SETTINGS_FILE = f"{DATA_DIR}/user_settings.json"

# 絵文字
EMOJI_SUCCESS = "✅"
EMOJI_ERROR = "❌"
EMOJI_INFO = "ℹ️"
EMOJI_VC = "🔊"
EMOJI_TTS = "💬"
EMOJI_DICT = "📖"
EMOJI_SETTING = "⚙️"
EMOJI_HELP = "🤖"
EMOJI_WAVE = "👋"
EMOJI_QUEUE = "🎵"
EMOJI_MUTE = "🔇"
EMOJI_PAUSE = "⏸️"
EMOJI_RESUME = "▶️"


# --- データクラス ---
class GuildSession:
    """サーバーごとのセッション情報を管理するクラス"""

    def __init__(self, bot_loop: asyncio.AbstractEventLoop, guild_id: str):
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel_id: Optional[int] = None
        self.queue = asyncio.Queue()
        self.is_muted: bool = False
        self.server_volume: float = 0.75  # サーバー全体の音量 (0.0 ~ 2.0)
        self.player_task = bot_loop.create_task(audio_player_task(guild_id))

    def stop(self):
        self.player_task.cancel()


# --- グローバル変数 ---
guild_sessions: Dict[str, GuildSession] = {}
dictionaries: Dict[str, Dict[str, str]] = {}
user_settings: Dict[str, Dict] = {}


# --- ヘルパー関数 ---
def load_data(filepath: str) -> dict:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_data(filepath: str, data: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def create_embed(
    title: str, description: str, color: discord.Color = discord.Color.blue()
) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


# --- Botクラスの拡張 ---
class AivisBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        # コマンドグループをTreeに追加
        self.tree.add_command(vc_commands)
        self.tree.add_command(tts_commands)
        self.tree.add_command(dict_commands)
        self.tree.add_command(setting_commands)
        await self.tree.sync()

    async def on_close(self):
        if self.http_session:
            await self.http_session.close()


# --- Botの初期化 ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = AivisBot(command_prefix="!", intents=intents)


# --- 音声合成と再生 ---
async def synthesize_speech(
    text: str, model_uuid: str, speaking_rate: float
) -> Optional[bytes]:
    url = "https://api.aivis-project.com/v1/tts/synthesize"
    headers = {
        "Authorization": f"Bearer {AIVIS_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model_uuid": model_uuid,
        "text": text,
        "output_format": "mp3",
        "speaking_rate": speaking_rate,
    }
    try:
        async with bot.http_session.post(
            url, json=payload, headers=headers
        ) as response:
            if response.status == 200:
                return await response.read()
            else:
                print(f"Aivis API Error: {response.status} - {await response.text()}")
                return None
    except Exception as e:
        print(f"An error occurred while contacting Aivis API: {e}")
        return None


async def audio_player_task(guild_id: str):
    while True:
        try:
            session = guild_sessions[guild_id]
            text, model_uuid, rate, user_volume = await session.queue.get()

            if not session.voice_client or not session.voice_client.is_connected():
                continue

            audio_data = await synthesize_speech(text, model_uuid, rate)
            if not audio_data:
                continue

            source = discord.FFmpegPCMAudio(
                io.BytesIO(audio_data), pipe=True, options="-vn"
            )
            # サーバー音量と個人音量を掛け合わせる
            final_volume = session.server_volume * user_volume
            volume_source = discord.PCMVolumeTransformer(source, volume=final_volume)
            session.voice_client.play(volume_source)

            while session.voice_client.is_playing() or session.voice_client.is_paused():
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in audio player task for guild {guild_id}: {e}")
            break


def process_text_for_speech(
    message: discord.Message, dictionary: dict
) -> Optional[str]:
    if message.attachments:
        return "添付ファイル"
    text = message.clean_content
    if re.search(r"https?://\S+", text):
        return "URL"
    for word, reading in dictionary.items():
        text = text.replace(word, reading)
    return text


# --- Botイベント ---
@bot.event
async def on_ready():
    global dictionaries, user_settings
    os.makedirs(DATA_DIR, exist_ok=True)
    dictionaries = load_data(DICT_FILE)
    user_settings = load_data(SETTINGS_FILE)
    print(f"{bot.user} としてログインしました。")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    guild_id = str(message.guild.id)
    session = guild_sessions.get(guild_id)
    if not session or session.text_channel_id != message.channel.id or session.is_muted:
        return
    if not session.voice_client or not session.voice_client.is_connected():
        return

    if message.content.lower() == "s":
        can_skip = session.voice_client.is_playing() or not session.queue.empty()

        if can_skip:
            while not session.queue.empty():
                session.queue.get_nowait()
            if session.voice_client.is_playing():
                session.voice_client.stop()
            await message.add_reaction("⏩")
        else:
            await message.add_reaction("❌")
        return

    user_id = str(message.author.id)
    settings = user_settings.get(user_id, {})
    model_uuid = settings.get("model_uuid", DEFAULT_MODEL_UUID)
    speaking_rate = settings.get("speaking_rate", 1.1)
    user_volume = settings.get("volume", 100) / 100.0  # 0.0 ~ 2.0
    dictionary = dictionaries.get(guild_id, {})
    text_to_speak = process_text_for_speech(message, dictionary)
    if not text_to_speak:
        return

    await session.queue.put((text_to_speak, model_uuid, speaking_rate, user_volume))


@bot.event
async def on_voice_state_update(
    member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
):
    if member.bot:
        return
    guild_id = str(member.guild.id)
    session = guild_sessions.get(guild_id)
    if not session or not session.voice_client:
        return
    vc_channel = session.voice_client.channel

    text = None
    if before.channel != vc_channel and after.channel == vc_channel:
        text = f"{member.display_name}さんが参加しました"
    elif before.channel == vc_channel and after.channel != vc_channel:
        text = f"{member.display_name}さんが退出しました"

    if text:
        await session.queue.put(
            (text, DEFAULT_MODEL_UUID, 1.0, 1.0)
        )


# --- スラッシュコマンド ---
@bot.tree.command(name="help", description="Botのコマンド一覧と詳細な使い方を表示します。")
async def help_command(interaction: discord.Interaction):
    embed = create_embed(
        f"{EMOJI_HELP} Aivis読み上げBot ヘルプ",
        "Aivis Cloud APIを利用した高機能な読み上げBotです。\n各コマンドの詳しい使い方を以下に示します。"
    )

    vc_description = (
        "`/vc join`: あなたがいるVCに参加し、このチャンネルの読み上げを開始します。\n"
        "`/vc leave`: VCから退出します。\n"
        "`/vc mute`: メッセージの読み上げを一時的に停止します。\n"
        "`/vc unmute`: 読み上げを再開します。\n"
        "`/vc pause`: 現在の読み上げを一時停止します。\n"
        "`/vc resume`: 一時停止した読み上げを再開します。\n"
        "`/vc volume [level]`: サーバー全体の音量を変更します。(0～200%)"
    )
    embed.add_field(name=f"{EMOJI_VC} VC関連コマンド", value=vc_description, inline=False)

    tts_description = (
        "`/tts channel [channel]`: 読み上げ対象のテキストチャンネルを変更します。\n"
        "`/tts queue`: 再生待ちのメッセージ一覧を表示します。"
    )
    embed.add_field(name=f"{EMOJI_TTS} 読み上げ関連コマンド", value=tts_description, inline=False)

    dict_description = (
        "`/dict add [word] [reading]`: 単語とその読みを辞書に登録します。\n"
        "`/dict remove [word]`: 辞書から単語を削除します。\n"
        "`/dict list`: 登録されている単語の一覧を表示します。"
    )
    embed.add_field(name=f"{EMOJI_DICT} 辞書関連コマンド", value=dict_description, inline=False)

    setting_description = (
        "`/setting model [model_uuid]`: あなたの声の種類を変更します。\n"
        "（UUIDは[AivisHub](https://hub.aivis-project.com/)で探せます。）\n"
        "`/setting speed [rate]`: あなたの読み上げ速度を変更します。(0.5～2.0)\n"
        "`/setting volume [level]`: あなたの個人音量を変更します。(0～200%)\n"
        "`/setting view`: あなたの現在の個人設定を確認します。\n"
        "`/setting reset`: あなたの個人設定をすべてリセットします。"
    )
    embed.add_field(name=f"{EMOJI_SETTING} 個人設定コマンド", value=setting_description, inline=False)

    other_description = (
        "**`s` と送信**: 再生中の音声と再生待ちのメッセージをすべてスキップします。\n"
        "**VCへの参加/退出通知**: ユーザーがVCに出入りすると、その旨を読み上げます。"
    )
    embed.add_field(name="✨ その他の機能", value=other_description, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- コマンドグループ ---
vc_commands = app_commands.Group(name="vc", description="ボイスチャンネル関連の操作")
tts_commands = app_commands.Group(name="tts", description="読み上げ関連の操作")
dict_commands = app_commands.Group(name="dict", description="辞書機能を管理します。")
setting_commands = app_commands.Group(
    name="setting", description="個人の読み上げ設定を管理します。"
)


@vc_commands.command(
    name="join", description="VCに参加し、このチャンネルの読み上げを開始します。"
)
async def vc_join(interaction: discord.Interaction):
    if not interaction.user.voice:
        return await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_ERROR} エラー",
                "先にボイスチャンネルに参加してください。",
                discord.Color.red(),
            ),
            ephemeral=True,
        )

    voice_channel = interaction.user.voice.channel
    try:
        vc = await voice_channel.connect()
        await interaction.guild.me.edit(deafen=True)

        guild_id = str(interaction.guild.id)
        if guild_id not in guild_sessions:
            guild_sessions[guild_id] = GuildSession(bot.loop, guild_id)

        session = guild_sessions[guild_id]
        session.voice_client = vc
        session.text_channel_id = interaction.channel.id

        embed = create_embed(
            f"{EMOJI_VC} 接続しました", f"**{voice_channel.name}** に参加しました。"
        )
        embed.add_field(
            name="読み上げチャンネル", value=interaction.channel.mention, inline=False
        )
        embed.add_field(
            name="サーバー音量",
            value=f"{int(session.server_volume * 100)}%",
            inline=True,
        )
        embed.add_field(name="読み上げ状態", value="有効", inline=True)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_ERROR} エラー", f"接続に失敗しました: {e}", discord.Color.red()
            ),
            ephemeral=True,
        )


@vc_commands.command(name="leave", description="VCから退出します。")
async def vc_leave(interaction: discord.Interaction):
    if not interaction.guild.voice_client:
        return await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_ERROR} エラー",
                "BotはVCに参加していません。",
                discord.Color.red(),
            ),
            ephemeral=True,
        )
    guild_id = str(interaction.guild.id)
    if guild_id in guild_sessions:
        guild_sessions[guild_id].stop()
        del guild_sessions[guild_id]
    await interaction.guild.voice_client.disconnect()
    await interaction.response.send_message(
        embed=create_embed(
            f"{EMOJI_WAVE} 退出しました", "ボイスチャンネルから退出しました。"
        )
    )


@vc_commands.command(name="mute", description="読み上げをミュートします。")
async def vc_mute(interaction: discord.Interaction):
    session = guild_sessions.get(str(interaction.guild.id))
    if session:
        session.is_muted = True
        await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_MUTE} ミュートしました",
                "メッセージの読み上げを停止します。\n`/vc unmute` で再開できます。",
            )
        )


@vc_commands.command(name="unmute", description="読み上げのミュートを解除します。")
async def vc_unmute(interaction: discord.Interaction):
    session = guild_sessions.get(str(interaction.guild.id))
    if session:
        session.is_muted = False
        await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_VC} ミュート解除", "メッセージの読み上げを再開します。"
            )
        )


@vc_commands.command(name="pause", description="再生を一時停止します。")
async def vc_pause(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.pause()
        await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_PAUSE} 一時停止", "読み上げを一時停止しました。"
            )
        )


@vc_commands.command(name="resume", description="再生を再開します。")
async def vc_resume(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
        interaction.guild.voice_client.resume()
        await interaction.response.send_message(
            embed=create_embed(f"{EMOJI_RESUME} 再開", "読み上げを再開しました。")
        )


@vc_commands.command(
    name="volume", description="サーバー全体の音量を変更します (0-200%)。"
)
@app_commands.describe(level="音量をパーセントで指定 (例: 100)")
async def vc_volume(
    interaction: discord.Interaction, level: app_commands.Range[int, 0, 200]
):
    session = guild_sessions.get(str(interaction.guild.id))
    if not session:
        return await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_ERROR} エラー",
                "BotがVCに参加していません。",
                discord.Color.red(),
            ),
            ephemeral=True,
        )

    session.server_volume = level / 100.0
    if interaction.guild.voice_client and interaction.guild.voice_client.source:
        interaction.guild.voice_client.source.volume = session.server_volume * (
            user_settings.get(str(interaction.user.id), {}).get("volume", 100) / 100.0
        )
    await interaction.response.send_message(
        embed=create_embed(
            f"{EMOJI_VC} 音量変更",
            f"サーバー全体の読み上げ音量を **{level}%** に設定しました。",
        )
    )


@tts_commands.command(
    name="channel", description="読み上げるテキストチャンネルを変更します。"
)
async def tts_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    session = guild_sessions.get(str(interaction.guild.id))
    if not session:
        return await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_ERROR} エラー",
                "先に `/vc join` でBotをVCに参加させてください。",
                discord.Color.red(),
            ),
            ephemeral=True,
        )
    session.text_channel_id = channel.id
    await interaction.response.send_message(
        embed=create_embed(
            f"{EMOJI_SUCCESS} チャンネル変更",
            f"読み上げチャンネルを **{channel.mention}** に変更しました。",
        )
    )


@tts_commands.command(
    name="queue", description="再生待ちのメッセージ一覧を表示します。"
)
async def tts_queue(interaction: discord.Interaction):
    session = guild_sessions.get(str(interaction.guild.id))
    if not session or session.queue.empty():
        return await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_QUEUE} 再生待ちリスト",
                "現在、再生待ちのメッセージはありません。",
            ),
            ephemeral=True,
        )

    queue_list = list(session.queue._queue)
    embed = create_embed(
        f"{EMOJI_QUEUE} 再生待ちリスト",
        f"現在 {len(queue_list)} 件のメッセージが待機中です。",
    )
    description = "\n".join(
        [
            f"{i+1}. {EMOJI_TTS} `{text[:50]}`"
            for i, (text, *_) in enumerate(queue_list[:10])
        ]
    )
    if len(queue_list) > 10:
        description += f"\n...他 {len(queue_list) - 10} 件"
    embed.description = description
    await interaction.response.send_message(embed=embed, ephemeral=True)


@dict_commands.command(name="add", description="辞書に単語と読みを登録します。")
async def dict_add(interaction: discord.Interaction, word: str, reading: str):
    guild_id = str(interaction.guild.id)
    if guild_id not in dictionaries:
        dictionaries[guild_id] = {}
    dictionaries[guild_id][word] = reading
    save_data(DICT_FILE, dictionaries)
    await interaction.response.send_message(
        embed=create_embed(
            f"{EMOJI_SUCCESS} 辞書登録",
            f"「**{word}**」を「**{reading}**」として登録しました。",
        ),
        ephemeral=True,
    )


@dict_commands.command(name="remove", description="辞書から単語を削除します。")
async def dict_remove(interaction: discord.Interaction, word: str):
    guild_id = str(interaction.guild.id)
    if guild_id in dictionaries and word in dictionaries[guild_id]:
        del dictionaries[guild_id][word]
        save_data(DICT_FILE, dictionaries)
        await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_SUCCESS} 辞書削除", f"「**{word}**」を削除しました。"
            ),
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_ERROR} エラー",
                f"「**{word}**」は見つかりませんでした。",
                discord.Color.red(),
            ),
            ephemeral=True,
        )


@dict_commands.command(
    name="list", description="登録されている単語の一覧を表示します。"
)
async def dict_list(interaction: discord.Interaction):
    guild_id = str(interaction.guild.id)
    dictionary = dictionaries.get(guild_id, {})
    if not dictionary:
        return await interaction.response.send_message(
            embed=create_embed(f"{EMOJI_DICT} 辞書一覧", "辞書は空です。"),
            ephemeral=True,
        )
    embed = create_embed(
        f"{EMOJI_DICT} {interaction.guild.name} の辞書一覧",
        "\n".join([f"・`{w}` → `{r}`" for w, r in dictionary.items()]),
        discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@setting_commands.command(
    name="model", description="読み上げに使用する声のモデルを変更します。"
)
async def setting_model(interaction: discord.Interaction, model_uuid: str):
    user_id = str(interaction.user.id)
    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id]["model_uuid"] = model_uuid
    save_data(SETTINGS_FILE, user_settings)
    embed = create_embed(
        f"{EMOJI_SUCCESS} 声のモデルを設定しました",
        f"あなたの読み上げ音声を新しいモデルに変更しました。\nUUID: `{model_uuid}`",
    )
    embed.add_field(
        name="💡 モデルを探す",
        value="他の声は[AivisHub](https://hub.aivis-project.com/)で探せます。",
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@setting_commands.command(
    name="speed", description="読み上げの速さを変更します。(0.5 ~ 2.0)"
)
@app_commands.describe(rate="速度を数値で指定 (例: 1.2)")
async def setting_speed(
    interaction: discord.Interaction, rate: app_commands.Range[float, 0.5, 2.0]
):
    user_id = str(interaction.user.id)
    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id]["speaking_rate"] = rate
    save_data(SETTINGS_FILE, user_settings)
    await interaction.response.send_message(
        embed=create_embed(
            f"{EMOJI_SUCCESS} 速度設定",
            f"あなたの読み上げ速度を **{rate}** に設定しました。",
        ),
        ephemeral=True,
    )


@setting_commands.command(
    name="volume", description="個人の音量を変更します (0-200%)。"
)
@app_commands.describe(level="音量をパーセントで指定 (例: 120)")
async def setting_volume(
    interaction: discord.Interaction, level: app_commands.Range[int, 0, 200]
):
    user_id = str(interaction.user.id)
    if user_id not in user_settings:
        user_settings[user_id] = {}
    user_settings[user_id]["volume"] = level
    save_data(SETTINGS_FILE, user_settings)
    await interaction.response.send_message(
        embed=create_embed(
            f"{EMOJI_SUCCESS} 音量設定",
            f"あなたの個人音量を **{level}%** に設定しました。",
        ),
        ephemeral=True,
    )


@setting_commands.command(name="view", description="現在の設定を確認します。")
async def setting_view(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    settings = user_settings.get(user_id, {})
    model = settings.get("model_uuid", f"{DEFAULT_MODEL_UUID} (デフォルト)")
    speed = settings.get("speaking_rate", "1.1 (デフォルト)")
    volume = settings.get("volume", "100 (デフォルト)")
    
    embed = create_embed(
        title=f"{EMOJI_SETTING} {interaction.user.display_name} の設定",
        description="あなたの現在の読み上げ設定です。",
        color=discord.Color.purple()
    )
    embed.add_field(name="声のモデル (UUID)", value=f"`{model}`", inline=False)
    embed.add_field(name="話速", value=f"`{speed}`", inline=True)
    embed.add_field(name="個人音量", value=f"`{volume}%`", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@setting_commands.command(name="reset", description="個人設定をデフォルトに戻します。")
async def setting_reset(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if user_id in user_settings:
        del user_settings[user_id]
        save_data(SETTINGS_FILE, user_settings)
        await interaction.response.send_message(
            embed=create_embed(
                f"{EMOJI_SUCCESS} 設定リセット", "個人設定をデフォルトに戻しました。"
            ),
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            embed=create_embed(f"{EMOJI_INFO} 情報", "設定はまだ変更されていません。"),
            ephemeral=True,
        )


# --- Bot実行 ---
if __name__ == "__main__":
    if not all([DISCORD_TOKEN, AIVIS_API_KEY]):
        print("エラー: .envファイルにDISCORD_TOKENとAIVIS_API_KEYを設定してください。")
    else:
        bot.run(DISCORD_TOKEN)
