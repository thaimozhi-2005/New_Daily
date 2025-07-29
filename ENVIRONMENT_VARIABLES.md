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
    print("✅ Bot token is
