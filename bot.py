import os
import sys
from types import ModuleType

# Python 3.13+ Compatibility Fix
# Modules 'cgi' and 'audioop' were removed in Python 3.13.
# We mock them here to stay compatible with third-party libraries.
for module_name in ["cgi", "audioop"]:
    try:
        __import__(module_name)
    except ImportError:
        sys.modules[module_name] = ModuleType(module_name)

import requests
import feedparser
import discord
import random
import asyncio
from google import genai
from google.genai import types
from discord.ext import commands, tasks
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

# 動作設定
CHARA_NAME = os.getenv("CHARA_NAME", "AIアシスタント")
CHARA_SYSTEM_PROMPT = os.getenv("CHARA_SYSTEM_PROMPT", "あなたはAIアシスタントです。ユーザーをサポートしてください。").replace("\\n", "\n")
BOT_ACTIVITY_NAME = os.getenv("BOT_ACTIVITY_NAME", "待機中")

# 投稿検知設定
raw_channel_id = os.getenv("CHANNEL_ID", "0")
try:
    CHANNEL_ID = int(raw_channel_id)
except ValueError:
    CHANNEL_ID = 0

TARGET_X_USER = os.getenv("TARGET_X_USER", "").strip("@")
NITTER_INSTANCE = os.getenv("NITTER_INSTANCE", "https://nitter.net").rstrip("/")
TWEET_NOTIFY_MESSAGE = os.getenv("TWEET_NOTIFY_MESSAGE", "新しいポストが投稿されました！")
GUILD_ID = os.getenv("GUILD_ID")

client = None
if GEMINI_KEY:
    client = genai.Client(api_key=GEMINI_KEY)

class GenericBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
    
    async def setup_hook(self):
        if GUILD_ID:
            try:
                guild = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            except: pass
        await self.tree.sync()
        print("コマンドを同期しました。")

bot = GenericBot()
last_tweet_url = None
processed_messages = set()

def fetch_latest_user_tweet():
    if not TARGET_X_USER:
        return None
    rss_url = f"{NITTER_INSTANCE}/{TARGET_X_USER}/rss"
    try:
        response = requests.get(rss_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        feed = feedparser.parse(response.content)
        if feed.entries:
            link = feed.entries[0].link
            if "/status/" in link:
                tweet_part = link.split(NITTER_INSTANCE)[-1]
                return f"https://x.com{tweet_part}".split("#")[0]
            return link
    except: pass
    return None

@tasks.loop(minutes=3.0)
async def check_new_tweets():
    if not CHANNEL_ID or not TARGET_X_USER:
        return
        
    global last_tweet_url
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        try: channel = await bot.fetch_channel(CHANNEL_ID)
        except: return

    latest_url = fetch_latest_user_tweet()
    if latest_url:
        if last_tweet_url is None:
            last_tweet_url = latest_url
            print(f"Bot起動完了。現在の最新を記録: {latest_url}")
            await channel.send(f"【監視開始】対象: @{TARGET_X_USER}\n初期取得: {latest_url}")
        elif latest_url != last_tweet_url:
            last_tweet_url = latest_url
            print(f"新しいツイート検知: {latest_url}")
            # プレースホルダーを置換
            notify_msg = TWEET_NOTIFY_MESSAGE.replace("{user}", f"@{TARGET_X_USER}")
            await channel.send(f"{notify_msg}\n{latest_url}")
        else:
            print(f"@{TARGET_X_USER} さんの新しいポストはありません。")

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    # 二重応答防止: すでに処理中または処理済みのメッセージIDなら無視
    if message.id in processed_messages:
        return
    
    # 判定前に「処理中」として記録
    is_mentioned = bot.user in message.mentions
    
    # リプライ判定（権限不足でメッセージが取れなくてもエラーにならないようにする）
    is_reply_to_bot = False
    if message.reference:
        try:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
            is_reply_to_bot = ref_msg.author == bot.user
        except:
            pass
    
    if is_mentioned or is_reply_to_bot or isinstance(message.channel, discord.DMChannel):
        if not client:
            await message.reply("AIの設定（APIキー）が未設定です。")
            return
            
        # メッセージを「処理済み」として記録
        processed_messages.add(message.id)
        # 履歴が溜まりすぎないよう制限（100件まで）
        if len(processed_messages) > 100:
            # 古い順に削除
            sorted_processed = sorted(list(processed_messages))
            processed_messages.remove(sorted_processed[0])

        async with message.channel.typing():
            try:
                content = message.content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()
                if not content: content = "こんにちは！"
                
                response = await client.aio.models.generate_content(
                    model="gemini-3-flash-preview",
                    config=types.GenerateContentConfig(
                        system_instruction=CHARA_SYSTEM_PROMPT,
                        tools=[types.Tool(google_search=types.GoogleSearch())]
                    ),
                    contents=content
                )
                await message.reply(response.text)
            except Exception as e:
                import traceback
                error_msg = str(e)
                print(f"Gemini API エラー: {error_msg}")
                traceback.print_exc()
                
                if "API_KEY_INVALID" in error_msg or "401" in error_msg:
                    await message.reply("通信エラー：APIキーが正しくないようです。`.env`の設定を見直してください。")
                elif "quota" in error_msg.lower():
                    await message.reply("通信エラー：APIの利用制限に達しました。しばらく待ってから試してください。")
                else:
                    await message.reply("通信エラーが発生しました。設定（APIキーやモデル名）を確認してください。")

    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"Discordログイン完了: {bot.user} (CHARA_NAME: {CHARA_NAME})")
    if not GEMINI_KEY:
        print("【警告】GEMINI_API_KEY が設定されていません。AIとの会話はできません。")
    await bot.change_presence(activity=discord.Game(name=BOT_ACTIVITY_NAME))
    if TARGET_X_USER and CHANNEL_ID:
        if not check_new_tweets.is_running(): check_new_tweets.start()

if __name__ == "__main__":
    if not TOKEN:
        print("エラー: DISCORD_BOT_TOKEN が設定されていません。")
    else:
        bot.run(TOKEN)
