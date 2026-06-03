from flask import Flask, request, jsonify
import requests
import gspread
from google.oauth2.service_account import Credentials
import os
import json
import time

app = Flask(__name__)

VERIFY_TOKEN      = os.environ.get('VERIFY_TOKEN', 'mybot2024')
PAGE_ACCESS_TOKEN = os.environ.get('PAGE_ACCESS_TOKEN', '')
PAGE_ID           = os.environ.get('PAGE_ID', '')
SHEET_ID          = os.environ.get('SHEET_ID', '')
GOOGLE_CREDS_JSON = os.environ.get('GOOGLE_CREDS_JSON', '')

_cache = {'data': {}, 'last_updated': 0}
CACHE_DURATION = 300

def subscribe_to_page():
    """Auto-subscribe page to webhook on startup"""
    try:
        url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/subscribed_apps"
        payload = {
            'subscribed_fields': 'feed,messages',
            'access_token': PAGE_ACCESS_TOKEN
        }
        result = requests.post(url, data=payload).json()
        if result.get('success'):
            print(f"✅ Page {PAGE_ID} subscribed to webhook!")
        else:
            print(f"⚠️ Subscription result: {result}")
    except Exception as e:
        print(f"❌ Subscription error: {e}")

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
            post_id    = str(record.get('Post ID', '')).strip()
            reply      = str(record.get('Reply Text', '')).strip()
            dm_message = str(record.get('DM Message', '')).strip()
            if post_id and reply:
                mapping[post_id] = {'reply': reply, 'dm': dm_message}
        _cache = {'data': mapping, 'last_updated': now}
        print(f"✅ Sheet loaded — {len(mapping)} posts configured")
        return mapping
    except Exception as e:
        print(f"❌ Sheet error: {e}")
        return _cache.get('data', {})

def send_reply(comment_id, message):
    url     = f"https://graph.facebook.com/v19.0/{comment_id}/comments"
    payload = {'message': message, 'access_token': PAGE_ACCESS_TOKEN}
    result  = requests.post(url, data=payload).json()
    if 'id' in result:
        print(f"✅ Comment replied: {comment_id}")
    else:
        print(f"❌ Reply failed: {result}")
    return result

def send_dm(user_id, message):
    url     = f"https://graph.facebook.com/v19.0/me/messages"
    payload = {
        'recipient':      json.dumps({'id': user_id}),
        'message':        json.dumps({'text': message}),
        'messaging_type': 'RESPONSE',
        'access_token':   PAGE_ACCESS_TOKEN
    }
    result = requests.post(url, data=payload).json()
    if 'message_id' in result:
        print(f"✅ DM sent to {user_id}")
    else:
        print(f"⚠️ DM result: {result}")
    return result

@app.route('/')
def home():
    return '🤖 Facebook Auto-Reply Bot is Running 24/7!', 200

@app.route('/webhook', methods=['GET'])
def verify():
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print("✅ Webhook verified!")
        return challenge, 200
    return 'Forbidden', 403

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    if not data or data.get('object') != 'page':
        return jsonify({'status': 'ignored'}), 200

    for entry in data.get('entry', []):
        page_id = str(entry.get('id', ''))
        for change in entry.get('changes', []):
            if change.get('field') != 'feed':
                continue
            value = change.get('value', {})
            if value.get('item') != 'comment' or value.get('verb') != 'add':
                continue

            comment_id   = value.get('comment_id', '')
            post_id      = value.get('post_id', '')
            commenter_id = str(value.get('from', {}).get('id', ''))
            parent_id    = value.get('parent_id', '')

            if commenter_id == page_id:
                continue
            if parent_id and parent_id != post_id:
                continue

            print(f"📩 Comment on post: {post_id}")

            replies = get_replies_from_sheet()
            data_for_post = replies.get(post_id)
            if not data_for_post and '_' in post_id:
                data_for_post = replies.get(post_id.split('_')[-1])

            if data_for_post and comment_id:
                reply_text = data_for_post.get('reply', '')
                if reply_text:
                    send_reply(comment_id, reply_text)
                dm_text = data_for_post.get('dm', '')
                if dm_text and commenter_id:
                    time.sleep(1)
                    send_dm(commenter_id, dm_text)
            else:
                print(f"ℹ️ No reply configured for: {post_id}")

    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    subscribe_to_page()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
