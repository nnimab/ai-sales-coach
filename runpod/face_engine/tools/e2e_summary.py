import sys, json
t = sys.stdin.read()
i = t.find('{\n')
if i < 0:
    print(t[-3000:]); sys.exit()
d = json.loads(t[i:])
for k in ('utterances', 'engine', 'subscriber', 'transport', 'engine_session_log'):
    print(k, json.dumps(d.get(k), ensure_ascii=False))
