# 🔐 Environment Variables Guide

## Required Environment Variables

### 1. Telegram Configuration

#### `API_ID`
- **Type**: Integer
- **Description**: Your Telegram API ID from my.telegram.org
- **Example**: `12345678`
- **How to get**:
  1. Visit [my.telegram.org](https://my.telegram.org)
  2. Log in with your phone number
  3. Go to "API development tools"
  4. Create an application
  5. Copy the "App api_id"

#### `API_HASH`
- **Type**: String
- **Description**: Your Telegram API hash from my.telegram.org
- **Example**: `abcdef1234567890abcdef1234567890`
- **How to get**: Same as API_ID, copy the "App api_hash"

#### `BOT_TOKEN`
- **Type**: String
- **Description**: Your bot token from BotFather
- **Example**: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`
- **How to get**:
  1. Message [@BotFather](https://t.me/BotFather) on Telegram
  2. Send `/newbot`
  3. Follow the instructions
  4. Copy the token provided

### 2. Database Configuration

#### `DATABASE_URL`
- **Type**: String (PostgreSQL Connection String)
- **Description**: PostgreSQL database connection URL
- **Format**: `postgresql://username:password@host:port/database_name`
- **Example**: `postgresql://bot_user:mypassword@dpg-xxxxx-a.oregon-postgres.render.com/dailymotion_bot`
- **How to get**:
  1. Create PostgreSQL database on Render
  2. Go to database dashboard
  3. Click "Connect"
  4. Copy "External Database URL"

## 🔧 Setting Environment Variables in Render

### Method 1: Through Dashboard
1. Go to your Render service dashboard
2. Click on "Environment" tab
3. Click "Add Environment Variable"
4. Enter key and value
5. Click "Save Changes"

### Method 2: Through render.yaml
```yaml
envVars:
  - key: API_ID
    value: your_api_id
  - key: API_HASH
    value: your_api_hash
  - key: BOT_TOKEN
    value: your_bot_token
  - key: DATABASE_URL
    fromDatabase:
      name: dailymotion-bot-db
      property: connectionString
```

## 🛡️ Security Best Practices

### Do's ✅
- Use Render's environment variable system
- Keep credentials in secure password managers
- Rotate tokens regularly
- Use strong database passwords
- Enable two-factor authentication where possible

### Don'ts ❌
- Never commit secrets to Git repository
- Don't share tokens in plain text
- Don't use weak passwords
- Don't reuse the same token across multiple bots
- Don't store credentials in code comments

## 🔍 Validation

### Check Your Variables
Before deployment, verify:

```bash
# API_ID should be numeric
echo $API_ID | grep -E '^[0-9]+$'

# API_HASH should be 32 characters
echo $API_HASH | grep -E '^[a-f0-9]{32}$'

# BOT_TOKEN should follow pattern
echo $BOT_TOKEN | grep -E '^[0-9]+:[A-Za-z0-9_-]+$'

# DATABASE_URL should be valid PostgreSQL URL
echo $DATABASE_URL | grep -E '^postgresql://'
```

## 🚨 Troubleshooting

### Common Issues

#### 1. Invalid API_ID or API_HASH
**Error**: `AuthKeyUnregistered` or `ApiIdInvalid`
**Solution**:
- Verify API_ID is correct number
- Check API_HASH is exact 32-character string
- Ensure they're from the same application

#### 2. Invalid BOT_TOKEN
**Error**: `Unauthorized` or `Bot token invalid`
**Solution**:
- Get fresh token from BotFather
- Ensure no extra spaces or characters
- Verify bot is not deleted

#### 3. Database Connection Failed
**Error**: `could not connect to server` or `database does not exist`
**Solution**:
- Check DATABASE_URL format
- Verify database is running
- Ensure firewall allows connections
- Check username/password

### Testing Environment Variables

#### Test Bot Token
```python
import requests

def test_bot_token(token):
    url = f"https://api.telegram.org/bot{token}/getMe"
    response = requests.get(url)
    return response.status_code == 200

# Usage
if test_bot_token(BOT_TOKEN):
    print("✅ Bot token is valid")
else:
    print("❌ Bot token is invalid")
```

#### Test Database Connection
```python
import psycopg2

def test_database_connection(database_url):
    try:
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"Database error: {e}")
        return False

# Usage
if test_database_connection(DATABASE_URL):
    print("✅ Database connection successful")
else:
    print("❌ Database connection failed")
```

## 📝 Environment Variable Template

Create a `.env` file for local development:

```bash
# Telegram Bot Configuration
API_ID=12345678
API_HASH=abcdef1234567890abcdef1234567890
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# Database Configuration
DATABASE_URL=postgresql://username:password@localhost:5432/dailymotion_bot

# Optional: Development Settings
DEBUG=false
LOG_LEVEL=INFO
```

## 🔄 Environment-Specific Configuration

### Development Environment
```bash
# Local PostgreSQL
DATABASE_URL=postgresql://postgres:password@localhost:5432/dailymotion_bot_dev

# Debug mode
DEBUG=true
LOG_LEVEL=DEBUG
```

### Production Environment (Render)
```bash
# Render PostgreSQL
DATABASE_URL=postgresql://bot_user:secure_password@dpg-xxxxx-a.oregon-postgres.render.com/dailymotion_bot

# Production settings
DEBUG=false
LOG_LEVEL=INFO
```

### Testing Environment
```bash
# Test database
DATABASE_URL=postgresql://test_user:test_password@localhost:5432/dailymotion_bot_test

# Test bot token (separate bot for testing)
BOT_TOKEN=1234567890:TEST_ABCdefGHIjklMNOpqrsTUVwxyz
```

## 🔐 Credential Management

### Rotating Credentials

#### Bot Token Rotation
1. Message BotFather: `/revoke`
2. Select your bot
3. Get new token
4. Update environment variable
5. Redeploy service

#### API Credentials Rotation
1. Visit [my.telegram.org](https://my.telegram.org)
2. Delete old application
3. Create new application
4. Update API_ID and API_HASH
5. Redeploy service

#### Database Password Rotation
1. Generate new strong password
2. Update database user password
3. Update DATABASE_URL
4. Redeploy service

### Password Generation
Use strong passwords for database:
```bash
# Generate secure password
openssl rand -base64 32

# Or use online generator
# https://passwordsgenerator.net/
```

## 📊 Monitoring Environment Variables

### Health Check Integration
The bot includes health checks that verify:
- Database connectivity
- Service availability
- Environment variable validity

### Logging
Environment variable issues are logged:
```python
import logging
import os

logger = logging.getLogger(__name__)

# Check required variables
required_vars = ['API_ID', 'API_HASH', 'BOT_TOKEN', 'DATABASE_URL']
missing_vars = [var for var in required_vars if not os.getenv(var)]

if missing_vars:
    logger.error(f"Missing environment variables: {missing_vars}")
    sys.exit(1)
else:
    logger.info("All required environment variables are set")
```

## 🚀 Deployment Checklist

Before deploying, verify:

- [ ] API_ID is set and numeric
- [ ] API_HASH is set and 32 characters
- [ ] BOT_TOKEN is set and valid format
- [ ] DATABASE_URL is set and accessible
- [ ] All variables are in Render dashboard
- [ ] No typos in variable names
- [ ] Values don't contain extra spaces
- [ ] Database is running and accessible
- [ ] Bot token is from correct bot

## 🆘 Emergency Recovery

### If Bot Stops Working

1. **Check Environment Variables**
   ```bash
   # In Render dashboard, verify all variables are set
   ```

2. **Test Individual Components**
   ```bash
   # Test bot token via API
   curl https://api.telegram.org/bot$BOT_TOKEN/getMe
   ```

3. **Check Logs**
   ```bash
   # View recent logs in Render dashboard
   # Look for authentication errors
   ```

4. **Reset Credentials**
   ```bash
   # Get new bot token from BotFather
   # Update environment variables
   # Redeploy service
   ```

### Backup Strategy
- Keep backup of all credentials in secure password manager
- Document which bot belongs to which environment
- Have emergency contact for Render and Telegram accounts

## 📞 Support Resources

### Documentation
- [Render Environment Variables](https://render.com/docs/environment-variables)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [PostgreSQL Connection Strings](https://www.postgresql.org/docs/current/libpq-connect.html)

### Tools
- [PostgreSQL Connection String Builder](https://www.allkeysgenerator.com/Random/PostgreSQL-Connection-String-Generator.aspx)
- [Password Generator](https://passwordsgenerator.net/)
- [Environment Variable Validator](https://www.jsonschemavalidator.net/)

---

## ✅ Final Verification

After setting all environment variables:

1. **Test locally** (if possible)
2. **Deploy to Render**
3. **Check health endpoint**: `/health`
4. **Test bot commands**: `/start`, `/help`
5. **Monitor logs** for any errors
6. **Test full upload flow** with small file

**Success Indicators:**
- ✅ Health check returns `{"status": "healthy"}`
- ✅ Bot responds to `/start` command
- ✅ Database queries work correctly
- ✅ No authentication errors in logs

**You're ready to go! 🎉**
