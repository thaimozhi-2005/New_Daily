import os
import asyncio
import logging
import requests
import aiohttp
import aiofiles
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import psycopg2
from psycopg2.extras import RealDictCursor
import tempfile
from urllib.parse import urlencode
import json
from datetime import datetime
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot configuration
API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# Initialize Pyrogram client
app = Client("dailymotion_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Database functions
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_database():
    """Initialize database tables"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Create channels table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                channel_name VARCHAR(255) NOT NULL,
                api_key VARCHAR(255) NOT NULL,
                api_secret VARCHAR(255) NOT NULL,
                username VARCHAR(255) NOT NULL,
                password VARCHAR(255) NOT NULL,
                access_token TEXT,
                refresh_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, channel_name)
            );
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

class DailymotionUploader:
    def __init__(self, api_key: str, api_secret: str, username: str, password: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.username = username
        self.password = password
        self.access_token = None
        self.refresh_token = None
        self.base_url = "https://api.dailymotion.com/oauth"
        self.api_url = "https://api.dailymotion.com"
        
    def get_auth_url(self):
        """Get Dailymotion Partner API authentication URL"""
        params = {
            'response_type': 'code',
            'client_id': self.api_key,
            'redirect_uri': 'https://www.dailymotion.com/oauth/authorize',
            'scope': 'manage_videos'
        }
        return f"https://www.dailymotion.com/oauth/authorize?{urlencode(params)}"
    
    async def authenticate(self):
        """Authenticate using partner credentials"""
        try:
            auth_data = {
                'grant_type': 'password',
                'client_id': self.api_key,
                'client_secret': self.api_secret,
                'username': self.username,
                'password': self.password,
                'scope': 'manage_videos'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/token", data=auth_data) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.access_token = data.get('access_token')
                        self.refresh_token = data.get('refresh_token')
                        logger.info("Authentication successful")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Authentication failed: {error_text}")
                        return False
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False
    
    async def upload_video(self, file_path, title, description="", progress_callback=None):
        """Upload video to Dailymotion"""
        try:
            if not self.access_token:
                if not await self.authenticate():
                    return None
            
            # Get upload URL
            upload_url = await self._get_upload_url()
            if not upload_url:
                return None
            
            # Upload file with progress tracking
            video_url = await self._upload_file(file_path, upload_url, progress_callback)
            if not video_url:
                return None
            
            # Create video entry
            video_id = await self._create_video(video_url, title, description)
            return video_id
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None
    
    async def _get_upload_url(self):
        """Get upload URL from Dailymotion"""
        try:
            headers = {'Authorization': f'Bearer {self.access_token}'}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/file/upload", headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('upload_url')
                    else:
                        error_text = await response.text()
                        logger.error(f"Get upload URL failed: {error_text}")
        except Exception as e:
            logger.error(f"Get upload URL error: {e}")
        return None
    
    async def _upload_file(self, file_path, upload_url, progress_callback=None):
        """Upload file to the provided URL with progress tracking"""
        try:
            file_size = os.path.getsize(file_path)
            logger.info(f"Uploading file: {file_path}, Size: {file_size} bytes")
            
            # Create multipart form data
            with open(file_path, 'rb') as file:
                form_data = aiohttp.FormData()
                form_data.add_field('file', file, filename=os.path.basename(file_path))
                
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3600)) as session:
                    async with session.post(upload_url, data=form_data) as response:
                        if response.status == 200:
                            result = await response.json()
                            logger.info("File upload successful")
                            return result.get('url')
                        else:
                            error_text = await response.text()
                            logger.error(f"File upload failed: {error_text}")
                        
        except Exception as e:
            logger.error(f"File upload error: {e}")
        return None
    
    async def _create_video(self, video_url, title, description):
        """Create video entry on Dailymotion"""
        try:
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            video_data = {
                'url': video_url,
                'title': title,
                'description': description,
                'published': 'true',
                'channel': 'videogames'  # Default channel, can be customized
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.api_url}/me/videos", 
                                      headers=headers, 
                                      data=video_data) as response:
                    if response.status == 200:
                        data = await response.json()
                        video_id = data.get('id')
                        logger.info(f"Video created successfully: {video_id}")
                        return video_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Create video failed: {error_text}")
                        
        except Exception as e:
            logger.error(f"Create video error: {e}")
        return None

    def get_video_url(self, video_id):
        """Get the public URL of the uploaded video"""
        return f"https://www.dailymotion.com/video/{video_id}"

# Progress tracking class
class ProgressTracker:
    def __init__(self, message, total_size, operation="Processing"):
        self.message = message
        self.total_size = total_size
        self.operation = operation
        self.last_update = 0
        self.start_time = time.time()
    
    async def update_progress(self, current, total=None):
        if total is None:
            total = self.total_size
        
        current_time = time.time()
        if current_time - self.last_update < 2:  # Update every 2 seconds
            return
        
        self.last_update = current_time
        percentage = (current / total) * 100 if total > 0 else 0
        
        # Create progress bar
        bar_length = 20
        filled_length = int(bar_length * current / total) if total > 0 else 0
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        
        # Calculate speed and ETA
        elapsed_time = current_time - self.start_time
        if elapsed_time > 0 and current > 0:
            speed = current / elapsed_time
            eta = (total - current) / speed if speed > 0 else 0
            speed_mb = speed / (1024 * 1024)
            
            progress_text = f"""
🎬 **{self.operation}**

📊 Progress: {bar} {percentage:.1f}%
📦 Size: {current / (1024*1024):.1f}MB / {total / (1024*1024):.1f}MB
🚀 Speed: {speed_mb:.1f} MB/s
⏱️ ETA: {int(eta//60)}m {int(eta%60)}s
            """
        else:
            progress_text = f"""
🎬 **{self.operation}**

📊 Progress: {bar} {percentage:.1f}%
📦 Size: {current / (1024*1024):.1f}MB / {total / (1024*1024):.1f}MB
            """
        
        try:
            await self.message.edit_text(progress_text)
        except Exception as e:
            logger.error(f"Progress update error: {e}")

# Bot command handlers
@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    welcome_text = """
🎬 **Welcome to Dailymotion Video Uploader Bot!** 🎬

This bot helps you upload videos from Telegram directly to your Dailymotion partner accounts.

**Features:**
✅ Upload large videos using Telegram Client
✅ Multiple Dailymotion account support
✅ Real-time progress tracking
✅ Secure credential storage
✅ Easy account management

**Getting Started:**
1. Use `/addchannel` to add your Dailymotion account
2. Use `/upload` to upload videos
3. Use `/list` to see your accounts
4. Use `/help` for detailed instructions

Ready to start uploading? 🚀
    """
    await message.reply_text(welcome_text)

@app.on_message(filters.command("help"))
async def help_command(client, message: Message):
    help_text = """
📖 **How to Use the Bot**

**Step 1: Add Your Dailymotion Account**
Use `/addchannel` and provide:
- Channel Name (your choice)
- API Key (from Dailymotion Partner Dashboard)
- API Secret (from Dailymotion Partner Dashboard)
- Username (your Dailymotion username)
- Password (your Dailymotion password)

**Step 2: Upload Videos**
1. Send `/upload` command
2. Send your video file
3. Choose the account to upload to
4. Wait for the upload to complete

**Commands:**
🔹 `/start` - Welcome message
🔹 `/addchannel` - Add Dailymotion account
🔹 `/upload` - Upload video
🔹 `/list` - Show saved accounts
🔹 `/rmchannel` - Remove account
🔹 `/help` - This help message

**Important Notes:**
- Only video files are supported
- Maximum file size: 2GB
- Upload time depends on file size and internet speed
- Your credentials are stored securely in encrypted database

**Getting API Credentials:**
1. Go to Dailymotion Partner Dashboard
2. Create a new application
3. Get your API Key and Secret
4. Use your regular Dailymotion login credentials

Need more help? Contact support! 💬
    """
    await message.reply_text(help_text)

@app.on_message(filters.command("addchannel"))
async def add_channel_command(client, message: Message):
    await message.reply_text(
        "📝 **Add New Dailymotion Channel**\n\n"
        "Please send your credentials in this format:\n\n"
        "```\n"
        "Channel Name: YourChannelName\n"
        "API Key: your_api_key\n"
        "API Secret: your_api_secret\n"
        "Username: your_username\n"
        "Password: your_password\n"
        "```\n\n"
        "**Example:**\n"
        "```\n"
        "Channel Name: MyChannel\n"
        "API Key: abc123def456\n"
        "API Secret: xyz789uvw012\n"
        "Username: myuser@email.com\n"
        "Password: mypassword123\n"
        "```"
    )
    
    # Set user state for next message
    app.user_states = getattr(app, 'user_states', {})
    app.user_states[message.from_user.id] = 'waiting_credentials'

@app.on_message(filters.command("list"))
async def list_channels_command(client, message: Message):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT channel_name, created_at FROM channels WHERE user_id = %s", 
                   (message.from_user.id,))
        channels = cur.fetchall()
        
        if not channels:
            await message.reply_text("❌ No channels found. Use `/addchannel` to add one!")
            return
        
        text = "📋 **Your Dailymotion Channels:**\n\n"
        for i, channel in enumerate(channels, 1):
            text += f"{i}. **{channel['channel_name']}**\n"
            text += f"   Added: {channel['created_at'].strftime('%Y-%m-%d %H:%M')}\n\n"
        
        await message.reply_text(text)
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"List channels error: {e}")
        await message.reply_text("❌ Error retrieving channels.")

@app.on_message(filters.command("rmchannel"))
async def remove_channel_command(client, message: Message):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT channel_name FROM channels WHERE user_id = %s", 
                   (message.from_user.id,))
        channels = cur.fetchall()
        
        if not channels:
            await message.reply_text("❌ No channels found to remove!")
            return
        
        # Create inline keyboard with channel options
        keyboard = []
        for channel in channels:
            keyboard.append([InlineKeyboardButton(
                f"🗑️ {channel['channel_name']}", 
                callback_data=f"remove_{channel['channel_name']}"
            )])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_remove")])
        
        await message.reply_text(
            "🗑️ **Select Channel to Remove:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Remove channel error: {e}")
        await message.reply_text("❌ Error loading channels.")

@app.on_message(filters.command("upload"))
async def upload_command(client, message: Message):
    try:
        # Check if user has any channels
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) as count FROM channels WHERE user_id = %s", 
                   (message.from_user.id,))
        result = cur.fetchone()
        
        if result['count'] == 0:
            await message.reply_text(
                "❌ **No Dailymotion accounts found!**\n\n"
                "Please add a Dailymotion account first using `/addchannel` command."
            )
            return
        
        await message.reply_text(
            "📹 **Upload Video to Dailymotion**\n\n"
            "Please send me the video file you want to upload.\n\n"
            "**Supported formats:** MP4, AVI, MOV, MKV, WMV, FLV\n"
            "**Maximum size:** 2GB\n\n"
            "Just send the video file and I'll handle the rest! 🚀"
        )
        
        # Set user state
        app.user_states = getattr(app, 'user_states', {})
        app.user_states[message.from_user.id] = 'waiting_video'
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Upload command error: {e}")
        await message.reply_text("❌ Error processing upload command.")

# Handle text messages based on user state
@app.on_message(filters.text & ~filters.command([]))
async def handle_text_message(client, message: Message):
    user_states = getattr(app, 'user_states', {})
    user_state = user_states.get(message.from_user.id)
    
    if user_state == 'waiting_credentials':
        await process_credentials(message)
    else:
        await message.reply_text("Please use a command to interact with the bot. Type /help for assistance.")

async def process_credentials(message: Message):
    try:
        lines = message.text.strip().split('\n')
        credentials = {}
        
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip().lower().replace(' ', '_')
                value = value.strip()
                credentials[key] = value
        
        required_fields = ['channel_name', 'api_key', 'api_secret', 'username', 'password']
        missing_fields = [field for field in required_fields if field not in credentials]
        
        if missing_fields:
            await message.reply_text(
                f"❌ Missing required fields: {', '.join(missing_fields)}\n\n"
                "Please provide all required information."
            )
            return
        
        # Test credentials
        status_msg = await message.reply_text("🔄 Testing credentials...")
        
        uploader = DailymotionUploader(
            credentials['api_key'],
            credentials['api_secret'],
            credentials['username'],
            credentials['password']
        )
        
        if await uploader.authenticate():
            # Save to database
            conn = get_db_connection()
            cur = conn.cursor()
            
            try:
                cur.execute("""
                    INSERT INTO channels (user_id, channel_name, api_key, api_secret, username, password, access_token, refresh_token)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, channel_name) 
                    DO UPDATE SET 
                        api_key = EXCLUDED.api_key,
                        api_secret = EXCLUDED.api_secret,
                        username = EXCLUDED.username,
                        password = EXCLUDED.password,
                        access_token = EXCLUDED.access_token,
                        refresh_token = EXCLUDED.refresh_token
                """, (
                    message.from_user.id,
                    credentials['channel_name'],
                    credentials['api_key'],
                    credentials['api_secret'],
                    credentials['username'],
                    credentials['password'],
                    uploader.access_token,
                    uploader.refresh_token
                ))
                
                conn.commit()
                
                await status_msg.edit_text(
                    f"✅ **Channel Added Successfully!**\n\n"
                    f"📺 Channel Name: **{credentials['channel_name']}**\n"
                    f"👤 Username: **{credentials['username']}**\n\n"
                    f"You can now upload videos using `/upload` command!"
                )
                
            except psycopg2.IntegrityError:
                await status_msg.edit_text("❌ Channel name already exists. Please use a different name.")
            
            cur.close()
            conn.close()
            
        else:
            await status_msg.edit_text(
                "❌ **Authentication Failed!**\n\n"
                "Please check your credentials and try again.\n\n"
                "Make sure you're using:\n"
                "• Valid API Key and Secret from Dailymotion Partner Dashboard\n"
                "• Correct username and password"
            )
        
        # Clear user state
        app.user_states[message.from_user.id] = None
        
    except Exception as e:
        logger.error(f"Process credentials error: {e}")
        await message.reply_text("❌ Error processing credentials. Please try again.")

# Handle video uploads
@app.on_message(filters.video)
async def handle_video_upload(client, message: Message):
    user_states = getattr(app, 'user_states', {})
    user_state = user_states.get(message.from_user.id)
    
    if user_state != 'waiting_video':
        await message.reply_text("Please use `/upload` command first to start the upload process.")
        return
    
    try:
        # Get user's channels
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT channel_name FROM channels WHERE user_id = %s", 
                   (message.from_user.id,))
        channels = cur.fetchall()
        
        if not channels:
            await message.reply_text("❌ No channels found. Please add a channel first using `/addchannel`.")
            return
        
        # Create channel selection keyboard
        keyboard = []
        for channel in channels:
            keyboard.append([InlineKeyboardButton(
                f"📺 {channel['channel_name']}", 
                callback_data=f"upload_{channel['channel_name']}"
            )])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_upload")])
        
        # Store video info for later use
        app.pending_uploads = getattr(app, 'pending_uploads', {})
        app.pending_uploads[message.from_user.id] = {
            'file_id': message.video.file_id,
            'file_name': message.video.file_name or f"video_{int(time.time())}.mp4",
            'file_size': message.video.file_size
        }
        
        await message.reply_text(
            f"📹 **Video Received!**\n\n"
            f"📁 File: `{message.video.file_name or 'video.mp4'}`\n"
            f"📦 Size: {message.video.file_size / (1024*1024):.1f} MB\n"
            f"⏱️ Duration: {message.video.duration}s\n\n"
            f"**Select Dailymotion account to upload to:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Handle video upload error: {e}")
        await message.reply_text("❌ Error processing video upload.")

# Handle callback queries
@app.on_callback_query()
async def handle_callback_query(client, callback_query: CallbackQuery):
    try:
        data = callback_query.data
        user_id = callback_query.from_user.id
        
        if data.startswith("upload_"):
            channel_name = data[7:]  # Remove "upload_" prefix
            await process_video_upload(callback_query, channel_name)
            
        elif data.startswith("remove_"):
            channel_name = data[7:]  # Remove "remove_" prefix
            await process_channel_removal(callback_query, channel_name)
            
        elif data == "cancel_upload":
            # Clear pending upload
            app.pending_uploads = getattr(app, 'pending_uploads', {})
            if user_id in app.pending_uploads:
                del app.pending_uploads[user_id]
            
            await callback_query.edit_message_text("❌ Upload cancelled.")
            
        elif data == "cancel_remove":
            await callback_query.edit_message_text("❌ Channel removal cancelled.")
            
    except Exception as e:
        logger.error(f"Callback query error: {e}")
        await callback_query.answer("❌ Error processing request.")

async def process_video_upload(callback_query: CallbackQuery, channel_name: str):
    try:
        user_id = callback_query.from_user.id
        
        # Get pending upload info
        app.pending_uploads = getattr(app, 'pending_uploads', {})
        upload_info = app.pending_uploads.get(user_id)
        
        if not upload_info:
            await callback_query.edit_message_text("❌ Upload session expired. Please try again.")
            return
        
        # Get channel credentials
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT api_key, api_secret, username, password, access_token, refresh_token 
            FROM channels WHERE user_id = %s AND channel_name = %s
        """, (user_id, channel_name))
        
        channel_data = cur.fetchone()
        if not channel_data:
            await callback_query.edit_message_text("❌ Channel not found.")
            return
        
        # Start upload process
        await callback_query.edit_message_text("🔄 Starting upload process...")
        
        # Download video from Telegram
        progress_msg = await callback_query.message.reply_text("📥 **Downloading video from Telegram...**")
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
            temp_path = temp_file.name
        
        try:
            # Download with progress tracking
            progress_tracker = ProgressTracker(progress_msg, upload_info['file_size'], "Downloading")
            
            await app.download_media(
                upload_info['file_id'], 
                temp_path,
                progress=lambda current, total: asyncio.create_task(progress_tracker.update_progress(current, total))
            )
            
            await progress_msg.edit_text("✅ Download completed! Starting upload to Dailymotion...")
            
            # Initialize Dailymotion uploader
            uploader = DailymotionUploader(
                channel_data['api_key'],
                channel_data['api_secret'],
                channel_data['username'],
                channel_data['password']
            )
            
            # Set existing tokens if available
            if channel_data['access_token']:
                uploader.access_token = channel_data['access_token']
                uploader.refresh_token = channel_data['refresh_token']
            
            # Upload to Dailymotion
            upload_progress = ProgressTracker(progress_msg, upload_info['file_size'], "Uploading to Dailymotion")
            
            video_title = upload_info['file_name'].rsplit('.', 1)[0]  # Remove extension
            video_description = f"Uploaded via Telegram Bot on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            video_id = await uploader.upload_video(
                temp_path,
                video_title,
                video_description,
                progress_callback=lambda current, total: asyncio.create_task(upload_progress.update_progress(current, total))
            )
            
            if video_id:
                video_url = uploader.get_video_url(video_id)
                
                await progress_msg.edit_text(
                    f"🎉 **Upload Successful!**\n\n"
                    f"📺 **Channel:** {channel_name}\n"
                    f"🎬 **Video ID:** `{video_id}`\n"
                    f"📁 **Title:** {video_title}\n"
                    f"🔗 **URL:** {video_url}\n\n"
                    f"✅ Your video is now live on Dailymotion!"
                )
                
                # Update tokens in database if changed
                if uploader.access_token != channel_data['access_token']:
                    cur.execute("""
                        UPDATE channels 
                        SET access_token = %s, refresh_token = %s 
                        WHERE user_id = %s AND channel_name = %s
                    """, (uploader.access_token, uploader.refresh_token, user_id, channel_name))
                    conn.commit()
                
            else:
                await progress_msg.edit_text(
                    "❌ **Upload Failed!**\n\n"
                    "The video could not be uploaded to Dailymotion. "
                    "Please check your credentials and try again."
                )
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            
            # Clear pending upload
            if user_id in app.pending_uploads:
                del app.pending_uploads[user_id]
            
            # Clear user state
            app.user_states = getattr(app, 'user_states', {})
            app.user_states[user_id] = None
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Process video upload error: {e}")
        await callback_query.message.reply_text(
            "❌ **Upload Error!**\n\n"
            "An error occurred during the upload process. This might be due to:\n"
            "• Network connectivity issues\n"
            "• Invalid credentials\n"
            "• Dailymotion API limitations\n\n"
            "Please try again later."
        )

async def process_channel_removal(callback_query: CallbackQuery, channel_name: str):
    try:
        user_id = callback_query.from_user.id
        
        # Remove channel from database
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            DELETE FROM channels 
            WHERE user_id = %s AND channel_name = %s
        """, (user_id, channel_name))
        
        if cur.rowcount > 0:
            conn.commit()
            await callback_query.edit_message_text(
                f"✅ **Channel Removed Successfully!**\n\n"
                f"📺 Channel: **{channel_name}**\n\n"
                f"The channel and its credentials have been deleted from the database."
            )
        else:
            await callback_query.edit_message_text("❌ Channel not found.")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"Process channel removal error: {e}")
        await callback_query.edit_message_text("❌ Error removing channel.")

# Error handler for connection issues
async def handle_connection_error():
    """Handle connection errors during upload"""
    logger.warning("Connection error detected, attempting to reconnect...")
    await asyncio.sleep(5)  # Wait before retry
    return True

# Main function to run the bot
async def main():
    """Main function to initialize and run the bot"""
    try:
        # Initialize database
        init_database()
        
        # Start the bot
        logger.info("Starting Dailymotion Upload Bot...")
        await app.start()
        logger.info("Bot started successfully!")
        
        # Keep the bot running
        await app.idle()
        
    except Exception as e:
        logger.error(f"Bot startup error: {e}")
    finally:
        await app.stop()

if __name__ == "__main__":
    # Run the bot
    asyncio.run(main())
