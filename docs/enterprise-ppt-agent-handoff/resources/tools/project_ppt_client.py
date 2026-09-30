#!/usr/bin/env python3
"""Thin client for the existing project PPT service; no model keys required."""
import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


def request(server, path, body=None, timeout=60):
    headers = {'Accept': 'application/json'}
    if os.getenv('PPT_SERVICE_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['PPT_SERVICE_TOKEN']
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(server.rstrip('/') + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'PPT service HTTP {exc.code}; inspect the service logs for details.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='http://localhost:18080')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('capabilities')
    analysis = commands.add_parser('analyze-page')
    analysis.add_argument('template_id')
    analysis.add_argument('--snapshot', required=True, type=Path,
                          help='Frontend-produced page snapshot JSON; this client never parses PPTX or applies labels.')
    for name in ('status', 'draft', 'resume-optimization'):
        commands.add_parser(name).add_argument('job_id')
    imp = commands.add_parser('import-manuscript')
    imp.add_argument('file', type=Path); imp.add_argument('--title', required=True)
    gen = commands.add_parser('generate')
    gen.add_argument('run_id'); gen.add_argument('--template-id', required=True)
    gen.add_argument('--revision', type=int, required=True)
    opt = commands.add_parser('optimize')
    opt.add_argument('job_id'); opt.add_argument('--feedback', type=Path)
    args = parser.parse_args()
    body = None
    if args.command == 'capabilities':
        path = '/api/presentation-capabilities'
    elif args.command == 'analyze-page':
        path = f'/api/enterprise-templates/{args.template_id}/analyze-page'
        body = json.loads(args.snapshot.read_text())
    elif args.command == 'import-manuscript':
        path = '/api/runs/import'
        body = {'title': args.title, 'manuscript': args.file.read_text(), 'filename': args.file.name}
    elif args.command == 'generate':
        path = f'/api/runs/{args.run_id}/presentation'
        body = {'template_id': args.template_id, 'template_revision': args.revision}
    elif args.command == 'draft':
        path = f'/api/presentation-drafts/{args.job_id}'
    else:
        path = f'/api/presentations/{args.job_id}'
        if args.command == 'optimize':
            path += '/optimize'
            body = json.loads(args.feedback.read_text()) if args.feedback else {'feedback': []}
        elif args.command == 'resume-optimization':
            path += '/resume-optimization'; body = {}
    print(json.dumps(request(args.server, path, body,
                             timeout=300 if args.command == 'analyze-page' else 60),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
