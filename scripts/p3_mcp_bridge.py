"""Generic STDIO MCP client bridge for the existing Codex session. No Agent logic."""
import asyncio
import argparse
import json
import os
from pathlib import Path
import sys
from datetime import datetime,timezone
import uuid
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from assistant_sensitive import SafeArgumentParser, SafeDiagnostics, private_process_diagnostics
from p3_mcp_tools import P3Tools, P3ToolError
OUT=ROOT/'evidence/p3-mcp'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])


def console_readline(getch=None):
    """Read raw Windows console characters; canonical PTY lines can truncate JSON."""
    if getch is None:
        import msvcrt
        getch=msvcrt.getwch
    chars=[]
    while True:
        char=getch()
        if char in ('\x00','\xe0'):getch();continue
        if char=='\x03':raise KeyboardInterrupt
        if char=='\x1a':return ''.join(chars)
        if char in ('\r','\n'):return ''.join(chars)+'\n'
        if char=='\b':
            if chars:chars.pop()
        else:chars.append(char)
        if len(chars)>1024*1024:raise ValueError('MCP_CONSOLE_REQUEST_TOO_LARGE')

async def main():
    global OUT
    parser=SafeArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--professional-only',action='store_true',help='Expose only task-bound professional tools; preserve complete envelopes.')
    parser.add_argument('--access-file',type=Path,help='Only this Agent connection credential is forwarded to the MCP process.')
    args=parser.parse_args();OUT=args.output.resolve()
    # Only the trusted host starts this process. The Agent receives an already
    # connected MCP/stdio handle, never the access-file or its bearer contents.
    host=P3Tools(args.base_url,access_file=args.access_file)
    boundary=host.output
    OUT.mkdir(parents=True,exist_ok=False)
    server_args=['-B','-m','p3_mcp_tools','--base-url',args.base_url]
    if args.professional_only:server_args.append('--professional-only')
    if args.access_file:server_args.extend(['--access-file',str(args.access_file.resolve())])
    params=StdioServerParameters(command=sys.executable,args=server_args+['--audit',str(OUT/'mcp-audit.jsonl')],
        cwd=str(ROOT),env={'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1','SYSTEMROOT':os.environ.get('SYSTEMROOT','C:\\Windows')})
    with (OUT/'stderr.txt').open('w',encoding='utf8') as err:
        diagnostics=SafeDiagnostics(err,boundary)
        async with stdio_client(params,errlog=diagnostics) as (read,write):
            async with ClientSession(read,write) as session:
                init=await session.initialize();tools=await session.list_tools()
                (OUT/'discovery.json').write_text(json.dumps({'initialize':init.model_dump(mode='json'),'tools':tools.model_dump(mode='json')},ensure_ascii=False,indent=2),encoding='utf8')
                print('MCP_READY '+json.dumps([t.name for t in tools.tools]),flush=True)
                while True:
                    line=await asyncio.to_thread(console_readline if os.name=='nt' and sys.stdin.isatty() else sys.stdin.readline)
                    if not line or line.strip()=='STOP':break
                    try:
                        request=json.loads(line)
                        if boundary.contains(request):
                            raise ValueError('P3_SENSITIVE_ARGUMENT_REJECTED')
                        if set(request)=={'get'}:
                            path=request['get']
                            if not isinstance(path,str) or not path.startswith('/v1/') or any(c in path for c in ('\\','\r','\n','#')):
                                raise ValueError('LOCAL_PUBLIC_READ_REQUIRED')
                            value=await asyncio.to_thread(host.request,'GET',path)
                            event=boundary.clean({'request':request,'is_error':False,'response':value})
                        else:
                            reply=await session.call_tool(request['name'],request.get('arguments',{}))
                            value=reply.structuredContent
                            if value is None:
                                try:value=json.loads(reply.content[0].text)
                                except (ValueError,AttributeError):value=reply.model_dump(mode='json')
                            if isinstance(value,dict) and not args.professional_only:value=value.get('result',value)
                            event=boundary.clean({'request':request,'is_error':reply.isError,'response':value})
                    except P3ToolError as error:
                        event=boundary.clean({'request':request,'is_error':True,
                            'response':{'status':'ERROR','code':str(error).split(':',1)[0]}})
                    except Exception:event={'error':'MCP_REQUEST_REJECTED_OR_FAILED',
                                            'recovery':'Inspect the discovered schema and original task state before retrying.'}
                    with (OUT/'client-calls.jsonl').open('a',encoding='utf8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
                    print(json.dumps(event,ensure_ascii=False),flush=True)
        diagnostics.close()
    print('MCP_CLOSED',flush=True)

if __name__=='__main__':
    try:
        with private_process_diagnostics():asyncio.run(main())
    except (KeyboardInterrupt,asyncio.CancelledError):print('MCP_CANCELLED',flush=True)
    except Exception:print('MCP_CONNECTION_FAILED',flush=True);sys.exit(1)
