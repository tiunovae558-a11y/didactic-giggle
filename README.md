services:
  - type: worker
    name: protivzla-ultimate
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: python bot.py
    envVars:
      - key: BOT_TOKEN
        sync: false
      - key: ADMIN_IDS
        sync: false
      - key: REQUIRED_CHAT
        sync: false
      - key: REQUIRED_CHAT_URL
        sync: false
