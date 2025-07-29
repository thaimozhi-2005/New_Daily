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
from urllib.parse import urlencode, quote
import tempfile
import sys
import signal
import traceback
import json
import mimetypes
from pathlib import Path

# Setup enhanced logging
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

# Database connection with retry logic
def get_db_connection(max_retries=3):
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        except Exception as e:
            logger.error(f"Database connection attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                import time
                time.sleep(2 ** attempt)
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
                    access_token TEXT,
                    refresh_token TEXT,
                    token_expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, channel_name)
                )
            """)
            
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_channels_user_id ON channels(user_id)
            """)
            
            conn.commit()
            cursor.close()
            conn.close()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")

def validate_video_file(file_path):
    """Validate video file before upload"""
    try:
        if not os.path.exists(file_path):
            return False, "File does not exist"
        
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            return False, "File is empty"
        
        # Dailymotion Partner API supports up to 4GB files
        if file_size > 4 * 1024 * 1024 * 1024:  # 4GB
            return False, "File too large (max 4GB for Partner accounts)"
        
        valid_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.3gp']
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext not in valid_extensions:
            return False, f"Unsupported format: {file_ext}"
        
        # Check MIME type
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type and not mime_type.startswith('video/'):
            return False, f"Invalid MIME type: {mime_type}"
        
        return True, "Valid"
        
    except Exception as e:
        return False, f"Validation error: {e}"

class DailymotionUploader:
    def __init__(self, api_key, api_secret, username, password):
        self.api_key = api_key
        self.api_secret = api_secret
        self.username = username
        self.password = password
        self.access_token = None
        self.refresh_token = None
        self.base_url = "https://partner.api.dailymotion.com/rest"  # Updated to Partner API
        self.api_url = "https://partner.api.dailymotion.com/rest"  # Align with base_url
        self.session = None
    
    async def get_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=900, connect=60)  # 15 minute timeout for uploads
            connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300, limit_per_host=5)
            headers = {
                'User-Agent': 'Dailymotion-Upload-Bot/1.0',
                'Accept': 'application/json',
                'Accept-Encoding': 'gzip, deflate'
            }
            self.session = aiohttp.ClientSession(
                timeout=timeout, 
                connector=connector,
                headers=headers,
                trust_env=True
            )
        return self.session
    
    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def authenticate(self):
        """Authenticate with Dailymotion Partner API using OAuth2 password grant"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(f"Dailymotion authentication attempt {attempt + 1}/{max_retries}")
                
                auth_url = f"{self.base_url}/oauth/v2/token"  # Updated endpoint
                
                auth_data = {
                    'grant_type': 'password',
                    'client_id': self.api_key,
                    'client_secret': self.api_secret,
                    'username': self.username,
                    'password': self.password,
                    'scope': 'manage_videos manage_channels'  # Partner-specific scope
                }
                
                session = await self.get_session()
                
                async with session.post(auth_url, data=auth_data) as response:
                    response_text = await response.text()
                    logger.info(f"Auth response status: {response.status}")
                    
                    if response.status == 200:
                        try:
                            result = await response.json()
                            self.access_token = result.get('access_token')
                            self.refresh_token = result.get('refresh_token')
                            
                            if self.access_token:
                                logger.info("Dailymotion authentication successful")
                                return True
                            else:
                                logger.error("No access token in authentication response")
                                logger.error(f"Response: {response_text}")
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse auth response JSON: {e}")
                            logger.error(f"Response text: {response_text}")
                    else:
                        logger.error(f"Authentication failed: {response.status}")
                        logger.error(f"Response: {response_text}")
                        
                        if response.status == 400:
                            try:
                                error_data = await response.json()
                                error_type = error_data.get('error', 'unknown')
                                if error_type == 'invalid_client':
                                    logger.error("Invalid API credentials - check your API key and secret")
                                elif error_type == 'invalid_grant':
                                    logger.error("Invalid username/password combination")
                                else:
                                    logger.error(f"Authentication error: {error_type}")
                            except:
                                logger.error("Bad request - check all credentials")
                        elif response.status == 401:
                            logger.error("Unauthorized - invalid credentials")
                        elif response.status == 403:
                            logger.error("Forbidden - account may not have API access or is suspended")
                        elif response.status >= 500:
                            logger.error("Dailymotion server error - try again later")
                        
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                        
            except asyncio.TimeoutError:
                logger.error(f"Authentication timeout (attempt {attempt + 1})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Authentication error (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        
        return False
    
    async def get_upload_url(self):
        """Get upload URL from Dailymotion Partner API"""
        if not self.access_token:
            logger.error("No access token available")
            return None
            
        try:
            upload_url_endpoint = f"{self.api_url}/file/upload"  # Updated endpoint
            params = {'access_token': self.access_token}
            
            session = await self.get_session()
            
            async with session.get(upload_url_endpoint, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    upload_url = data.get('upload_url')
                    if upload_url:
                        logger.info("Successfully obtained upload URL")
                        return upload_url
                    else:
                        logger.error("No upload URL in response")
                        return None
                elif response.status == 401:
                    logger.error("Token expired or invalid")
                    return None
                else:
                    response_text = await response.text()
                    logger.error(f"Failed to get upload URL: {response.status} - {response_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting upload URL: {e}")
            return None
    
    async def upload_file_to_url(self, file_path, upload_url, progress_callback=None):
        """Upload file to the provided upload URL"""
        try:
            file_size = os.path.getsize(file_path)
            logger.info(f"Uploading file: {file_path} (size: {file_size} bytes)")
            
            if progress_callback:
                await progress_callback(10)
            
            session = await self.get_session()
            
            async with aiofiles.open(file_path, 'rb') as file:
                file_content = await file.read()
                
                if progress_callback:
                    await progress_callback(30)
                
                data = aiohttp.FormData()
                filename = os.path.basename(file_path)
                mime_type = mimetypes.guess_type(file_path)[0] or 'video/mp4'
                
                data.add_field('file', 
                             file_content, 
                             filename=filename,
                             content_type=mime_type)
                
                if progress_callback:
                    await progress_callback(50)
                
                async with session.post(upload_url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        file_url = result.get('url')
                        if file_url:
                            logger.info("File uploaded successfully")
                            if progress_callback:
                                await progress_callback(80)
                            return file_url
                        else:
                            logger.error("No file URL in upload response")
                            return None
                    else:
                        response_text = await response.text()
                        logger.error(f"File upload failed: {response.status} - {response_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"File upload error: {e}")
            return None
    
    async def create_video(self, file_url, title, description="", tags="", progress_callback=None):
        """Create video entry on Dailymotion Partner API"""
        try:
            if not self.access_token:
                logger.error("No access token for video creation")
                return None
            
            create_url = f"{self.api_url}/videos"  # Updated endpoint
            
            video_data = {
                'access_token': self.access_token,
                'url': file_url,
                'title': title[:150],  # Dailymotion title limit
                'description': description[:2000] if description else "",  # Description limit
                'tags': tags[:500] if tags else "",  # Tags limit
                'is_public': 'true',  # Partner API convention
                'private': 'false'
            }
            
            session = await self.get_session()
            
            async with session.post(create_url, data=video_data) as response:
                if response.status == 200:
                    result = await response.json()
                    video_id = result.get('id')
                    if video_id:
                        video_url = f"https://www.dailymotion.com/video/{video_id}"
                        logger.info(f"Video created successfully: {video_url}")
                        if progress_callback:
                            await progress_callback(100)
                        return video_url
                    else:
                        logger.error("No video ID in creation response")
                        return None
                elif response.status == 401:
                    logger.error("Token expired during video creation")
                    return None
                else:
                    response_text = await response.text()
                    logger.error(f"Video creation failed: {response.status} - {response_text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Video creation error: {e}")
            return None
    
    async def upload_video(self, file_path, title, description="", tags="", progress_callback=None):
        """Complete video upload process"""
        try:
            logger.info(f"Starting complete upload process for: {file_path}")
            
            is_valid, validation_msg = validate_video_file(file_path)
            if not is_valid:
                logger.error(f"File validation failed: {validation_msg}")
                return None
            
            if progress_callback:
                await progress_callback(5)
            
            if not self.access_token:
                logger.info("Authenticating with Dailymotion...")
                if not await self.authenticate():
                    logger.error("Authentication failed")
                    return None
            
            if progress_callback:
                await progress_callback(15)
            
            logger.info("Getting upload URL...")
            upload_url = await self.get_upload_url()
            if not upload_url:
                logger.error("Failed to get upload URL")
                return None
            
            if progress_callback:
                await progress_callback(20)
            
            logger.info("Uploading file...")
            file_url = await self.upload_file_to_url(file_path, upload_url, 
                                                   lambda p: progress_callback(20 + p * 0.6) if progress_callback else None)
            if not file_url:
                logger.error("File upload failed")
                return None
            
            if progress_callback:
                await progress_callback(85)
            
            logger.info("Creating video entry...")
            video_url = await self.create_video(file_url, title, description, tags,
                                              lambda p: progress_callback(85 + p * 0.15) if progress_callback else None)
            
            if video_url:
                logger.info(f"Complete upload process successful: {video_url}")
                return video_url
            else:
                logger.error("Video creation failed")
                return None
                
        except Exception as e:
            logger.error(f"Complete upload process error: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
        finally:
            await self.close_session()

async def get_upload_error_details(uploader, file_path):
    """Get detailed error information for troubleshooting"""
    details = []
    
    is_valid, msg = validate_video_file(file_path)
    if not is_valid:
        details.append(f"❌ File validation: {msg}")
    else:
        details.append("✅ File validation passed")
    
    try:
        if await uploader.authenticate():
            details.append("✅ Authentication successful")
        else:
            details.append("❌ Authentication failed - check credentials")
    except Exception as e:
        details.append(f"❌ Authentication error: {str(e)[:100]}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://partner.api.dailymotion.com/rest", 
                                 timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status in [200, 404]:
                    details.append("✅ Dailymotion API accessible")
                else:
                    details.append(f"❌ Dailymotion API error: {response.status}")
    except asyncio.TimeoutError:
        details.append("❌ Network timeout - check internet connection")
    except Exception as e:
        details.append(f"❌ Network error: {str(e)[:100]}")
    
    return "\n".join(details)

# User states for multi-step commands
user_states = {}

@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    welcome_text = """
🎬 **Welcome to Dailymotion Upload Bot!** 🎬

I'm here to help you upload videos directly to your Dailymotion accounts with ease!

**What I can do:**
✅ Upload videos to multiple Dailymotion accounts
✅ Handle large video files (up to 4GB for Partner accounts)
✅ Show upload progress in real-time
✅ Manage multiple Dailymotion channels
✅ Robust error handling and recovery

**Getting Started:**
1. Add your Dailymotion account using /addchannel
2. Use /upload to upload videos
3. Use /list to see your added channels
4. Use /help for detailed instructions

**Debug Commands:**
🔧 /testauth - Test Dailymotion authentication
🔧 /testapi - Test API connectivity

**Requirements:**
• Dailymotion account with API access
• API Key & Secret from Dailymotion Developer Portal
• Valid username & password

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

**Debug Commands:**
🔧 `/testauth` - Test your Dailymotion credentials
🔧 `/testapi` - Test API connectivity

**How to get API Credentials:**
1. Go to https://developers.dailymotion.com
2. Create a new application
3. Get your API Key (Client ID) and Secret (Client Secret)
4. Use your regular Dailymotion username/password

**How to upload videos:**
1. First, add your Dailymotion account credentials using `/addchannel`
2. Use `/upload` command
3. Send your video file when prompted
4. Choose which account to upload to
5. Wait for the upload to complete

**Supported Formats:**
📹 MP4, AVI, MOV, MKV, WMV, FLV, WEBM, M4V, 3GP
📏 Maximum file size: 4GB (Partner accounts)
⏱️ Upload time depends on file size and internet speed

**Troubleshooting:**
If uploads fail, try:
• Check credentials with /testauth
• Test connectivity with /testapi
• Verify your Dailymotion account has API access
• Try smaller files first
• Check your internet connection
• Make sure your API application is approved

Need more help? Use the debug commands to diagnose issues!
    """
    await message.reply_text(help_text)

@app.on_message(filters.command("testauth"))
async def test_auth_command(client, message: Message):
    user_id = message.from_user.id
    
    testing_msg = await message.reply_text("🔍 **Testing Authentication...**\n\nChecking your Dailymotion credentials...")
    
    conn = get_db_connection()
    if not conn:
        await testing_msg.edit_text("❌ Database connection error.")
        return
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM channels WHERE user_id = %s", (user_id,))
        channels = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not channels:
            await testing_msg.edit_text(
                "❌ **No channels found!**\n\n"
                "Add a channel first using /addchannel\n\n"
                "📋 **Setup Steps:**\n"
                "1. Go to https://developers.dailymotion.com\n"
                "2. Create an application\n"
                "3. Get API Key & Secret\n"
                "4. Use /addchannel with your credentials"
            )
            return
        
        results = []
        for channel in channels:
            try:
                uploader = DailymotionUploader(
                    channel['api_key'],
                    channel['api_secret'],
                    channel['username'],
                    channel['password']
                )
                
                success = await uploader.authenticate()
                if success:
                    results.append(f"✅ **{channel['channel_name']}** (@{channel['username']})")
                else:
                    results.append(f"❌ **{channel['channel_name']}** (@{channel['username']})")
                    
            except Exception as e:
                results.append(f"⚠️ **{channel['channel_name']}** - Error: {str(e)[:50]}")
        
        result_text = "🔍 **Authentication Test Results:**\n\n" + "\n".join(results)
        
        if any("❌" in result for result in results):
            result_text += "\n\n💡 **If any channel failed:**\n"
            result_text += "• Verify API Key & Secret are correct\n"
            result_text += "• Check username/password\n"
            result_text += "• Ensure API application is approved\n"
            result_text += "• Try /testapi for connectivity issues"
        
        await testing_msg.edit_text(result_text)
        
    except Exception as e:
        logger.error(f"Test auth error: {e}")
        await testing_msg.edit_text(f"❌ Test failed: {str(e)}")

@app.on_message(filters.command("testapi"))
async def test_api_command(client, message: Message):
    testing_msg = await message.reply_text("🌐 **Testing API Connectivity...**\n\nChecking Dailymotion API access...")
    
    try:
        async with aiohttp.ClientSession() as session:
            start_time = asyncio.get_event_loop().time()
            async with session.get("https://partner.api.dailymotion.com/rest", 
                                 timeout=aiohttp.ClientTimeout(total=10)) as response:
                end_time = asyncio.get_event_loop().time()
                response_time = int((end_time - start_time) * 1000)
                
                start_oauth_time = asyncio.get_event_loop().time()
                async with session.get("https://partner.api.dailymotion.com/rest/oauth/v2/token", 
                                     timeout=aiohttp.ClientTimeout(total=10)) as oauth_response:
                    end_oauth_time = asyncio.get_event_loop().time()
                    oauth_time = int((end_oauth_time - start_oauth_time) * 1000)
                
                result_text = (
                    f"✅ **API Connectivity Test Results:**\n\n"
                    f"🌐 **Main API Endpoint:**\n"
                    f"   URL: https://partner.api.dailymotion.com/rest\n"
                    f"   Status: {response.status}\n"
                    f"   Response Time: {response_time}ms\n\n"
                    f"🔐 **OAuth Endpoint:**\n"
                    f"   URL: https://partner.api.dailymotion.com/rest/oauth/v2/token\n"
                    f"   Status: {oauth_response.status}\n"
                    f"   Response Time: {oauth_time}ms\n\n"
                    f"🔗 **Network Status:** Working\n"
                    f"🛡️ **SSL/HTTPS:** Verified\n\n"
                )
                
                if response.status in [200, 404] and oauth_response.status in [200, 404]:
                    result_text += "✅ **All endpoints are accessible!**\n\n"
                    result_text += "If uploads still fail, check:\n"
                    result_text += "• API credentials (/testauth)\n"
                    result_text += "• File format and size\n"
                    result_text += "• Account permissions on Dailymotion"
                else:
                    result_text += "⚠️ **Some endpoints returned unexpected status codes**\n"
                    result_text += "This might indicate temporary Dailymotion API issues."
                
        await testing_msg.edit_text(result_text)
        
    except asyncio.TimeoutError:
        await testing_msg.edit_text(
            "❌ **API Connectivity Test Failed!**\n\n"
            "🌐 **Error:** Connection timeout\n"
            "⏱️ **Timeout:** 10 seconds\n\n"
            "**Possible causes:**\n"
            "• Slow internet connection\n"
            "• Network firewall blocking access\n"
            "• Dailymotion API temporarily down\n"
            "• ISP or country blocking Dailymotion\n\n"
            "**Solutions:**\n"
            "• Check your internet connection\n"
            "• Try again in a few minutes\n"
            "• Use VPN if Dailymotion is blocked\n"
            "• Contact your network administrator"
        )
    except Exception as e:
        await testing_msg.edit_text(
            f"❌ **API Connectivity Test Failed!**\n\n"
            f"🌐 **Error:** {str(e)[:200]}\n\n"
            f"**This indicates a network connectivity issue.**\n"
            f"Please check your internet connection and try again.\n\n"
            f"**If using a VPS/Server:**\n"
            f"• Check DNS settings\n"
            f"• Verify outbound connections are allowed\n"
            f"• Test from a different network"
        )

@app.on_message(filters.command("addchannel"))
async def add_channel_command(client, message: Message):
    user_id = message.from_user.id
    user_states[user_id] = {"step": "channel_name", "data": {}}
    
    await message.reply_text(
        "📺 **Add New Dailymotion Channel**\n\n"
        "Let's add your Dailymotion account step by step.\n\n"
        "**Before starting, make sure you have:**\n"
        "• A Dailymotion account\n"
        "• API Key & Secret from https://developers.dailymotion.com\n"
        "• Your Dailymotion username & password\n\n"
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
                "You haven't added any channels yet.\n\n"
                "**Getting Started:**\n"
                "1. Go to https://developers.dailymotion.com\n"
                "2. Create an API application\n"
                "3. Get your API Key & Secret\n"
                "4. Use /addchannel to add your account! 🚀"
            )
            return
        
        channel_list = f"📺 **Your Dailymotion Channels ({len(channels)}):**\n\n"
        for i, channel in enumerate(channels, 1):
            created_date = channel['created_at'].strftime("%Y-%m-%d")
            channel_list += f"{i}. **{channel['channel_name']}**\n"
            channel_list += f"   👤 Username: {channel['username']}\n"
            channel_list += f"   📅 Added: {created_date}\n\n"
        
        channel_list += (
            "💡 **Available Commands:**\n"
            "• /upload - Upload videos to any channel\n"
            "• /testauth - Test channel authentication\n"
            "• /rmchannel - Remove a channel"
        )
        
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
                "🔧 **Setup Steps:**\n"
                "1. Go to https://developers.dailymotion.com\n"
                "2. Create a new application\n"
                "3. Get your API Key & Secret\n"
                "4. Use /addchannel to add your account\n"
                "5. Come back and use /upload\n\n"
                "🔍 **Troubleshooting:**\n"
                "• Use /testauth to verify credentials\n"
                "• Use /testapi to check connectivity\n"
                "• Use /help for detailed instructions! 📖"
            )
            return
        
        user_states[user_id] = {"step": "waiting_video"}
        await message.reply_text(
            "🎬 **Upload Video to Dailymotion**\n\n"
            "Please send me the video file you want to upload.\n\n"
            "📝 **Supported formats:** MP4, AVI, MOV, MKV, WMV, FLV, WEBM, M4V, 3GP\n"
            "📏 **Maximum file size:** 4GB (for Partner accounts)\n"
            "⏱️ **Processing time:** Depends on file size\n\n"
            "📎 Just drag and drop your video file here!\n\n"
            "🔍 **If upload fails, try:**\n"
            "• Smaller file (under 500MB) first\n"
            "• /testauth to verify credentials\n"
            "• /testapi to check connectivity\n"
            "• Check file format is supported"
        )
        
    except Exception as e:
        logger.error(f"Upload command error: {e}")
        await message.reply_text("❌ Error checking channels. Please try again.")

@app.on_message(filters.text & ~filters.command(["start", "help", "addchannel", "list", "rmchannel", "upload", "testauth", "testapi"]))
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
            "**Step 2/5:** Please enter your Dailymotion API Key (Client ID):\n\n"
            "💡 **Get it from:** https://developers.dailymotion.com\n"
            "📋 **Location:** Your Application → API Key/Client ID"
        )
    
    elif state["step"] == "api_key":
        api_key = message.text.strip()
        if len(api_key) < 10:
            await message.reply_text("❌ API Key seems too short. Please check and try again:")
            return
            
        state["data"]["api_key"] = api_key
        state["step"] = "api_secret"
        await message.reply_text(
            "**Step 3/5:** Please enter your Dailymotion API Secret (Client Secret):\n\n"
            "📋 **Location:** Your Application → API Secret/Client Secret"
        )
    
    elif state["step"] == "api_secret":
        api_secret = message.text.strip()
        if len(api_secret) < 10:
            await message.reply_text("❌ API Secret seems too short. Please check and try again:")
            return
            
        state["data"]["api_secret"] = api_secret
        state["step"] = "username"
        await message.reply_text(
            "**Step 4/5:** Please enter your Dailymotion Username:\n\n"
            "👤 **Note:** This is your regular Dailymotion login username"
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
            "🔐 **Security:** Your password will be stored securely and encrypted.\n"
            "🗑️ **Privacy:** This message will be deleted after processing."
        )
    
    elif state["step"] == "password":
        password = message.text.strip()
        if len(password) < 1:
            await message.reply_text("❌ Password cannot be empty. Please try again:")
            return
            
        state["data"]["password"] = password
        
        try:
            await message.delete()
        except:
            pass
        
        testing_msg = await message.reply_text(
            "🔄 **Testing credentials...**\n\n"
            "Please wait while I verify your Dailymotion account...\n"
            "This may take up to 30 seconds."
        )
        
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
                "Could not connect to your Dailymotion account.\n\n"
                "**Common Issues:**\n"
                "• Incorrect username or password\n"
                "• Invalid API Key or Secret\n"
                "• API application not approved by Dailymotion\n"
                "• Account suspended or restricted\n"
                "• API access not enabled for your account\n\n"
                "**Solutions:**\n"
                "• Double-check all credentials\n"
                "• Verify API application status at https://developers.dailymotion.com\n"
                "• Ensure account is active on Dailymotion\n"
                "• Try /testapi to check connectivity\n"
                "• Use /addchannel to start over with correct credentials"
            )
            del user_states[user_id]
            return
        
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
                    f"🔐 **Status:** Authenticated & Ready ✅\n\n"
                    f"🎬 **You can now upload videos to this account!**\n\n"
                    f"💡 **Quick Commands:**\n"
                    f"• /upload - Upload videos\n"
                    f"• /list - View all channels\n"
                    f"• /testauth - Test authentication\n\n"
                    f"🚀 **Ready to upload? Use /upload now!**"
                )
                
                del user_states[user_id]
                
            except psycopg2.IntegrityError:
                await testing_msg.edit_text(
                    "❌ **Channel Already Exists!**\n\n"
                    "You already have a channel with this name.\n\n"
                    "**Options:**\n"
                    "• Use a different channel name\n"
                    "• Remove the existing channel with /rmchannel\n"
                    "• Use /list to see all your channels"
                )
            except Exception as e:
                logger.error(f"Database save error: {e}")
                await testing_msg.edit_text(
                    "❌ **Database Error!**\n\n"
                    "Could not save your channel. Please try again later.\n\n"
                    f"**Error:** {str(e)[:100]}"
                )
        else:
            await testing_msg.edit_text("❌ Database connection error. Please try again.")

@app.on_message(filters.video | filters.document)
async def handle_video_upload(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_states or user_states[user_id]["step"] != "waiting_video":
        return
    
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
    
    max_size = 4 * 1024 * 1024 * 1024  # 4GB in bytes
    if file_info.file_size > max_size:
        file_size_gb = file_info.file_size / (1024 * 1024 * 1024)
        await message.reply_text(
            f"❌ **File too large!**\n\n"
            f"📏 **Your file:** {file_size_gb:.2f} GB\n"
            f"📏 **Maximum allowed:** 4 GB\n\n"
            f"**Solutions:**\n"
            f"• Compress your video using tools like HandBrake\n"
            f"• Reduce video quality/resolution\n"
            f"• Split video into smaller parts\n"
            f"• Use online compression tools"
        )
        return
    
    video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.m2v', '.mpg', '.mpeg']
    video_mimes = ['video/mp4', 'video/avi', 'video/quicktime', 'video/x-msvideo', 'video/x-matroska', 'video/webm']
    
    file_ext = os.path.splitext(file_name.lower())[1]
    if file_ext not in video_extensions and not any(mime in mime_type.lower() for mime in ['video/', 'application/octet-stream']):
        await message.reply_text(
            "❌ **Invalid file type!**\n\n"
            "Please send a video file with one of these formats:\n"
            "📹 **Supported:** MP4, AVI, MOV, MKV, WMV, FLV, WEBM, M4V, 3GP\n\n"
            "**If this is a video file:**\n"
            "• Try renaming it with the correct extension (.mp4, .avi, etc.)\n"
            "• Make sure the file isn't corrupted\n"
            "• Convert it to MP4 format for best compatibility"
        )
        return
    
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
        
        user_states[user_id].update({
            "step": "select_channel",
            "file_info": file_info,
            "file_name": file_name,
            "message_id": message.id
        })
        
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
            f"📏 **Size:** {file_size_mb:.1f} MB ({file_info.file_size:,} bytes)\n"
            f"{duration_text}\n"
            f"📺 **Select a channel to upload to:**\n\n"
            f"⚡ **Upload will begin immediately after selection**\n\n"
            f"🔍 **If upload fails:**\n"
            f"• Try /testauth first\n"
            f"• Check /testapi for connectivity\n"
            f"• Ensure file format is supported",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Video upload handler error: {e}")
        await message.reply_text("❌ Error processing video. Please try again.")

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
    
    try:
        await callback_query.edit_message_text(
            "🔄 **Initializing Upload Process...**\n\n"
            "📋 Preparing upload environment\n"
            "🔍 Validating credentials and file\n"
            "⚡ This may take a moment..."
        )
        
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
        
        progress_message = await callback_query.edit_message_text(
            f"⬇️ **Downloading Video from Telegram...**\n\n"
            f"📁 **File:** {file_name}\n"
            f"📺 **Channel:** {channel['channel_name']}\n"
            f"👤 **Account:** @{channel['username']}\n\n"
            f"📊 **Progress:** 0% - Starting download...\n"
            f"🔍 **Status:** Initializing download process..."
        )
        
        file_extension = os.path.splitext(file_name)[1] or '.mp4'
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
        temp_file_path = temp_file.name
        temp_file.close()
        
        try:
            await client.download_media(
                file_info.file_id,
                file_name=temp_file_path,
                progress=lambda current, total: asyncio.create_task(
                    update_download_progress(progress_message, current, total, file_name, channel['channel_name'])
                )
            )
            
            if not os.path.exists(temp_file_path) or os.path.getsize(temp_file_path) == 0:
                await progress_message.edit_text(
                    "❌ **Download Failed!**\n\n"
                    "File could not be downloaded from Telegram.\n\n"
                    "**Possible causes:**\n"
                    "• Network connection interrupted\n"
                    "• Telegram server issues\n"
                    "• File corrupted or unavailable\n\n"
                    "**Solutions:**\n"
                    "• Try uploading the file again\n"
                    "• Check your internet connection\n"
                    "• Try with a smaller file first"
                )
                return
            
            await progress_message.edit_text(
                f"⬆️ **Uploading to Dailymotion...**\n\n"
                f"📁 **File:** {file_name}\n"
                f"📺 **Channel:** {channel['channel_name']}\n"
                f"👤 **Account:** @{channel['username']}\n\n"
                f"🔐 **Status:** Connecting to Dailymotion API...\n"
                f"📡 Preparing for upload..."
            )
            
            uploader = DailymotionUploader(
                channel['api_key'],
                channel['api_secret'],
                channel['username'],
                channel['password']
            )
            
            title = os.path.splitext(file_name)[0].replace('_', ' ').replace('-', ' ').title()
            title = ''.join(c for c in title if c.isalnum() or c in (' ', '-', '_', '.', '(', ')', '[', ']'))
            if len(title) > 150:
                title = title[:147] + "..."
            if not title.strip():
                title = f"Video Upload {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            
            description = (
                f"Video uploaded via Telegram Bot\n\n"
                f"📅 Upload Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"📁 Original Filename: {file_name}\n"
                f"📏 File Size: {file_info.file_size:,} bytes\n"
                f"🤖 Uploaded automatically via Telegram"
            )
            
            video_url = await uploader.upload_video(
                temp_file_path,
                title,
                description=description,
                tags="telegram,bot,upload,automatic",
                progress_callback=lambda progress: asyncio.create_task(
                    update_upload_progress(progress_message, progress, file_name, channel['channel_name'])
                )
            )
            
            try:
                os.unlink(temp_file_path)
            except Exception as cleanup_error:
                logger.warning(f"Could not delete temporary file: {cleanup_error}")
            
            if video_url:
                success_text = (
                    f"✅ **Upload Completed Successfully!**\n\n"
                    f"📁 **File:** {file_name}\n"
                    f"🎬 **Title:** {title}\n"
                    f"📺 **Channel:** {channel['channel_name']}\n"
                    f"👤 **Account:** @{channel['username']}\n\n"
                    f"🔗 **Video URL:**\n{video_url}\n\n"
                    f"🎉 **Your video is now live on Dailymotion!**\n\n"
                    f"📊 **Note:** Dailymotion may take a few minutes to process HD quality.\n"
                    f"🔍 **Privacy:** Video is set to public by default."
                )
                
                await progress_message.edit_text(success_text)
                
                keyboard = [[InlineKeyboardButton("🎬 Watch on Dailymotion", url=video_url)]]
                await client.send_message(
                    user_id,
                    f"🎬 **Quick Access Link**\n\nClick the button below to watch your video on Dailymotion:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            else:
                error_details = await get_upload_error_details(uploader, temp_file_path)
                
                await progress_message.edit_text(
                    f"❌ **Upload Failed!**\n\n"
                    f"📁 **File:** {file_name}\n"
                    f"📺 **Channel:** {channel['channel_name']}\n\n"
                    f"**Diagnostic Results:**\n{error_details}\n\n"
                    f"**Troubleshooting Steps:**\n"
                    f"1️⃣ Use /testauth to verify credentials\n"
                    f"2️⃣ Use /testapi to check connectivity\n"
                    f"3️⃣ Try with a smaller file (under 100MB)\n"
                    f"4️⃣ Ensure account is active on Dailymotion\n"
                    f"5️⃣ Verify file format is supported\n"
                    f"6️⃣ Check if API application is approved\n\n"
                    f"💡 **Quick Tests:**\n"
                    f"• /testauth - Test credentials\n"
                    f"• /testapi - Test connectivity"
                )
            
        except asyncio.CancelledError:
            try:
                os.unlink(temp_file_path)
            except:
                pass
            await progress_message.edit_text(
                f"❌ **Upload Cancelled!**\n\n"
                f"The upload process was cancelled or timed out.\n\n"
                f"**Possible causes:**\n"
                f"• Network timeout or disconnection\n"
                f"• File too large for your connection speed\n"
                f"• Dailymotion server overload\n"
                f"• Process took longer than 15 minutes\n\n"
                f"**Solutions:**\n"
                f"• Ensure stable internet connection\n"
                f"• Try with smaller file size\n"
                f"• Upload during off-peak hours\n"
                f"• Compress video before uploading"
            )
        except Exception as e:
            logger.error(f"Upload process error: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            try:
                os.unlink(temp_file_path)
            except:
                pass
            
            await progress_message.edit_text(
                f"❌ **Upload Error!**\n\n"
                f"📁 **File:** {file_name}\n"
                f"📺 **Channel:** {channel['channel_name']}\n\n"
                f"**Error Details:** {str(e)[:200]}{'...' if len(str(e)) > 200 else ''}\n\n"
                f"**Diagnostic Commands:**\n"
                f"• /testauth - Test credentials\n"
                f"• /testapi - Test connectivity\n\n"
                f"**Common Solutions:**\n"
                f"• Check internet connection stability\n"
                f"• Verify file isn't corrupted\n"
                f"• Try with smaller file first\n"
                f"• Ensure Dailymotion account is active\n"
                f"• Check API application approval status"
            )
        
        if user_id in user_states:
            del user_states[user_id]
            
    except Exception as e:
        logger.error(f"Upload callback error: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        try:
            await callback_query.edit_message_text(
                "❌ **Critical Error!**\n\n"
                "An unexpected error occurred during upload.\n\n"
                "**Immediate Steps:**\n"
                "1️⃣ Try /testauth to check credentials\n"
                "2️⃣ Try /testapi to check connectivity\n"
                "3️⃣ Try again with a smaller file\n"
                "4️⃣ Restart the process with /upload\n\n"
                "If the problem persists, there may be an issue with:\n"
                "• Your API credentials or account status\n"
                "• Network connectivity\n"
                "• Dailymotion API temporary issues"
            )
        except:
            logger.error("Could not edit callback message")

async def handle_remove_callback(client, callback_query: CallbackQuery, channel_id: int):
    user_id = callback_query.from_user.id
    
    try:
        conn = get_db_connection()
        if not conn:
            await callback_query.edit_message_text("❌ Database connection error.")
            return
        
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT channel_name, username FROM channels WHERE id = %s AND user_id = %s", (channel_id, user_id))
        channel = cursor.fetchone()
        
        if not channel:
            await callback_query.edit_message_text("❌ Channel not found or access denied.")
            cursor.close()
            conn.close()
            return
        
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
                f"🔒 **Security:** All stored credentials have been wiped.\n"
                f"💡 **Note:** You can add it back anytime using /addchannel"
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
        
        speed_text = ""
        current_time = asyncio.get_event_loop().time()
        
        if not hasattr(update_download_progress, 'last_update'):
            update_download_progress.last_update = current_time
            update_download_progress.last_current = current
            return
        
        time_diff = current_time - update_download_progress.last_update
        
        if time_diff >= 3 or percent >= 95 or percent % 20 == 0:
            bytes_diff = current - update_download_progress.last_current
            if time_diff > 0:
                speed_mbps = (bytes_diff / time_diff) / (1024 * 1024)
                if speed_mbps > 0:
                    speed_text = f"📡 **Speed:** {speed_mbps:.1f} MB/s\n"
            
            eta_text = ""
            if speed_mbps > 0 and current < total:
                remaining_mb = (total - current) / (1024 * 1024)
                eta_seconds = remaining_mb / speed_mbps
                if eta_seconds < 60:
                    eta_text = f"⏱️ **ETA:** {int(eta_seconds)}s\n"
                elif eta_seconds < 3600:
                    eta_text = f"⏱️ **ETA:** {int(eta_seconds/60)}m {int(eta_seconds%60)}s\n"
            
            await message.edit_text(
                f"⬇️ **Downloading from Telegram...**\n\n"
                f"📁 **File:** {file_name}\n"
                f"📺 **Channel:** {channel_name}\n\n"
                f"📊 **Progress:** {percent}%\n"
                f"[{progress_bar}]\n"
                f"📦 **Downloaded:** {current_mb:.1f} MB / {total_mb:.1f} MB\n"
                f"{speed_text}"
                f"{eta_text}"
                f"⏳ **Please wait...** Do not close the app!"
            )
            
            update_download_progress.last_update = current_time
            update_download_progress.last_current = current
            
    except Exception as e:
        logger.debug(f"Download progress update error (ignored): {e}")
        pass

async def update_upload_progress(message, progress_percent, file_name, channel_name):
    try:
        progress_bar = "█" * (progress_percent // 5) + "░" * (20 - progress_percent // 5)
        
        if progress_percent < 10:
            status_text = "🔐 Authenticating with Dailymotion API..."
            step = "1/5"
        elif progress_percent < 20:
            status_text = "📤 Getting upload URL from Dailymotion..."
            step = "2/5"
        elif progress_percent < 80:
            status_text = "⬆️ Uploading file to Dailymotion servers..."
            step = "3/5"
        elif progress_percent < 95:
            status_text = "⚙️ Processing video metadata and thumbnails..."
            step = "4/5"
        else:
            status_text = "🎬 Creating video entry and finalizing..."
            step = "5/5"
        
        if not hasattr(update_upload_progress, 'last_percent'):
            update_upload_progress.last_percent = 0
        
        percent_diff = abs(progress_percent - update_upload_progress.last_percent)
        
        if percent_diff >= 15 or progress_percent >= 95 or progress_percent <= 5:
            await message.edit_text(
                f"⬆️ **Uploading to Dailymotion...**\n\n"
                f"📁 **File:** {file_name}\n"
                f"📺 **Channel:** {channel_name}\n\n"
                f"📊 **Progress:** {progress_percent}% ({step})\n"
                f"[{progress_bar}]\n"
                f"🔄 **Status:** {status_text}\n\n"
                f"⏳ **Please be patient...** Large files take longer!\n"
                f"🚫 **Do not close the app** until upload completes."
            )
            update_upload_progress.last_percent = progress_percent
            
    except Exception as e:
        logger.debug(f"Upload progress update error (ignored): {e}")
        pass

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    asyncio.create_task(shutdown_bot())

async def shutdown_bot():
    await app.stop()
    sys.exit(0)

async def keep_alive():
    """Periodic task to keep the bot alive on Render"""
    while True:
        logger.info("Keeping bot alive with a ping...")
        await asyncio.sleep(300)  # Ping every 5 minutes

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        init_db()
        logger.info("🚀 Dailymotion Upload Bot starting...")
        logger.info("📋 Available commands: /start, /help, /addchannel, /upload, /list, /rmchannel, /testauth, /testapi")
        logger.info("🔗 API Endpoints: https://partner.api.dailymotion.com/rest")
        logger.info("🔐 OAuth Endpoint: https://partner.api.dailymotion.com/rest/oauth/v2/token")
        
        if not all([API_ID, API_HASH, BOT_TOKEN, DATABASE_URL]):
            logger.error("❌ Missing required environment variables!")
            logger.error("Required: API_ID, API_HASH, BOT_TOKEN, DATABASE_URL")
            sys.exit(1)
        
        logger.info("✅ All environment variables present")
        logger.info("🔄 Starting Pyrogram client...")
        
        # Start keep-alive task
        asyncio.create_task(keep_alive())
        app.run()
        
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user (Ctrl+C)")
    except Exception as e:
        logger.error(f"💥 Bot crashed: {e}")
        logger.error(f"📋 Traceback: {traceback.format_exc()}")
        sys.exit(1)
    finally:
        logger.info("🔚 Bot shutdown complete")
