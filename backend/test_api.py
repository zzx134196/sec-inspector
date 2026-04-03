import urllib.request
import json
import ssl

url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
headers = {
    "Authorization": "Bearer sk-958b34a3-8d8e-40b7-a7e5-d9b3aff70427",
    "Content-Type": "application/json"
}
data = {
    "model": "qwen3-8b",
    "messages": [
        {"role": "user", "content": "Hello"}
    ]
}
req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
try:
    response = urllib.request.urlopen(req, context=ssl._create_unverified_context())
    print(response.read().decode())
except urllib.error.HTTPError as e:
    print(f"HTTPError: {e.code}")
    print(e.read().decode())
