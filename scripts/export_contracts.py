"""Regenerate versioned OpenAPI and Grok function definitions without secrets."""
import json
import tempfile
from pathlib import Path

from grok_codex.api import create_app
from grok_codex.config import Settings

ROOT=Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory() as tmp:
    app=create_app(Settings(state_dir=Path(tmp)/'state',grok_token='g'*40,human_token='h'*40))
    schema=app.openapi()
(ROOT/'docs/openapi.json').write_text(json.dumps(schema,indent=2)+'\n')
components=schema['components']['schemas']
def expand(obj):
    if isinstance(obj,list): return [expand(x) for x in obj]
    if isinstance(obj,dict):
        if '$ref' in obj: return expand(components[obj['$ref'].rsplit('/',1)[1]])
        return {k:expand(v) for k,v in obj.items()}
    return obj
tools=[]
for path, methods in schema['paths'].items():
    if not path.startswith('/tools/'): continue
    op=methods['post']
    params=op.get('requestBody',{}).get('content',{}).get('application/json',{}).get('schema',{'type':'object','properties':{},'additionalProperties':False})
    tools.append({'type':'function','function':{'name':op['operationId'],
        'description':op.get('description',op['summary'])+'. Adapter: POST '+path+' with bearer token; mutations require a stable Idempotency-Key.',
        'parameters':expand(params)}})
(ROOT/'examples/grok-tools.json').write_text(json.dumps(tools,indent=2)+'\n')
print(f'Exported OpenAPI and {len(tools)} Grok tools')
