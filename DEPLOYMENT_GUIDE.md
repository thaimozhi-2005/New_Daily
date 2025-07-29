# 🚀 Dailymotion Telegram Bot - Deployment Guide

## 📋 Prerequisites

### 1. Telegram Bot Setup
- Create a bot using [@BotFather](https://t.me/BotFather)
- Get your `BOT_TOKEN`
- Get your `API_ID` and `API_HASH` from [my.telegram.org](https://my.telegram.org)

### 2. Dailymotion API Setup
- Visit [Dailymotion Developer Portal](https://api.dailymotion.com)
- Create a developer account
- Register your application
- Note down API Key and Secret

### 3. Render Account
- Sign up at [Render.com](https://render.com)
- Connect your GitHub account

## 🔧 Deployment Steps

### Step 1: Prepare Repository
1. Create a new GitHub repository
2. Upload all the provided files:
   - `main.py` (use the updated version)
   - `health.py`
   - `requirements.txt`
   - `Dockerfile`
   - `render.yaml`
   - `.env.example`

### Step 2: Database Setup on Render
1. Go to Render Dashboard
2. Click "New" → "PostgreSQL"
3. Configure database:
   - **Name**: `dailymotion-bot-db`
   - **Database Name**: `dailymotion_bot`
   - **User**: `bot_user`
   - **Region**: Choose closest to your users
   - **Plan**: Free or Starter
4. Click "Create Database"
5. Note down the connection details

### Step 3: Web Service Setup
1. Go to Render Dashboard
2. Click "New" → "Web Service"
3. Connect your GitHub repository
4. Configure service:
   - **Name**: `dailymotion-telegram-bot`
   - **Environment**: `Docker`
   - **Region**: Same as database
   - **Branch**: `main`
   - **Plan**: Starter ($7/month recommended)

### Step 4: Environment Variables
Set these environment variables in Render:

```bash
API_ID=your_telegram_api_id
API_HASH=your_telegram_api_hash
BOT_TOKEN=your_bot_token_from_botfather
DATABASE_URL=postgresql://bot_user:password@host:port/dailymotion_bot
```

**Get DATABASE_URL from:**
1. Go to your PostgreSQL database in Render
2. Click on "Connect"
3. Copy the "External Database URL"

### Step 5: Deploy
1. Click "Create Web Service"
2. Render will automatically:
   - Build the Docker image
   - Deploy the application
   - Start the health check server
3. Wait for deployment to complete (5-10 minutes)

## 🔍 Verification

### 1. Check Health Status
Visit: `https://your-app-name.onrender.com/health`

Should return:
```json
{
  "status": "healthy",
  "database": "connected",
  "service": "dailymotion-telegram-bot"
}
```

### 2. Test Bot
1. Find your bot on Telegram
2. Send `/start` command
3. Verify bot responds correctly

## 📊 Monitoring

### Logs
- View logs in Render Dashboard → Your Service → Logs
- Monitor for any errors or issues

### Database
- Monitor database connections
- Check for any query errors

### Performance
- Monitor response times
- Check memory and CPU usage

## 🔧 Troubleshooting

### Common Issues

#### 1. Bot Not Responding
**Possible Causes:**
- Incorrect BOT_TOKEN
- Network connectivity issues
- Service not running

**Solutions:**
```bash
# Check logs in Render Dashboard
# Verify environment variables
# Restart service if needed
```

#### 2. Database Connection Errors
**Possible Causes:**
- Incorrect DATABASE_URL
- Database service down
- Connection limit reached

**Solutions:**
```bash
# Verify DATABASE_URL format
# Check database status in Render
# Restart database service
```

#### 3. Upload Failures
**Possible Causes:**
- Invalid Dailymotion credentials
- Network timeouts
- File size too large

**Solutions:**
```bash
# Verify API credentials
# Check file size limits
# Monitor upload progress
```

### Service Recovery
The bot includes automatic recovery mechanisms:
- **Database reconnection** with exponential backoff
- **API retry logic** for failed requests
- **Error handling** for interrupted uploads
- **Session management** for user states

## 🔄 Updates and Maintenance

### Updating Code
1. Push changes to your GitHub repository
2. Render will automatically redeploy
3. Monitor logs during deployment

### Database Maintenance
- Render handles backups automatically
- Monitor storage usage
- Consider upgrading plan if needed

### Security
- Regularly rotate API keys
- Monitor access logs
- Keep dependencies updated

## 💰 Cost Estimation

### Render Pricing (Monthly)
- **Starter Plan**: $7/month
  - 512MB RAM
  - 0.5 CPU
  - Suitable for moderate usage
  
- **Standard Plan**: $25/month
  - 2GB RAM
  - 1 CPU
  - Better for high usage

### Database Pricing
- **Free Plan**: 
  - 1GB storage
  - 100 max connections
  - Good for testing

- **Starter Plan**: $7/month
  - 10GB storage
  - 100 max connections
  - Recommended for production

## 📈 Scaling

### Horizontal Scaling
- Render automatically handles load balancing
- Add more instances if needed

### Vertical Scaling
- Upgrade to higher plans for more resources
- Monitor performance metrics

### Database Scaling
- Upgrade database plan for more storage
- Consider read replicas for high read loads

## 🛡️ Security Best Practices

### Environment Variables
- Never commit secrets to repository
- Use Render's environment variable system
- Rotate credentials regularly

### Database Security
- Use strong passwords
- Enable SSL connections
- Regular backups

### Bot Security
- Validate user inputs
- Implement rate limiting
- Monitor for abuse

## 📞 Support

### Getting Help
1. **Check Logs**: Always check Render logs first
2. **Documentation**: Refer to Render and Telegram Bot API docs
3. **Community**: Join Telegram developer communities
4. **Issues**: Create GitHub issues for code problems

### Useful Links
- [Render Documentation](https://render.com/docs)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Dailymotion API](https://developer.dailymotion.com)
- [Pyrogram Documentation](https://docs.pyrogram.org)

---

## 🎉 Congratulations!

Your Dailymotion Upload Bot is now live and ready to help users upload videos to Dailymotion directly from Telegram!

**Next Steps:**
1. Share your bot with users
2. Monitor usage and performance
3. Gather feedback for improvements
4. Consider adding new features

**Happy Coding! 🚀**
