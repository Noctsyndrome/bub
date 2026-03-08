import os

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    channel = bot.get_channel(1475703185938317342)

    # 测试：使用实际换行符发送消息
    await channel.send("你好！Discord 支持换行显示\n第一行内容\n第二行内容\n第三行内容")

    print("Message sent!")
    await bot.close()

token = os.environ.get('BUB_DISCORD_TOKEN')
bot.run(token)
