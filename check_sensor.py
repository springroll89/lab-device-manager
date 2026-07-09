import requests

r = requests.get('http://127.0.0.1:7800/api/status')
data = r.json()

for d in data['devices']:
    if d['name'] == 'sensor-1':
        print("sensor-1 data:")
        print(f"  id: {d['id']}")
        print(f"  name: {d['name']}")
        print(f"  alias: {d['alias']}")
        print(f"  type: {d['type']}")
        print(f"  latest: {d['latest']}")
        print(f"  latest keys: {list(d['latest'].keys())}")
        print(f"  latest['state']: {d['latest'].get('state')}")
        print(f"  latest['temp_c']: {d['latest'].get('temp_c')}")