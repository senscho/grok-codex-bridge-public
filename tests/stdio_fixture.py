"""Real subprocess fixture speaking the verified JSONL envelope (no Codex needed)."""
import json
import sys

for line in sys.stdin:
    m=json.loads(line)
    if m.get('method')=='initialized':
        continue
    if m.get('method')=='initialize':
        print(json.dumps({'id':m['id'],'result':{'userAgent':'mock'}}),flush=True)
    elif m.get('method')=='fixture/error':
        print(json.dumps({'id':m['id'],'error':{'code':-32600,'message':'test error'}}),flush=True)
    elif m.get('method')=='fixture/event':
        print(json.dumps({'method':'turn/started','params':{'turn':{'id':'turn'}}}),flush=True)
        print(json.dumps({'id':m['id'],'result':{'ok':True}}),flush=True)
    elif m.get('method')=='fixture/exit':
        sys.exit(0)
    elif m.get('method')=='fixture/hang':
        pass
    else:
        print(json.dumps({'id':m['id'],'result':{}}),flush=True)
