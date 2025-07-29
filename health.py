import asyncio
from aiohttp import web
import logging

logger = logging.getLogger(__name__)

async def health_check(request):
    """Health check endpoint for Render"""
    return web.json_response({"status": "healthy", "service": "dailymotion-telegram-bot"})

async def start_health_server():
    """Start health check server"""
    try:
        app = web.Application()
        app.router.add_get('/health', health_check)
        app.router.add_get('/', health_check)
        
        port = int(os.getenv('PORT', 8000))
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        logger.info(f"Health server started on port {port}")
        return runner
    except Exception as e:
        logger.error(f"Health server error: {e}")
        return None
