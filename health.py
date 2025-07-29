from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import json
import os
import psycopg2

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            try:
                # Check database connection
                conn = psycopg2.connect(os.getenv("DATABASE_URL"))
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.close()
                conn.close()
                
                # Return healthy status
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {
                    "status": "healthy",
                    "database": "connected",
                    "service": "dailymotion-telegram-bot"
                }
                self.wfile.write(json.dumps(response).encode())
                
            except Exception as e:
                # Return unhealthy status
                self.send_response(503)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {
                    "status": "unhealthy",
                    "error": str(e),
                    "service": "dailymotion-telegram-bot"
                }
                self.wfile.write(json.dumps(response).encode())
        else:
            # Default response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {
                "service": "dailymotion-telegram-bot",
                "status": "running"
            }
            self.wfile.write(json.dumps(response).encode())
    
    def log_message(self, format, *args):
        # Suppress default logging
        pass

def start_health_server():
    """Start health check server in a separate thread"""
    port = int(os.getenv('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    print(f"Health check server started on port {port}")
    return server
