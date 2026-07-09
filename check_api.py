import requests

r = requests.get('http://127.0.0.1:7800/api/status')
data = r.json()
print(f"Total devices: {len(data['devices'])}")
for d in data['devices']:
    print(f"ID:{d['id']} Name:{d['name']} Alias:{d['alias']} Type:{d['type']}")