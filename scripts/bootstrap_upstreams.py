"""Fetch exact upstream revisions without modifying existing working trees."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def bootstrap(destination, names=None):
    rows=json.loads((ROOT/'manifests/upstreams.lock.json').read_text())['upstreams']
    known={r['name'] for r in rows}
    if names and set(names)-known:
        raise ValueError('Unknown upstream: '+', '.join(set(names)-known))
    for row in rows:
        if names and row['name'] not in names:continue
        path=destination/row['name']
        if path.exists():
            revision=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
            dirty=subprocess.check_output(['git','-C',str(path),'status','--porcelain'],text=True)
            if revision!=row['revision'] or dirty:
                raise ValueError('Existing upstream differs from lock; preserve it and choose another --out: '+str(path))
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run(['git','init',str(path)],check=True)
            subprocess.run(['git','-C',str(path),'remote','add','origin',row['url']],check=True)
            subprocess.run(['git','-C',str(path),'fetch','--depth','1','origin',row['revision']],check=True)
            subprocess.run(['git','-C',str(path),'checkout','--detach','FETCH_HEAD'],check=True)
        print(row['name'],row['revision'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'vendor')
    parser.add_argument('--only',nargs='+')
    args=parser.parse_args()
    bootstrap(args.out,args.only)
