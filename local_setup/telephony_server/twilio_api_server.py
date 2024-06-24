import os
import json
import requests
import uuid
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client
from dotenv import load_dotenv
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from vo_utils.database_utils import db
from datetime import datetime
from config import settings
from fastapi.middleware.cors import CORSMiddleware
app = FastAPI()
load_dotenv()
port = 8001

twilio_account_sid = os.getenv('TWILIO_ACCOUNT_SID')
twilio_auth_token = os.getenv('TWILIO_AUTH_TOKEN')
twilio_phone_number = os.getenv('TWILIO_PHONE_NUMBER')

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)
# Initialize Twilio client
twilio_client = Client(twilio_account_sid, twilio_auth_token)

from endpoints import agent_batch_calling
for endpoint in [agent_batch_calling]:
    app.include_router(endpoint.router)

    
def populate_ngrok_tunnels():
    response = requests.get("http://ngrok:4040/api/tunnels")  # ngrok interface
    app_callback_url, websocket_url = None, None

    if response.status_code == 200:
        data = response.json()

        for tunnel in data['tunnels']:
            if tunnel['name'] == 'twilio-app':
                app_callback_url = tunnel['public_url']
            elif tunnel['name'] == 'bolna-app':
                websocket_url = tunnel['public_url'].replace('https:', 'wss:')

        return app_callback_url, websocket_url
    else:
        print(f"Error: Unable to fetch data. Status code: {response.status_code}")


@app.post('/call')
async def make_call(request: Request):
    try:
        call_details = await request.json()
        agent_id = call_details.get('agent_id', None)
        from_number = call_details.get('from_number', twilio_phone_number)
        recipient_data = call_details.get('recipient_data', None)
        context_id =  str(uuid.uuid4())
        data_for_db ={
                    'context_id': context_id,
                    'created_at': datetime.now().isoformat(),
                    'recipient_data': recipient_data
                    }
    # redis_client.set(context_id, json.dumps(context_data))
        db[settings.CALL_CONTEXTS].insert_one(data_for_db)
        if not agent_id:
            raise HTTPException(status_code=404, detail="Agent not provided")
        
        if not call_details or "recipient_phone_number" not in call_details:
            raise HTTPException(status_code=404, detail="Recipient phone number not provided")

        app_callback_url, websocket_url = populate_ngrok_tunnels()

        print(f'app_callback_url: {app_callback_url}')
        print(f'websocket_url: {websocket_url}')

        call = twilio_client.calls.create(
            to=call_details.get('recipient_phone_number'),
            from_=from_number,
            url=f"{app_callback_url}/twilio_callback?ws_url={websocket_url}&agent_id={agent_id}&context_id={context_id}",
            method="POST",
            record=True
        )

        return PlainTextResponse("done", status_code=200)

    except Exception as e:
        print(f"Exception occurred in make_call: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.post('/twilio_callback')
async def twilio_callback(ws_url: str = Query(...), agent_id: str = Query(...), context_id: str = Query(...)):
    try:
        response = VoiceResponse()

        connect = Connect()
        websocket_twilio_route = f'{ws_url}/chat/v1/{agent_id}/{context_id}'
        connect.stream(url=websocket_twilio_route)
        print(f"websocket connection done to {websocket_twilio_route}")
        response.append(connect)

        return PlainTextResponse(str(response), status_code=200, media_type='text/xml')

    except Exception as e:
        print(f"Exception occurred in twilio_callback: {e}")
