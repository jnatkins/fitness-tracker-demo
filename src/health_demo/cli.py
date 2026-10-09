import argparse
import time
from .db import init
from .seed import seed
from .cdc import sync

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['init','seed','sync','serve']);args=p.parse_args()
    if args.command=='init': init()
    elif args.command=='seed': init();seed()
    elif args.command=='sync': print(sync())
    else:
        import os
        os.environ.setdefault("HEALTH_AI_EMBED","1")
        import uvicorn
        uvicorn.run('health_demo.app:app',host='127.0.0.1',port=8088)
