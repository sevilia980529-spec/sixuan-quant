#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把自包含单文件发布到 CodeSandbox（匿名 static sandbox），产出持久公网链接。"""
import json, sys, urllib.request, urllib.error

SRC = '/workspace/思旋_量化站_v51.html'

html = open(SRC, encoding='utf-8').read()
print('源文件: %s (%d 字节)' % (SRC, len(html)))

payload = {
    "files": {
        "index.html": {"content": html},
        "sandbox.config.json": {"content": json.dumps({"template": "static"})},
        "package.json": {"content": json.dumps({
            "name": "sixuan-v51-quant",
            "version": "1.0.0",
            "description": "思旋 · A股 v5.1 量化站（选股 / 持仓 / 战绩）",
            "private": True
        }, ensure_ascii=False, indent=2)}
    }
}

body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
req = urllib.request.Request(
    'https://codesandbox.io/api/v1/sandboxes/define?json=1',
    data=body,
    headers={'Content-Type': 'application/json', 'Accept': 'application/json'}
)

try:
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print('HTTP 错误:', e.code, e.read()[:500].decode('utf-8', 'ignore'))
    sys.exit(1)
except Exception as e:
    print('请求失败:', e)
    sys.exit(1)

sid = res.get('sandbox_id')
print('sandbox_id:', sid)
if not sid:
    print('返回:', res)
    sys.exit(1)

for host in ('csb.app', 'codesandbox.io'):
    url = 'https://%s.%s/' % (sid, host)
    print('URL:', url)
print('SID=%s' % sid)
