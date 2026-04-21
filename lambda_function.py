import json
import os
import hmac
import hashlib
import time
import urllib.request
import boto3

SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']
SLACK_SIGNING_SECRET = os.environ['SLACK_SIGNING_SECRET']
GITHUB_TOKEN = os.environ['GITHUB_TOKEN']
GITHUB_REPO = os.environ['GITHUB_REPO']  # e.g. "raghu/my-repo"

BEDROCK_AGENT_ID = 'RPWQD6AYTQ'
BEDROCK_ALIAS_ID = 'IFXCI3OHUH'

def verify_slack(headers, body):
    timestamp = headers.get('x-slack-request-timestamp', '')
    if abs(time.time() - int(timestamp)) > 60 * 5:
        return False
    sig_basestring = f"v0:{timestamp}:{body}"
    my_sig = 'v0=' + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(my_sig, headers.get('x-slack-signature', ''))

def send_slack_message(channel, text):
    data = json.dumps({"channel": channel, "text": text}).encode()
    req = urllib.request.Request(
        'https://slack.com/api/chat.postMessage',
        data=data,
        headers={
            'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
            'Content-Type': 'application/json'
        }
    )
    urllib.request.urlopen(req)

def trigger_github_deploy(user):
    data = json.dumps({
        "event_type": "slack-deploy",
        "client_payload": {"triggered_by": user}
    }).encode()
    req = urllib.request.Request(
        f'https://api.github.com/repos/{GITHUB_REPO}/dispatches',
        data=data,
        headers={
            'Authorization': f'Bearer {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        }
    )
    urllib.request.urlopen(req)

def ask_bedrock(text, session_id):
    client = boto3.client('bedrock-agent-runtime')
    response = client.invoke_agent(
        agentId=BEDROCK_AGENT_ID,
        agentAliasId=BEDROCK_ALIAS_ID,
        sessionId=session_id,
        inputText=text,
        endSession=False
    )
    result = ""
    for event in response.get('completion', []):
        if "chunk" in event and "bytes" in event["chunk"]:
            result += event['chunk']['bytes'].decode('utf8')
    return result

def lambda_handler(event, context):
    body_str = event.get('body', '')
    headers = event.get('headers', {})

    # Verify Slack request
    if not verify_slack(headers, body_str):
        return {'statusCode': 401, 'body': 'Unauthorized'}

    body = json.loads(body_str)

    # Slack URL verification challenge
    if body.get('type') == 'url_verification':
        return {
            'statusCode': 200,
            'body': json.dumps({'challenge': body['challenge']})
        }

    # Handle events
    event_data = body.get('event', {})
    text = event_data.get('text', '').strip()
    channel = event_data.get('channel')
    user = event_data.get('user')
    session_id = f"slack-{user}"

    # Ignore bot's own messages
    if event_data.get('bot_id'):
        return {'statusCode': 200, 'body': 'ok'}

    # /deploy command
    if '/deploy' in text.lower():
        send_slack_message(channel, "🚀 Starting deployment to AWS...")
        try:
            trigger_github_deploy(user)
            send_slack_message(channel, "⏳ Pipeline triggered! I'll notify you when done.")
        except Exception as e:
            send_slack_message(channel, f"❌ Deploy failed: {str(e)}")
        return {'statusCode': 200, 'body': 'ok'}

    # Regular chat → Bedrock Agent
    try:
        reply = ask_bedrock(text, session_id)
        send_slack_message(channel, reply)
    except Exception as e:
        send_slack_message(channel, f"❌ Error: {str(e)}")

    return {'statusCode': 200, 'body': 'ok'}
