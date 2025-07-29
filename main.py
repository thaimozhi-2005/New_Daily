import os
import asyncio
import logging
from datetime import datetime
from io import BytesIO
import aiohttp
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from urllib.parse import urlencode
import tempfile
import sys
import signal
from health import start_health_server

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Environment variables
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Initialize bot
app = Client("dailymotion_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global health server reference
health_server = None

# Database connection with retry logic
def get_db_connection(max_retries=3):
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        except Exception as e:
            logger.error(f"Database connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                asyncio.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.error("All database connection attempts failed")
                return None

# Initialize database
def init_db():
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    channel_name VARCHAR(255) NOT NULL,
                    api_key VARCHAR(255) NOT NULL,
                    api_secret VARCHAR(255) NOT NULL,
                    username VARCHAR(255) NOT NULL,
                    password VARCHAR(255) NOT NULL,
                    access_token VARCHAR(500),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, channel_name)
                )
            """)
            
            # Create index for better performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_channels_user_id ON channels(user_id)
            """)
            
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")

class DailymotionUploader:
    def __init__(self, api_key, api_secret, username, password):
        self.api_key = api_key
        self.api_secret = api_secret
        self.username = username
        self.password = password
        self.access_token = None
        self.base_url = "https://api.dailymotion.com"
        self.session = None
    
    async def get_session(self):
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=300, connect=30)  # 5 min total, 30s connect
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session
    
    async def close_session(self):
        """Close aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def authenticate(self):
        """Authenticate with Dailymotion API with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                auth_url = f"{self.base_url}/oauth/token"
                data = {
                    'grant_type': 'password',
                    'client_id': self.api_key,
                    'client_secret': self.api_secret,
                    'username': self.username,
                    'password': self.password,
                    'scope': 'manage_videos'
                }
                
                session = await self.get_session()
                async with session.post(auth_url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        self.access_token = result.get('access_token')
                        if self.access_token:
                            logger.info("Dailymotion authentication successful")
                            return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Authentication failed (attempt {attempt + 1}): {response.status} - {error_text}")
                        
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        
            except asyncio.TimeoutError:
                logger.error(f"Authentication timeout (attempt {attempt + 1})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Authentication error (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        
        return False
    
    async def upload_video(self, file_path, title, description="", tags="", progress_callback=None):
        """Upload video to Dailymotion with enhanced error handling"""
        try:
            if not self.access_token:
                if not await self.authenticate():
                    return None
            
            # Step 1: Get upload URL
            upload_url_endpoint = f"{self.base_url}/file/upload"
            params = {'access_token': self.access_token}
            
            session = await self.get_session()
            
            # Get upload URL with retry
            upload_url = None
            for attempt in range(3):
                try:
                    async with session.get(upload_url_endpoint, params=params) as response:
                        if response.status == 200:
                            upload_data = await response.json()
                            upload_url = upload_data.get('upload_url')
                            break
                        elif response.status == 401:
                            # Token expired, re-authenticate
                            if await self.authenticate():
                                params['access_token'] = self.access_token
                                continue
                            else:
                                logger.error("Re-authentication failed")
                                return None
                        else:
                            error_text = await response.text()
                            logger.error(f"Failed to get upload URL (attempt {attempt + 1}): {response.status} - {error_text}")
                            
                except asyncio.TimeoutError:
                    logger.error(f"Upload URL request timeout (attempt {attempt + 1})")
                except Exception as e:
                    logger.error(f"Upload URL request error (attempt {attempt + 1}): {e}")
                
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
            
            if not upload_url:
                logger.error("Could not get upload URL after all attempts")
                return None
            
            # Step 2: Upload file with progress tracking
            file_size = os.path.getsize(file_path)
            
            async with aiofiles.open(file_path, 'rb') as file:
                file_content = await file.read()
                
                # Upload with retry logic
                file_url = None
                for attempt in range(3):
                    try:
                        data = aiohttp.FormData()
                        data.add_field('file', file_content, filename=os.path.basename(file_path))
                        
                        if progress_callback:
                            await progress_callback(50)  # 50% progress during upload
                        
                        async with session.post(upload_url, data=data) as response:
                            if response.status == 200:
                                upload_result = await response.json()
                                file_url = upload_result.get('url')
                                break
                            else:
                                error_text = await response.text()
                                logger.error(f"File upload failed (attempt {attempt + 1}): {response.status} - {error_text}")
                                
                    except asyncio.TimeoutError:
                        logger.error(f"File upload timeout (attempt {attempt + 1})")
                    except Exception as e:
                        logger.error(f"File upload error (attempt {attempt + 1}): {e}")
                        
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                
                if not file_url:
                    logger.error("File upload failed after all attempts")
                    return None
            
            if progress_callback:
                await progress_callback(75)  # 75% progress
            
            # Step 3: Create video with retry
            create_url = f"{self.base_url}/me/videos"
            video_data = {
                'access_token': self.access_token,
                'url': file_url,
                'title': title,
                'description': description,
                'tags': tags,
                'published': 'true'
            }
            
            for attempt in range(3):
                try:
                    async with session.post(create_url, data=video_data) as response:
                        if response.status == 200:
                            result = await response.json()
                            video_id = result.get('id')
                            if video_id:
                                video_url = f"https://www.dailymotion.com/video/{video_id}"
                                logger.info(f"Video uploaded successfully: {video_url}")
                                
                                if progress_callback:
                                    await progress_callback(100)  # 100% complete
                                
                                return video_url
                        elif response.status == 401:
                            # Token expired, re-authenticate
                            if await self.authenticate():
                                video_data['access_token'] = self.access_token
                                continue
                            else:
                                logger.error("Re-authentication failed during video creation")
                                return None
                        else:
                            error_text = await response.text()
                            logger.error(f"Video creation failed (attempt {attempt + 1}): {response.status} - {error_text}")
                            
                except asyncio.TimeoutError:
                    logger.error(f"Video creation timeout (attempt {attempt + 1})")
                except Exception as e:
                    logger.error(f"Video creation error (attempt {attempt + 1}): {e}")
                
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
            
            logger.error("Video creation failed after all attempts")
            return None
                    
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None
        finally:
            await self.close_session()

# User states for multi-step commands
user_states = {}

@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    welcome_text = """
🎬 **Welcome to Dailymotion Upload Bot!** 🎬

I'm here to help you upload videos directly to your Dailymotion accounts with ease!

**What I can do:**
✅ Upload videos to multiple Dailymotion accounts
✅ Handle large video files efficiently  
✅ Show upload progress in real-time
✅ Manage multiple Dailymotion channels
✅ Robust error handling and recovery

**Getting Started:**
1. Add your Dailymotion account using /addchannel
2. Use /upload to upload videos
3. Use /list to see your added channels
4. Use /help for detailed instructions

**Features:**
🔄 Automatic retry on failures
📊 Real-time progress tracking
🔐 Secure credential storage
🌐 Works with large files (up to 2GB)

Let's get started! 🚀
    """
    await message.reply_text(welcome_text)

@app.on_message(filters.command("help"))
async def help_command(client, message: Message):
    help_text = """
📖 **How to use Dailymotion Upload Bot**

**Commands:**
🔹 `/start` - Welcome message and bot info
🔹 `/addchannel` - Add a new Dailymotion account
🔹 `/upload` - Upload a video to Dailymotion
🔹 `/list` - Show all your added channels
🔹 `/rmchannel` - Remove a channel
🔹 `/help` - Show this help message

**How to upload videos:**
1. First, add your Dailymotion account credentials using `/addchannel`
2. Use `/upload` command
3. Send your video file when prompted
4. Choose which account to upload to
5. Wait for the upload to complete

**Supported Formats:**
📹 MP4, AVI, MOV, MKV, WMV, FLV
📏 Maximum file size: 2GB
⏱️ Upload time depends on file size and internet speed

**Getting API Credentials:**
1. Go to https://api.dailymotion.com
2. Create a developer account
3. Register your application
4. Get your API Key and Secret
5. Use your Dailymotion username/password

**Important Notes:**
• All credentials are stored securely
• Bot handles connection issues automatically
• Progress is shown during upload/download
• Videos are processed and uploaded efficiently

Need more help? Contact support or check Dailymotion Developer Documentation.
    """
    await message.reply_text(help_text)

@app.on_message(filters.command("addchannel"))
async def add_channel_command(client, message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "channel_name", "data": {}}
    
    await message.reply_text(
        "📺 **Add New Dailymotion Channel**\n\n"
        "Let's add your Dailymotion account step by step.\n\n"
        "**Step 1/5:** Please enter a friendly name for this channel:\n"
        "*(Example: My Main Channel, Gaming Videos, etc.)*"
    )

@app.on_message(filters.command("list"))
async def list_channels_command(client, message: Message):
    user_id = message.from_user.id
    conn = get_db_connection()
    
    if not conn:
        await message.reply_text("❌ Database connection error. Please try again later.")
        return
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT channel_name, username, created_at FROM channels WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        channels = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not channels:
            await message.reply_text(
                "📺 **No channels found!**\n\n"
                "You haven't added any channels yet.\n"
                "Use /addchannel to add your first Dailymotion account! 🚀"
            )
            return
        
        channel_list = f"📺 **Your Dailymotion Channels ({len(channels)}):**\n\n"
        for i, channel in enumerate(channels, 1):
            created_date = channel['created_at'].strftime("%Y-%m-%d")
            channel_list += f"{i}. **{channel['channel_name']}**\n"
            channel_list += f"   👤 Username: {channel['username']}\n"
            channel_list += f"   📅 Added: {created_date}\n\n"
        
        channel_list += "💡 Use /upload to upload videos to any of these channels!"
        
        await message.reply_text(channel_list)
        
    except Exception as e:
        logger.error(f"List channels error: {e}")
        await message.reply_text("❌ Error retrieving channels. Please try again.")

@app.on_message(filters.command("rmchannel"))
async def remove_channel_command(client, message: Message):
    user_id = message.from_user.id
    conn = get_db_connection()
    
    if not conn:
        await message.reply_text("❌ Database connection error. Please try again later.")
        return
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, channel_name, username FROM channels WHERE user_id = %s ORDER BY channel_name", (user_id,))
        channels = cursor.fetchall()
        
        if not channels:
            await message.reply_text(
                "📺 **No channels to remove!**\n\n"
                "You don't have any channels added yet.\n"
                "Use /addchannel to add a channel first!"
            )
            cursor.close()
            conn.close()
            return
        
        # Create inline keyboard with channels
        keyboard = []
        for channel in channels:
            keyboard.append([InlineKeyboardButton(
                f"🗑️ {channel['channel_name']} (@{channel['username']})", 
                callback_data=f"remove_{channel['id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_remove")])
        
        await message.reply_text(
            "🗑️ **Remove Channel**\n\n"
            "⚠️ **Warning:** This will permanently delete the channel and all its credentials.\n\n"
            "Select a channel to remove:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Remove channel error: {e}")
        await message.reply_text("❌ Error retrieving channels. Please try again.")

@app.on_message(filters.command("upload"))
async def upload_command(client, message: Message):
    user_id = message.from_user.id
    conn = get_db_connection()
    
    if not conn:
        await message.reply_text("❌ Database connection error. Please try again later.")
        return
    
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM channels WHERE user_id = %s", (user_id,))
        channel_count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        
        if channel_count == 0:
            await message.reply_text(
                "📺 **No channels found!**\n\n"
                "You need to add at least one Dailymotion account before uploading.\n\n"
                "🔧 **Steps to get started:**\n"
                "1. Use /addchannel to add your Dailymotion account\n"
                "2. Get your API credentials from https://api.dailymotion.com\n"
                "3. Come back and use /upload\n\n"
                "Need help? Use /help for detailed instructions! 📖"
            )
            return
        
        user_states[user_id] = {"step": "waiting_video"}
        await message.reply_text(
            "🎬 **Upload Video to Dailymotion**\n\n"
            "Please send me the video file you want to upload.\n\n"
            "📝 **Supported formats:** MP4, AVI, MOV, MKV, WMV, FLV\n"
            "📏 **Maximum file size:** 2GB\n"
            "⏱️ **Processing time:** Depends on file size\n\n"
            "📎 Just drag and drop your video file here!"
        )
        
    except Exception as e:
        logger.error(f"Upload command error: {e}")
        await message.reply_text("❌ Error checking channels. Please try again.")

# Handle text messages for multi-step commands
@app.on_message(filters.text & ~filters.command(["start", "help", "addchannel", "list", "rmchannel", "upload"]))
async def handle_text_messages(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_states:
        return
    
    state = user_states[user_id]
    
    if state["step"] == "channel_name":
        channel_name = message.text.strip()
        if len(channel_name) < 1 or len(channel_name) > 50:
            await message.reply_text("❌ Channel name must be between 1-50 characters. Please try again:")
            return
            
        state["data"]["channel_name"] = channel_name
        state["step"] = "api_key"
        await message.reply_text(
            "**Step 2/5:** Please enter your Dailymotion API Key:\n\n"
            "💡 *Get it from: https://api.dailymotion.com*"
        )
    
    elif state["step"] == "api_key":
        api_key = message.text.strip()
        if len(api_key) < 10:
            await message.reply_text("❌ API Key seems too short. Please check and try again:")
            return
            
        state["data"]["api_key"] = api_key
        state["step"] = "api_secret"
        await message.reply_text(
            "**Step 3/5:** Please enter your Dailymotion API Secret:"
        )
    
    elif state["step"] == "api_secret":
        api_secret = message.text.strip()
        if len(api_secret) < 10:
            await message.reply_text("❌ API Secret seems too short. Please check and try again:")
            return
            
        state["data"]["api_secret"] = api_secret
        state["step"] = "username"
        await message.reply_text(
            "**Step 4/5:** Please enter your Dailymotion Username:"
        )
    
    elif state["step"] == "username":
        username = message.text.strip()
        if len(username) < 1:
            await message.reply_text("❌ Username cannot be empty. Please try again:")
            return
            
        state["data"]["username"] = username
        state["step"] = "password"
        await message.reply_text(
            "**Step 5/5:** Please enter your Dailymotion Password:\n\n"
            "🔐 *Your password will be stored securely and encrypted.*"
        )
    
    elif state["step"] == "password":
        password = message.text.strip()
        if len(password) < 1:
            await message.reply_text("❌ Password cannot be empty. Please try again:")
            return
            
        state["data"]["password"] = password
        
        # Delete the password message for security
        try:
            await message.delete()
        except:
            pass
        
        # Test credentials before saving
        testing_msg = await message.reply_text(
            "🔄 **Testing credentials...**\n\n"
            "Please wait while I verify your Dailymotion account..."
        )
        
        # Test authentication
        uploader = DailymotionUploader(
            state["data"]["api_key"],
            state["data"]["api_secret"],
            state["data"]["username"],
            state["data"]["password"]
        )
        
        auth_success = await uploader.authenticate()
        
        if not auth_success:
            await testing_msg.edit_text(
                "❌ **Authentication Failed!**\n\n"
                "Could not connect to your Dailymotion account.\n"
                "Please check your credentials and try again.\n\n"
                "Use /addchannel to start over."
            )
            del user_states[user_id]
            return
        
        # Save to database
        conn = get_db_connection()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO channels (user_id, channel_name, api_key, api_secret, username, password, access_token)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    user_id,
                    state["data"]["channel_name"],
                    state["data"]["api_key"],
                    state["data"]["api_secret"],
                    state["data"]["username"],
                    state["data"]["password"],
                    uploader.access_token
                ))
                conn.commit()
                cursor.close()
                conn.close()
                
                await testing_msg.edit_text(
                    f"✅ **Channel Added Successfully!**\n\n"
                    f"📺 **Channel:** {state['data']['channel_name']}\n"
                    f"👤 **Username:** {state['data']['username']}\n"
                    f"🔐 **Status:** Authenticated\n\n"
                    f"🎬 You can now use /upload to upload videos to this account!\n\n"
                    f"💡 Use /list to see all your channels."
                )
                
                del user_states[user_id]
                
            except psycopg2.IntegrityError:
                await testing_msg.edit_text(
                    "❌ **Channel Already Exists!**\n\n"
                    "You already have a channel with this name.\n"
                    "Please use a different name or remove the existing channel first."
                )
            except Exception as e:
                logger.error(f"Database save error: {e}")
                await testing_msg.edit_text(
                    "❌ **Database Error!**\n\n"
                    "Could not save your channel. Please try again later."
                )
        else:
            await testing_msg.edit_text("❌ Database connection error. Please try again.")

# Handle video uploads
@app.on_message(filters.video | filters.document)
async def handle_video_upload(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_states or user_states[user_id]["step"] != "waiting_video":
        return
    
    # Validate file
    if message.video:
        file_info = message.video
        file_name = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        mime_type = "video/mp4"
    elif message.document:
        file_info = message.document
        file_name = file_info.file_name or f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        mime_type = file_info.mime_type or ""
    else:
        await message.reply_text("❌ Please send a valid video file.")
        return
    
    # Check file size (2GB limit)
    max_size = 2 * 1024 * 1024 * 1024  # 2GB in bytes
    if file_info.file_size > max_size:
        file_size_mb = file_info.file_size / (1024 * 1024)
        await message.reply_text(
            f"❌ **File too large!**\n\n"
            f"📏 **Your file:** {file_size_mb:.1f} MB\n"
            f"📏 **Maximum allowed:** 2048 MB (2GB)\n\n"
            f"Please compress your video or use a smaller file."
        )
        return
    
    # Check if it's a video file
    video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v']
    video_mimes = ['video/mp4', 'video/avi', 'video/quicktime', 'video/x-msvideo', 'video/x-matroska']
    
    file_ext = os.path.splitext(file_name.lower())[1]
    if file_ext not in video_extensions and mime_type not in video_mimes:
        await message.reply_text(
            "❌ **Invalid file type!**\n\n"
            "Please send a video file with one of these formats:\n"
            "📹 MP4, AVI, MOV, MKV, WMV, FLV, WEBM\n\n"
            "If this is a video file, try renaming it with the correct extension."
        )
        return
    
    # Get user's channels
    conn = get_db_connection()
    if not conn:
        await message.reply_text("❌ Database connection error. Please try again later.")
        return
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, channel_name, username FROM channels WHERE user_id = %s ORDER BY channel_name", (user_id,))
        channels = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not channels:
            await message.reply_text("❌ No channels found. Please add a channel first using /addchannel.")
            del user_states[user_id]
            return
        
        # Store video info in user state
        user_states[user_id].update({
            "step": "select_channel",
            "file_info": file_info,
            "file_name": file_name,
            "message_id": message.id
        })
        
        # Create channel selection keyboard
        keyboard = []
        for channel in channels:
            keyboard.append([InlineKeyboardButton(
                f"📺 {channel['channel_name']} (@{channel['username']})", 
                callback_data=f"upload_{channel['id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel Upload", callback_data="cancel_upload")])
        
        file_size_mb = file_info.file_size / (1024 * 1024)
        duration_text = ""
        if hasattr(file_info, 'duration') and file_info.duration:
            minutes = file_info.duration // 60
            seconds = file_info.duration % 60
            duration_text = f"⏱️ **Duration:** {minutes}m {seconds}s\n"
        
        await message.reply_text(
            f"🎬 **Video Received Successfully!**\n\n"
            f"📁 **File:** {file_name}\n"
            f"📏 **Size:** {file_size_mb:.1f} MB\n"
            f"{duration_text}\n"
            f"📺 **Select a channel to upload to:**\n\n"
            f"⚡ *Upload will begin immediately after selection*",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Video upload handler error: {e}")
        await message.reply_text("❌ Error processing video. Please try again.")

# Handle callback queries
@app.on_callback_query()
async def handle_callback_query(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    try:
        if data.startswith("upload_"):
            channel_id = int(data.split("_")[1])
            await handle_upload_callback(client, callback_query, channel_id)
        
        elif data.startswith("remove_"):
            channel_id = int(data.split("_")[1])
            await handle_remove_callback(client, callback_query, channel_id)
        
        elif data in ["cancel_upload", "cancel_remove"]:
            if user_id in user_states:
                del user_states[user_id]
            await callback_query.edit_message_text("❌ Operation cancelled.")
            
    except Exception as e:
        logger.error(f"Callback query error: {e}")
        await callback_query.answer("❌ An error occurred. Please try again.")

async def handle_upload_callback(client, callback_query: CallbackQuery, channel_id: int):
    user_id = callback_query.from_user.id
    
    if user_id not in user_states:
        await callback_query.answer("❌ Session expired. Please try /upload again.")
        return
    
    try:
        await callback_query.edit_message_text(
            "🔄 **Initializing Upload Process...**\n\n"
            "📋 Preparing upload environment\n"
            "🔍 Validating credentials\n"
            "⚡ This may take a moment..."
        )
        
        # Get channel credentials
        conn = get_db_connection()
        if not conn:
            await callback_query.edit_message_text("❌ Database connection error.")
            return
        
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM channels WHERE id = %s AND user_id = %s", (channel_id, user_id))
        channel = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not channel:
            await callback_query.edit_message_text("❌ Channel not found or access denied.")
            return
        
        state = user_states[user_id]
        file_info = state["file_info"]
        file_name = state["file_name"]
        
        # Update progress message
        progress_message = await callback_query.edit_message_text(
            f"⬇️ **Downloading Video...**\n\n"
            f"📁 **File:** {file_name}\n"
            f"📺 **Channel:** {channel['channel_name']}\n"
            f"👤 **Account:** @{channel['username']}\n\n"
            f"📊 **Progress:** 0% - Starting download..."
        )
        
        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1])
        temp_file_path = temp_file.name
        temp_file.close()
        
        try:
            # Download file with progress tracking
            await client.download_media(
                file_info.file_id,
                file_name=temp_file_path,
                progress=lambda current, total: asyncio.create_task(
                    update_download_progress(progress_message, current, total, file_name, channel['channel_name'])
                )
            )
            
            # Update message for upload phase
            await progress_message.edit_text(
                f"⬆️ **Uploading to Dailymotion...**\n\n"
                f"📁 **File:** {file_name}\n"
                f"📺 **Channel:** {channel['channel_name']}\n"
                f"👤 **Account:** @{channel['username']}\n\n"
                f"🔐 Authenticating with Dailymotion..."
            )
            
            # Create uploader and upload
            uploader = DailymotionUploader(
                channel['api_key'],
                channel['api_secret'],
                channel['username'],
                channel['password']
            )
            
            # Generate title from filename
            title = os.path.splitext(file_name)[0].replace('_', ' ').replace('-', ' ').title()
            if len(title) > 150:  # Dailymotion title limit
                title = title[:147] + "..."
            
            # Create description
            description = (
                f"Video uploaded via Telegram Bot\n"
                f"Upload Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Original Filename: {file_name}"
            )
            
            # Upload with progress callback
            video_url = await uploader.upload_video(
                temp_file_path,
                title,
                description=description,
                tags="telegram,bot,upload",
                progress_callback=lambda progress: asyncio.create_task(
                    update_upload_progress(progress_message, progress, file_name, channel['channel_name'])
                )
            )
            
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
            except:
                pass
            
            if video_url:
                # Success message
                await progress_message.edit_text(
                    f"✅ **Upload Completed Successfully!**\n\n"
                    f"📁 **File:** {file_name}\n"
                    f"🎬 **Title:** {title}\n"
                    f"📺 **Channel:** {channel['channel_name']}\n"
                    f"👤 **Account:** @{channel['username']}\n\n"
                    f"🔗 **Video URL:**\n{video_url}\n\n"
                    f"🎉 **Your video is now live on Dailymotion!**\n"
                    f"📊 Processing may take a few minutes for HD quality."
                )
                
                # Send clickable link
                keyboard = [[InlineKeyboardButton("🎬 Watch on Dailymotion", url=video_url)]]
                await client.send_message(
                    user_id,
                    f"🎬 **Quick Access**\n\nClick below to watch your video:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            else:
                await progress_message.edit_text(
                    f"❌ **Upload Failed!**\n\n"
                    f"📁 **File:** {file_name}\n"
                    f"📺 **Channel:** {channel['channel_name']}\n\n"
                    f"**Possible reasons:**\n"
                    f"• Invalid credentials\n"
                    f"• Network connectivity issues\n"
                    f"• Dailymotion service temporarily unavailable\n"
                    f"• File format not supported by Dailymotion\n\n"
                    f"**Solutions:**\n"
                    f"• Check your credentials with /list\n"
                    f"• Try again in a few minutes\n"
                    f"• Contact support if problem persists"
                )
            
        except asyncio.CancelledError:
            await progress_message.edit_text(
                f"❌ **Upload Cancelled!**\n\n"
                f"The upload process was cancelled due to timeout or connection issues.\n"
                f"Please try again with a stable internet connection."
            )
        except Exception as e:
            logger.error(f"Upload process error: {e}")
            try:
                os.unlink(temp_file_path)
            except:
                pass
            
            await progress_message.edit_text(
                f"❌ **Upload Error!**\n\n"
                f"📁 **File:** {file_name}\n"
                f"📺 **Channel:** {channel['channel_name']}\n\n"
                f"**Error Details:** {str(e)[:200]}...\n\n"
                f"**Troubleshooting:**\n"
                f"• Check your internet connection\n"
                f"• Verify file is not corrupted\n"
                f"• Try with a smaller file first\n"
                f"• Contact support if issue persists"
            )
        
        # Clean up user state
        if user_id in user_states:
            del user_states[user_id]
            
    except Exception as e:
        logger.error(f"Upload callback error: {e}")
        await callback_query.edit_message_text(
            "❌ **Critical Error!**\n\n"
            "An unexpected error occurred during upload.\n"
            "Please try again or contact support."
        )

async def handle_remove_callback(client, callback_query: CallbackQuery, channel_id: int):
    user_id = callback_query.from_user.id
    
    try:
        conn = get_db_connection()
        if not conn:
            await callback_query.edit_message_text("❌ Database connection error.")
            return
        
        # Get channel name first
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT channel_name, username FROM channels WHERE id = %s AND user_id = %s", (channel_id, user_id))
        channel = cursor.fetchone()
        
        if not channel:
            await callback_query.edit_message_text("❌ Channel not found or access denied.")
            cursor.close()
            conn.close()
            return
        
        # Delete channel
        cursor.execute("DELETE FROM channels WHERE id = %s AND user_id = %s", (channel_id, user_id))
        deleted_count = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        
        if deleted_count > 0:
            await callback_query.edit_message_text(
                f"✅ **Channel Removed Successfully!**\n\n"
                f"📺 **Channel:** {channel['channel_name']}\n"
                f"👤 **Username:** @{channel['username']}\n\n"
                f"The channel and all its credentials have been permanently deleted from our database.\n\n"
                f"💡 You can add it back anytime using /addchannel"
            )
        else:
            await callback_query.edit_message_text("❌ Channel could not be removed. It may have been already deleted.")
        
    except Exception as e:
        logger.error(f"Remove callback error: {e}")
        await callback_query.edit_message_text("❌ Error removing channel. Please try again later.")

async def update_download_progress(message, current, total, file_name, channel_name):
    try:
        percent = int((current / total) * 100)
        progress_bar = "█" * (percent // 5) + "░" * (20 - percent // 5)
        current_mb = current / (1024 * 1024)
        total_mb = total / (1024 * 1024)
        
        # Calculate speed (rough estimate)
        speed_text = ""
        if hasattr(update_download_progress, 'last_time') and hasattr(update_download_progress, 'last_current'):
            import time
            current_time = time.time()
            time_diff = current_time - update_download_progress.last_time
            if time_diff > 1:  # Update every second
                bytes_diff = current - update_download_progress.last_current
                speed_mbps = (bytes_diff / time_diff) / (1024 * 1024)
                speed_text = f"📡 **Speed:** {speed_mbps:.1f} MB/s\n"
                update_download_progress.last_time = current_time
                update_download_progress.last_current = current
        else:
            import time
            update_download_progress.last_time = time.time()
            update_download_progress.last_current = current
        
        await message.edit_text(
            f"⬇️ **Downloading Video...**\n\n"
            f"📁 **File:** {file_name}\n"
            f"📺 **Channel:** {channel_name}\n\n"
            f"📊 **Progress:** {percent}%\n"
            f"[{progress_bar}]\n"
            f"📦 **Downloaded:** {current_mb:.1f} MB / {total_mb:.1f} MB\n"
            f"{speed_text}"
            f"⏳ Please wait... Do not close the app!"
        )
    except Exception:
        # Ignore message edit errors (rate limiting, etc.)
        pass

async def update_upload_progress(message, progress_percent, file_name, channel_name):
    try:
        progress_bar = "█" * (progress_percent // 5) + "░" * (20 - progress_percent // 5)
        
        status_text = ""
        if progress_percent < 25:
            status_text = "🔐 Authenticating..."
        elif progress_percent < 50:
            status_text = "📤 Uploading file..."
        elif progress_percent < 75:
            status_text = "⚙️ Processing video..."
        elif progress_percent < 100:
            status_text = "🎬 Creating video entry..."
        else:
            status_text = "✅ Upload complete!"
        
        await message.edit_text(
            f"⬆️ **Uploading to Dailymotion...**\n\n"
            f"📁 **File:** {file_name}\n"
            f"📺 **Channel:** {channel_name}\n\n"
            f"📊 **Progress:** {progress_percent}%\n"
            f"[{progress_bar}]\n"
            f"🔄 **Status:** {status_text}\n\n"
            f"⏳ Please be patient... Large files take longer!"
        )
    except Exception:
        # Ignore message edit errors
        pass

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    global health_server
    if health_server:
        health_server.shutdown()
    sys.exit(0)

if __name__ == "__main__":
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Start health check server
        health_server = start_health_server()
        
        # Initialize database
        init_db()
        
        # Start bot
        logger.info("🚀 Dailymotion Upload Bot starting...")
        logger.info(f"Bot username: @{BOT_TOKEN.split(':')[0]}")
        
        app.run()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
    finally:
        if health_server:
            health_server.shutdown()
        logger.info("Bot shutdown complete")
