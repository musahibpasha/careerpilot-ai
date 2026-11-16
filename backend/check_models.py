import httpx
from pathlib import Path
from dotenv import dotenv_values

env_path = Path(__file__).with_name('.env')
auth = dotenv_values(env_path).get('OPENROUTER_API_KEY', '')
models = [
    'liquid/lfm-3-1.7b:free',
    'recraft/recraft-v4.1-flash-v2:free',
    'openrouter/free',
]

for model in models:
    try:
        r = httpx.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {auth}', 'Content-Type': 'application/json'},
            json={
                'model': model,
                'messages': [{'role': 'user', 'content': 'reply with one word: ok'}],
                'max_tokens': 5,
            },
            timeout=60.0,
        )
        print('MODEL=', model)
        print('STATUS=', r.status_code)
        print((r.text[:400]).replace('\n', ' '))
        print('---')
    except Exception as e:
        print('MODEL=', model)
        print('ERROR=', type(e).__name__, e)
        print('---')
