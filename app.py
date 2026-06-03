from flask import Flask, request, jsonify
import requests
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import time

app = Flask(__name__)

# ============================================================
#  CONFIGURATION — Set these in Render Environment Variables
# ============================================================
VERIFY_TOKEN       = os.environ.get('VERIFY_TOKEN', 'mybot2024')
PAGE_ACCESS_TOKEN  = os.environ.get('PAGE_ACCESS_TOKEN', '')
SHEET_ID           = os.environ.get('SHEET_ID', '')
GOOGLE_CREDS_JSON  = os.environ.get('GOOGLE_CREDS_JSON', '')

# Cache so we don't hit Google Sheets on every single comment
_cache = {'data': {}, 'last_updated': 0}
CACHE_DURATION = 300  # refresh every 5 minutes

# ============================================================
#  GOOGLE SHEETS — Load post replies
# ============================================================
def get_replies_from_sheet():
    global _cache
    now = time.time()

    if now - _cache['last_updated'] < CACHE_DURATION and _cache['data']:
        return _cache['data']

    try:
        scope = [
            'https://www.googleapis.com/auth/spreadsheets.readonly',
            'https://www.googleapis.com/auth/drive.readonly'
        ]
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1
        records = sheet.get_all_records()

        mapping = {}
        for record in records:
            post_id   = str(record.get('Post ID', '')).strip()
            reply     = str(record.get('Reply Text', '')).strip()
            if post_id and reply:
                mapping[post_id] = reply

        _cache = {'data': mapping, 'last_updated': now}
        print(f"✅ Sheet loaded — {len(mapping)} posts configured")
        return mapping

    except Exception as e:
        print(f"❌ Sheet error: {e}")
        return _cache.get('data', {})

# ============================================================
#  FACEBOOK — Send reply to a comment
# ============================================================
def send_reply(comment_id, message):
    url     = f"https://graph.facebook.com/v19.0/{comment_id}/comments"
    payload = {'message': message, 'access_token': PAGE_ACCESS_TOKEN}
    result  = requests.post(url, data=payload).json()

    if 'id' in result:
        print(f"✅ Replied to comment {comment_id}")
    else:
        print(f"❌ Reply failed: {result}")
    return result

# ============================================================
#  ROUTES
# ============================================================
@app.route('/')
def home():
    return '🤖 Facebook Auto-Reply Bot is Running 24/7!', 200


@app.route('/webhook', methods=['GET'])
def verify():
    """Facebook calls this once to confirm the webhook is real."""
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print("✅ Webhook verified by Facebook!")
        return challenge, 200

    print(f"❌ Bad verify token: {token}")
    return 'Forbidden', 403


@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """Every comment on your page arrives here."""
    data = request.json

    if not data or data.get('object') != 'page':
        return jsonify({'status': 'ignored'}), 200

    for entry in data.get('entry', []):
        page_id = str(entry.get('id', ''))

        for change in entry.get('changes', []):
            if change.get('field') != 'feed':
                continue

            value = change.get('value', {})

            # Only handle brand-new top-level comments
            if value.get('item') != 'comment' or value.get('verb') != 'add':
                continue

            comment_id   = value.get('comment_id', '')
            post_id      = value.get('post_id', '')   # "pageID_postID"
            commenter_id = str(value.get('from', {}).get('id', ''))
            parent_id    = value.get('parent_id', '')

            # Skip the page's own comments (avoid infinite loop)
            if commenter_id == page_id:
                continue

            # Skip replies-to-replies (only reply to top-level comments)
            if parent_id and parent_id != post_id:
                continue

            print(f"📩 Comment on post: {post_id}")

            replies = get_replies_from_sheet()

            # Try full post_id first, then short ID (after the underscore)
            reply_text = replies.get(post_id)
            if not reply_text and '_' in post_id:
                reply_text = replies.get(post_id.split('_')[-1])

            if reply_text and comment_id:
                send_reply(comment_id, reply_text)
            else:
                print(f"ℹ️  No reply set for post: {post_id}")

    return jsonify({'status': 'ok'}), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
